"""Confidence labels for the variant calls behind rare and clinical findings.

A ClinVar pathogenic match or a rare gnomAD allele is only as good as the call
under it, and a 30x genome has stretches at 5x, collapsed duplications and
strand-biased artifacts. Each carried ALT allele in a findings VCF gets
INFO/WGSE_CONF (high, moderate or low) and INFO/WGSE_CONF_REASONS (reason codes)
from the evidence the caller wrote for the sample: read depth, genotype
quality, allele balance, mapping quality, strand bias and FILTER. Every rule
and threshold, with its rationale and sources, is in docs/variant-confidence.md.

Labels are conservative screens of technical support, never a clinical
classification: a "high" call is still a research finding until an accredited
laboratory confirms it.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import re
import signal
import subprocess
from collections import Counter, deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import IO, Any

from wgsextract_cli.core import clinical_evidence
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.execution_context import context_thread
from wgsextract_cli.core.imputed_vcf import IMPUTED_TAG
from wgsextract_cli.core.paths import PathInput
from wgsextract_cli.core.results import Scalar, current_result, warn
from wgsextract_cli.core.utils import WGSExtractError, popen
from wgsextract_cli.core.variant_confidence_header import detect_caller as detect_caller
from wgsextract_cli.core.variant_confidence_header import (
    mapping_quality_kind as mapping_quality_kind,
)
from wgsextract_cli.core.variant_files import ensure_vcf_indexed

from ._confidence_reporting import (
    DISCLAIMER as DISCLAIMER,
)
from ._confidence_reporting import (
    GUIDE as GUIDE,
)
from ._confidence_reporting import (
    MAX_MANIFEST_FINDINGS as MAX_MANIFEST_FINDINGS,
)
from ._confidence_reporting import (
    SCHEME as SCHEME,
)
from ._confidence_reporting import (
    TABLE_COLUMNS as TABLE_COLUMNS,
)
from ._confidence_reporting import (
    ConfidenceSummary as ConfidenceSummary,
)
from ._confidence_reporting import (
    Finding as Finding,
)
from ._confidence_reporting import (
    write_table as write_table,
)
from ._confidence_rules import (
    HET_HIGH_FRACTION as HET_HIGH_FRACTION,
)
from ._confidence_rules import (
    HET_LOW_FRACTION as HET_LOW_FRACTION,
)
from ._confidence_rules import (
    HIGH as HIGH,
)
from ._confidence_rules import (
    HOM_HIGH_FRACTION as HOM_HIGH_FRACTION,
)
from ._confidence_rules import (
    HOM_LOW_FRACTION as HOM_LOW_FRACTION,
)
from ._confidence_rules import (
    LABELS as LABELS,
)
from ._confidence_rules import (
    LOW as LOW,
)
from ._confidence_rules import (
    MAX_FS as MAX_FS,
)
from ._confidence_rules import (
    MAX_SOR as MAX_SOR,
)
from ._confidence_rules import (
    MIN_MAPPING_QUALITY as MIN_MAPPING_QUALITY,
)
from ._confidence_rules import (
    MIN_QD as MIN_QD,
)
from ._confidence_rules import (
    MODERATE as MODERATE,
)
from ._confidence_rules import (
    SPLIT_TAG as SPLIT_TAG,
)
from ._confidence_rules import (
    SYMBOLIC_REFERENCE_ALTS as SYMBOLIC_REFERENCE_ALTS,
)
from ._confidence_rules import (
    CallConfidence as CallConfidence,
)
from ._confidence_rules import (
    Thresholds as Thresholds,
)
from ._confidence_rules import (
    _confidence_policy,
    _number,
    _numbers,
)
from ._confidence_rules import (
    classify as classify,
)
from ._confidence_rules import (
    strand_odds_ratio as strand_odds_ratio,
)
from ._confidence_rules import (
    thresholds_from as thresholds_from,
)

CONF_TAG, REASONS_TAG = "WGSE_CONF", "WGSE_CONF_REASONS"
# Imputed rarer than this in the panel is never listed as a finding: imputation
# can't see a rare allele the reads don't show (#621).
RARE_IMPUTED_AF = 0.01

LOW_REASONS = {
    "unsupported_somatic": "Mutect2 somatic calls are not assessable by the "
    "germline confidence scheme",
    "filtered": "the caller's FILTER did not pass",
    "low_depth": "read depth below the minimum",
    "low_gq": "genotype quality below the minimum",
    "allele_imbalance": "alt read fraction far from the genotype's expectation",
    "low_mapq": "reads map ambiguously (low mean mapping quality)",
    "strand_bias": "alt reads come mostly from one strand",
    "low_qd": "variant quality is low for its depth (QD below 2)",
    "no_call_metrics": "no depth, genotype quality or allele counts to judge by",
    "no_call": "the sample has no called genotype",
    "reference_overlap": "conflicting records overlap the reference span",
    "reference_gap": "the whole reference span is not covered",
    "reference_metrics_unknown": "reference depth or genotype quality is unknown",
    "imputed": "the genotype was imputed from a reference panel (`vcf impute`), "
    "not read from the sample",
}
MODERATE_REASONS = {
    "moderate_depth": "read depth below the high-confidence level",
    "moderate_gq": "genotype quality below the high-confidence level",
    "skewed_allele_balance": "alt read fraction somewhat off the expectation",
    "depth_unknown": "the VCF has no read depth for the call",
    "gq_unknown": "the VCF has no genotype quality or likelihoods for the call "
    "(a split multiallelic record's likelihoods no longer cover it)",
    "allele_balance_unknown": "the VCF has no allele counts for the call "
    "(a split multiallelic record's site counts mix every ALT)",
    "sample_contaminated": "qc contamination found DNA from another person "
    "in the sample (FREEMIX at or above qc_max_contamination)",
}
REASONS = {**LOW_REASONS, **MODERATE_REASONS}


def _non_negative(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = -1.0
    if not value >= 0:
        raise argparse.ArgumentTypeError(f"must be a non-negative number: {text!r}")
    return value


DEPTH_HELP = (
    "Read depth below LOW labels a call low confidence, below HIGH "
    "moderate (default: config confidence_min_depth 10, "
    "confidence_high_depth 20; halved for haploid calls)"
)
GQ_HELP = (
    "Genotype quality below LOW labels a call low confidence, below "
    "HIGH moderate (default: config confidence_min_gq 20, "
    "confidence_high_gq 30)"
)


def rare_imputed(info: Mapping[str, str], alt: int) -> bool:
    """An imputed ALT whose reference-panel frequency is rare or unknown."""
    if IMPUTED_TAG not in info:
        return False
    frequencies = _numbers(info.get("RAF"))
    if len(frequencies) >= alt and (frequency := frequencies[alt - 1]) is not None:
        return frequency < RARE_IMPUTED_AF
    # Beagle's AF describes the target sample(s), not the reference panel.
    # Dedicated counts survive bcftools sample subsetting, unlike AC/AN.
    if "WGSE_PANEL_AC" in info or "WGSE_PANEL_AN" in info:
        counts = _numbers(info.get("WGSE_PANEL_AC"))
        total = _number(info.get("WGSE_PANEL_AN"))
        if total and total > 0 and len(counts) >= alt:
            count = counts[alt - 1]
            if count is not None:
                return count / total < RARE_IMPUTED_AF
        return True
    return True


def _header_lines(
    thresholds: Thresholds,
    caller: str,
    contamination: float | None,
    *,
    include_reference: bool,
) -> list[str]:
    t = thresholds
    freemix = "" if contamination is None else f",Contamination={contamination:g}"
    reference_note = (
        "; for a reference call, its confidence is repeated for each ALT"
        if include_reference
        else ""
    )
    return [
        f'##INFO=<ID={CONF_TAG},Number=A,Type=String,Description="wgsextract '
        f"confidence in the sample's call of this ALT: high, moderate or low "
        f'({GUIDE}); . when the sample does not carry it{reference_note}">',
        f'##INFO=<ID={REASONS_TAG},Number=A,Type=String,Description="Reason codes '
        f'behind {CONF_TAG}, joined by | ({GUIDE})">',
        f"##wgsextract_confidence=<Scheme={SCHEME},Caller={caller},"
        f"Policy={_confidence_policy(caller)},"
        f"MinDepth={t.min_depth:g},HighDepth={t.high_depth:g},"
        f"MinGQ={t.min_gq:g},HighGQ={t.high_gq:g}{freemix}>",
    ]


_OWN_HEADER = re.compile(
    rf"^##(INFO=<ID=({CONF_TAG}|{REASONS_TAG}),|wgsextract_confidence=)"
)


def _strip_own_info(info: str) -> str:
    kept = [
        item
        for item in info.split(";")
        if item.partition("=")[0] not in {CONF_TAG, REASONS_TAG}
    ]
    return ";".join(kept) or "."


def _parse_info(info: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if info == ".":
        return parsed
    for item in info.split(";"):
        key, _, value = item.partition("=")
        parsed[key] = value
    return parsed


def label_records(
    lines: Iterable[str],
    thresholds: Thresholds,
    *,
    summary_columns: Sequence[str] = (),
    region_names: Sequence[tuple[str, int, int, str]] = (),
    keep_findings: bool = True,
    suppress_rare_imputed: bool = True,
    contamination: float | None = None,
    include_reference: bool = False,
) -> Iterator[str | ConfidenceSummary]:
    """Label VCF text lines; yields output lines, then the summary last.

    ``contamination`` is the sample's FREEMIX when it is at or above
    ``qc_max_contamination``: every carried call is then at most moderate.

    ``summary_columns`` are INFO keys copied into each finding; region_names
    are (chrom, start0, end, name) intervals naming a finding's ``gene``.
    """
    from wgsextract_cli.core.reference_evidence import parse_provenance

    provenance = None
    header: list[str] = []
    summary: ConfidenceSummary | None = None
    mq_kind = "rms"
    for line in lines:
        line = line.rstrip("\r\n")
        if line.startswith("##"):
            provenance = parse_provenance(line) or provenance
            if not _OWN_HEADER.match(line):
                header.append(line)
            continue
        if line.startswith("#"):
            columns = line.split("\t")
            caller = detect_caller(header)
            mq_kind = mapping_quality_kind(header)
            summary = ConfidenceSummary(
                caller,
                thresholds,
                sample=columns[9] if len(columns) > 9 else None,
                contamination=contamination,
            )
            if caller == "mutect2" and not any(
                warning.code == "unsupported_somatic_confidence"
                for warning in current_result().warnings
            ):
                warn(
                    "unsupported_somatic_confidence",
                    "Mutect2 somatic calls cannot be assessed by the germline "
                    "confidence scheme; carried calls are labelled low with "
                    "unsupported_somatic, not germline allele-imbalance reasons. "
                    "This is not a CHIP or tumour diagnosis.",
                    caller=caller,
                    policy="unsupported_somatic",
                )
            if len(columns) > 10:
                warn(
                    "confidence_first_sample_only",
                    f"Confidence labels judge only the first sample "
                    f"({columns[9]}) of this {len(columns) - 9}-sample VCF.",
                    sample=columns[9],
                )
            yield from header
            yield from _header_lines(
                thresholds, caller, contamination, include_reference=include_reference
            )
            yield line
            continue
        if summary is None:
            raise WGSExtractError("VCF records before the #CHROM header line.")
        summary.records += 1
        fields = line.split("\t")
        alts = fields[4].split(",")
        info = _parse_info(fields[7])
        fmt: dict[str, str] = {}
        alleles: list[str] = []
        if len(fields) > 9:
            fmt = dict(zip(fields[8].split(":"), fields[9].split(":"), strict=False))
            alleles = re.split(r"[/|]", fmt.get("GT", "."))
        labels: list[str] = []
        reasons: list[str] = []
        reference_record = include_reference and (
            (
                all(a in {"0", "."} for a in alleles)
                and not any(
                    a.startswith("<") and a not in SYMBOLIC_REFERENCE_ALTS for a in alts
                )
            )
            or all(a in SYMBOLIC_REFERENCE_ALTS or a == "." for a in alts)
        )
        for index, alt_allele in enumerate(alts[:1] if reference_record else alts, 1):
            result = (
                None
                if alt_allele in SYMBOLIC_REFERENCE_ALTS and not reference_record
                else classify(
                    alt=index,
                    alleles=alleles,
                    ref=fields[3],
                    alt_allele=alt_allele,
                    filters=fields[6],
                    info=info,
                    fmt=fmt,
                    n_alts=len(alts),
                    thresholds=thresholds,
                    mq_kind=mq_kind,
                    caller=summary.caller,
                    samples=max(len(fields) - 9, 1),
                    contaminated=contamination is not None,
                    reference=reference_record,
                    ploidy=(
                        provenance.record_ploidy(
                            fields[0],
                            int(fields[1]),
                            int(
                                _number(info.get("END"))
                                or (int(fields[1]) + len(fields[3]) - 1)
                            ),
                        )
                        if provenance
                        else None
                    ),
                )
            )
            if result is not None and reference_record:
                support_reasons = info.get("PGX_REASONS", "").split("|")
                result.reasons.extend(
                    reason
                    for reason in support_reasons
                    if reason in REASONS and reason not in result.reasons
                )
                if any(reason in LOW_REASONS for reason in result.reasons):
                    result.label = LOW
                for reason in ("reference_overlap", "reference_gap"):
                    if reason in info:
                        result.label = LOW
                        result.reasons.append(reason)
            if result is None:
                labels.append(".")
                reasons.append(".")
                if alt_allele not in SYMBOLIC_REFERENCE_ALTS:
                    summary.not_carried += 1
                continue
            component_label = info.get(clinical_evidence.COMPONENT_CONF)
            if component_label in LABELS:
                if LABELS.index(component_label) > LABELS.index(result.label):
                    result.label = component_label
                result.reasons = sorted(
                    set(result.reasons)
                    | {
                        r
                        for r in info.get(
                            clinical_evidence.COMPONENT_REASONS, ""
                        ).split("|")
                        if r in REASONS
                    }
                )
            labels.append(result.label)
            reasons.append("|".join(result.reasons) or ".")
            if suppress_rare_imputed and rare_imputed(info, index):
                # Labelled in the VCF, but never counted or listed as a finding.
                summary.rare_imputed += 1
                continue
            summary.counts[result.label] += 1
            summary.reasons.update(result.reasons)
            if keep_findings:
                annotations = {key: info.get(key, "") for key in summary_columns}
                if region_names:
                    pos = int(fields[1])
                    annotations["gene"] = ",".join(
                        name
                        for chrom, start, end, name in region_names
                        if chrom == fields[0] and start < pos <= end
                    )
                summary.findings.append(
                    Finding(
                        fields[0],
                        int(fields[1]),
                        fields[3],
                        alt_allele,
                        fmt.get("GT", "."),
                        result,
                        annotations,
                        "" if fmt.get("PS", ".") == "." else fmt["PS"],
                    )
                )
        fields[7] = _strip_own_info(fields[7])
        if any(label != "." for label in labels):
            if reference_record:
                # Count one reference call, but emit both INFO tags per ALT.
                labels *= len(alts)
                reasons *= len(alts)
            tags = f"{CONF_TAG}={','.join(labels)};{REASONS_TAG}={','.join(reasons)}"
            fields[7] = tags if fields[7] == "." else f"{fields[7]};{tags}"
        yield "\t".join(fields)
    if summary is None:
        raise WGSExtractError("The VCF has no #CHROM header line.")
    yield summary


class _Stderr:
    """Drain a process's stderr in the background, so a chatty tool never
    blocks on a full pipe, keeping its tail for error messages."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.lines: deque[str] = deque(maxlen=20)
        self._thread = context_thread(
            target=self._drain, args=(process.stderr,), daemon=True
        )
        self._thread.start()

    def _drain(self, stream: IO[str] | None) -> None:
        if stream is not None:
            with stream:
                for line in stream:
                    self.lines.append(line.rstrip())

    def join(self) -> None:
        # A wrapper's descendants can keep the stderr pipe open after it exits.
        self._thread.join(timeout=5)

    def tail(self) -> str:
        self.join()
        return "\n".join(self.lines)


def _start(
    command: Sequence[str], **kwargs: Any
) -> tuple[subprocess.Popen[str], _Stderr]:
    process = popen(command, stderr=subprocess.PIPE, text=True, **kwargs)
    try:
        return process, _Stderr(process)
    except BaseException:
        with process:
            process.kill()
        raise


def label_vcf(
    source_vcf: PathInput,
    out_vcf: PathInput | None,
    thresholds: Thresholds,
    *,
    include: str | None = None,
    regions_file: PathInput | None = None,
    summary_columns: Sequence[str] = (),
    region_names: Sequence[tuple[str, int, int, str]] = (),
    keep_findings: bool = True,
    suppress_rare_imputed: bool = True,
    samples: Sequence[PathInput | None] = (),
    include_reference: bool = False,
) -> ConfidenceSummary:
    """Write source_vcf's records (those matching the bcftools ``include``
    expression, within ``regions_file``) with confidence labels to out_vcf,
    bgzipped and indexed; with out_vcf None, only summarise them. A sample
    that `qc contamination` found contaminated caps every label at moderate:
    its estimate is looked up for ``samples`` (the sample's own input files,
    as source_vcf may be an intermediate written elsewhere), then source_vcf
    and out_vcf."""
    from wgsextract_cli.core.contamination import contaminated, warn_if_contaminated

    freemix = None
    for file in (*samples, source_vcf, out_vcf):
        if file is not None and (freemix := contaminated(file)) is not None:
            warn_if_contaminated(file, "the calls labelled")
            break
    view = ["bcftools", "view"]
    if include:
        view += ["-i", include]
    if regions_file:
        view += ["-R", str(regions_file)]
    reader, reader_errors = _start([*view, str(source_vcf)], stdout=subprocess.PIPE)
    assert reader.stdout is not None
    labelled = label_records(
        reader.stdout,
        thresholds,
        summary_columns=summary_columns,
        region_names=region_names,
        keep_findings=keep_findings,
        suppress_rare_imputed=suppress_rare_imputed,
        contamination=freemix,
        include_reference=include_reference,
    )

    def reader_failed() -> bool:
        return reader.wait() != 0

    def reader_error() -> WGSExtractError:
        return _ReaderError(
            f"bcftools failed reading {source_vcf}: {reader_errors.tail()}"
        )

    try:
        if out_vcf is None:
            summary = _write(labelled, None)
            if reader_failed():
                raise reader_error()
        else:
            with atomic_output(out_vcf) as staged, open(staged, "wb") as sink:
                summary = _write_bgzipped(labelled, sink, out_vcf)
                if reader_failed():
                    raise reader_error()
    except BaseException as error:
        # Closing the pipe stops a reader still writing (SIGPIPE), so the wait
        # can't hang. A reader that failed by itself explains malformed or
        # missing records better than the parse error does.
        reader.stdout.close()
        status = reader.wait()
        if status not in (0, -_SIGPIPE) and not isinstance(
            error, (_OutputError, _ReaderError, KeyboardInterrupt)
        ):
            raise reader_error() from error
        raise
    finally:
        reader_errors.join()
        reader.stdout.close()
    if out_vcf is not None:
        ensure_vcf_indexed(out_vcf)
    return summary


_SIGPIPE = getattr(signal, "SIGPIPE", 13)


class _OutputError(WGSExtractError):
    """bgzip, not the reading bcftools, failed."""


class _ReaderError(WGSExtractError):
    """The reading bcftools failed."""


def _write_bgzipped(
    labelled: Iterable[str | ConfidenceSummary], sink: IO[bytes], out_vcf: PathInput
) -> ConfidenceSummary:
    writer, writer_errors = _start(["bgzip", "-c"], stdin=subprocess.PIPE, stdout=sink)
    assert writer.stdin is not None
    stopped = False
    try:
        summary = _write(labelled, writer.stdin)
    except OSError:
        stopped = True  # bgzip stopped reading: its status and stderr say why
    finally:
        with contextlib.suppress(OSError):
            writer.stdin.close()
        status = writer.wait()
        writer_errors.join()
        if status != 0 or stopped:
            raise _OutputError(
                f"bgzip failed writing {out_vcf}: {writer_errors.tail()}"
            )
    return summary


def _write(
    labelled: Iterable[str | ConfidenceSummary], sink: IO[str] | None
) -> ConfidenceSummary:
    for item in labelled:
        if isinstance(item, ConfidenceSummary):
            return item
        if sink is not None:
            sink.write(item + "\n")
    raise WGSExtractError("Labelling produced no summary.")


def _counts_text(summary: ConfidenceSummary) -> str:
    return ", ".join(f"{summary.counts[label]} {label}" for label in LABELS)


def record(
    summary: ConfidenceSummary,
    *,
    output: PathInput | None,
    role: str,
    what: str,
    table: PathInput | None = None,
    clinical: int = 0,
) -> None:
    """Log the counts and add them, warnings and details to the result manifest.

    ``what`` names the findings in messages ("ClinVar pathogenic calls");
    ``clinical`` counts the carried ClinVar pathogenic/likely pathogenic calls
    among them, which add a not-a-diagnosis warning.
    """
    result = current_result()
    metrics = summary.metrics()
    if output is not None:
        result.add_output(output, role, metrics=metrics)
    if table is not None:
        result.add_output(table, "confidence_table", kind="tsv")
    totals: dict[str, Scalar] = {}
    for key, value in metrics.items():
        previous = result.metrics.get(key)
        if isinstance(value, int) and isinstance(previous, int):
            value += previous
        totals[key] = value
    result.add_metrics(**totals)
    details = dict(result.details or {})
    previous_confidence = details.get("confidence", [])
    if not isinstance(previous_confidence, Iterable):
        raise TypeError("Result confidence details must be iterable.")
    details["confidence"] = [*previous_confidence, summary.details(
        None if output is None else str(output), role
    )]  # fmt: skip
    result.set_details(details)
    if summary.reference_evidence:
        _record_reference_evidence(summary.reference_evidence)

    if summary.rare_imputed:
        warn(
            "rare_imputed_not_listed",
            f"{summary.rare_imputed} imputed {what} with a panel frequency "
            f"under {RARE_IMPUTED_AF:.0%} are not listed: imputation cannot "
            "detect rare variants reliably. Sequence the sample to look for them.",
            count=summary.rare_imputed,
        )
    if not summary.carried:
        logging.info(f"No {what} are carried by the sample.")
        return
    logging.info(
        f"Confidence of {summary.carried} {what}: {_counts_text(summary)} "
        f"(caller: {summary.caller}; {GUIDE})."
    )
    where = f" (see {table})" if table else ""
    low = summary.counts[LOW]
    if low:
        top = ", ".join(
            code
            for code, _ in Counter(
                {k: v for k, v in summary.reasons.items() if k in LOW_REASONS}
            ).most_common(3)
        )
        warn(
            "low_confidence_findings",
            f"{low} of {summary.carried} {what} are low confidence ({top}){where}: "
            "the reads don't support them well enough to trust without "
            "confirmation.",
            count=low,
            total=summary.carried,
            output=None if output is None else str(output),
            reasons=top,
        )
    if clinical:
        warn(
            "unconfirmed_clinical_findings",
            f"ClinVar lists {clinical} of the sample's calls as pathogenic or "
            f"likely pathogenic{where}. {DISCLAIMER}",
            count=clinical,
            output=None if output is None else str(output),
        )


def is_pathogenic(clnsig: str) -> bool:
    """Whether a ClinVar CLNSIG value includes pathogenic or likely pathogenic."""
    return "Pathogenic" in clnsig or "Likely_pathogenic" in clnsig


def clinical_count(summary: ConfidenceSummary) -> int:
    return sum(is_pathogenic(f.annotations.get("CLNSIG", "")) for f in summary.findings)


CALL_SITES, CLINVAR_SITES = "calls", "clinvar_pathogenic_sites"
_CHECKED = {
    CALL_SITES: "labelled calls",
    CLINVAR_SITES: "ClinVar pathogenic/likely pathogenic sites",
}


def add_reference_evidence(
    summary: ConfidenceSummary,
    vcf: PathInput,
    work: PathInput,
    clinvar_sites: Iterable[tuple[str, int, int]] | None = None,
    reference: str | None = None,
) -> None:
    """Add companion gVCF states at calls/ClinVar spans, aligned to ``reference``."""
    from wgsextract_cli.core import reference_evidence as evidence

    gvcf = evidence.companion(vcf)
    if gvcf is None:
        return
    read = evidence.readable(gvcf, summary.sample, Path(work))
    if read is None:
        warn(
            "reference_evidence_other_sample",
            "The gVCF beside the input holds another sample; it was not used.",
            reference_evidence=gvcf,
        )
        return
    source = evidence.ReferenceEvidence(read)
    if reference:
        source = source.normalized(reference, Path(work))
    checked: dict[str, Iterable[tuple[str, int, int]]] = {
        CALL_SITES: [
            (f.chrom, f.pos, f.pos + max(len(f.ref), 1) - 1) for f in summary.findings
        ]
    }
    if clinvar_sites is not None:
        checked[CLINVAR_SITES] = clinvar_sites
    report: dict[str, Any] = {}
    for name, sites in checked.items():
        counts = Counter(
            state.value
            for _, state in evidence.site_states(source, sites, summary.thresholds)
        )
        report[name] = {"sites": sum(counts.values()), "states": dict(sorted(counts.items()))}  # fmt: skip
    current_result().add_input(gvcf, evidence.ROLE)
    summary.reference_evidence = {
        "file": gvcf,
        "engine": source.provenance.engine if source.provenance else None,
        "quality_model": source.quality_model,
        "checked": report,
    }


def _record_reference_evidence(report: Mapping[str, Any]) -> None:
    for name, checked in report["checked"].items():
        states = ", ".join(f"{n} {state}" for state, n in checked["states"].items())
        logging.info(
            f"Reference evidence at the {checked['sites']} {_CHECKED[name]}: "
            f"{states or 'none'} ({Path(report['file']).name})."
        )
    sites = report["checked"].get(CLINVAR_SITES)
    if not sites:
        return
    open_states = {
        state: n
        for state, n in sites["states"].items()
        if state not in {"callable_reference", "variant", "excluded_by_policy"}
    }
    if open_states:
        count = sum(open_states.values())
        warn(
            "reference_evidence_unassessed_sites",
            f"{count} of {sites['sites']} ClinVar pathogenic/likely pathogenic "
            "sites have neither a call nor confident reference in the gVCF ("
            + ", ".join(f"{n} {state}" for state, n in open_states.items())
            + "): the sample may carry a variant there that was not called.",
            count=count,
            total=sites["sites"],
            reference_evidence=str(report["file"]),
            **{f"sites_{state}": n for state, n in open_states.items()},
        )
