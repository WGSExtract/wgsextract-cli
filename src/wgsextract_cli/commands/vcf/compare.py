"""Compare exact, normalized small alleles locally across labelled samples."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.vcf_specs.compare import VcfCompareParams, labelled
from wgsextract_cli.core.atomic_output import publish_output_set
from wgsextract_cli.core.config import output_directory
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.results import (
    current_result,
    human_output,
    warn,
)
from wgsextract_cli.core.table_writer import (
    Column,
    TableWriter,
    check_format,
    table_path,
    write_table,
)
from wgsextract_cli.core.utils import UsageError, run_command
from wgsextract_cli.core.variant_confidence import SPLIT_TAG, thresholds_from

from .compare_inputs import (
    ALIGN_ORIGIN,
    PARTIAL,
    Dictionary,
    Input,
    prepare,
    reference_indexes,
    require_local,
    verify_build,
)
from .compare_records import Call, EvidenceCursor, Scope, resolve, sites
from .compare_summary import PAIR_COLUMNS, SUPPORT_COLUMNS, Summary, variant_type

_SCHEMA = "wgsextract.vcf_compare/1"
_STATE_TAG = "WGSE_COMPARE_STATE"
_DECISION_TAG = "WGSE_COMPARE_DECISION"
_KEY_COLUMNS = (
    Column("chrom", "TEXT"),
    Column("pos", "INTEGER"),
    Column("ref", "TEXT"),
    Column("alt", "TEXT"),
    Column("variant_type", "TEXT"),
    Column("support", "INTEGER"),
    Column("decision", "TEXT"),
)


@dataclass(frozen=True)
class Sources:
    calls: dict[str, Input]
    evidence: dict[str, Input]
    reference: Path | None
    bed: Path | None

    @classmethod
    def read(cls, params: VcfCompareParams) -> Sources:
        samples = labelled(params.sample, "sample")
        calls = {
            label: Input.read(require_local(path), samples.get(label), label)
            for label, path in labelled(params.callset, "callset").items()
        }
        evidence = {
            label: Input.read(
                require_local(path), calls[label].sample, f"{label} evidence"
            )
            for label, path in labelled(params.gvcf, "gvcf").items()
        }
        return cls(
            calls,
            evidence,
            require_local(params.ref) if params.ref else None,
            require_local(params.bed) if params.bed else None,
        )

    def record(self) -> None:
        result = current_result()
        for label, call in self.calls.items():
            result.add_input(call.path, f"callset:{label}")
        for label, evidence in self.evidence.items():
            result.add_input(evidence.path, f"gvcf:{label}")
        result.add_input(self.bed, "bed")
        if self.reference is not None:
            result.add_input(self.reference, "reference")
            for index in reference_indexes(self.reference):
                if index.is_file():
                    result.add_input(index, "reference_index")


@dataclass(frozen=True)
class Groups:
    include: tuple[int, ...]
    exclude: tuple[int, ...]
    strict: bool

    def decision(self, calls: list[Call]) -> str:
        if not self.include:
            return "shared" if all(call.carried for call in calls) else "discordant"
        if not any(calls[i].carried for i in self.include):
            return "outside_include"
        if any(calls[i].carried for i in self.exclude):
            return "excluded_carrier"
        if any(not calls[i].carried for i in self.include):
            return "inconclusive"
        allowed = {"callable_reference"} if self.strict else {"absent", "not_called"}
        return (
            "retained"
            if all(calls[i].state in allowed for i in self.exclude)
            else "inconclusive"
        )


def _header(stream: TextIO, destination: TextIO, labels: tuple[str, ...]) -> None:
    for line in stream:
        if line.startswith(f"##INFO=<ID={_STATE_TAG},") or line.startswith(
            f"##INFO=<ID={_DECISION_TAG},"
        ):
            raise UsageError(
                "The anchor already has comparison annotations; use its original callset."
            )
        if line.startswith("#CHROM\t"):
            description = f"Per-callset state in order: {', '.join(labels)}"
            destination.write(
                f'##INFO=<ID={_STATE_TAG},Number=.,Type=String,Description="{description}">\n'
                f'##INFO=<ID={_DECISION_TAG},Number=1,Type=String,Description="Comparison decision">\n'
            )
            destination.write(line)
            break
        if not line.startswith(
            (f"##INFO=<ID={PARTIAL},", f"##INFO=<ID={ALIGN_ORIGIN},")
        ):
            destination.write(line)


def _annotate(record: str, calls: list[Call], decision: str) -> str:
    fields = record.split("\t")
    tags = {}
    for item in fields[7].split(";"):
        if item not in {".", PARTIAL}:
            tags[item.partition("=")[0]] = item
    if shifted := tags.pop(ALIGN_ORIGIN, None):
        tags.setdefault(SPLIT_TAG, f"{SPLIT_TAG}={shifted.partition('=')[2]}")
    tags[_STATE_TAG] = f"{_STATE_TAG}=" + ",".join(call.state for call in calls)
    tags[_DECISION_TAG] = f"{_DECISION_TAG}={decision}"
    fields[7] = ";".join(tags.values())
    return "\t".join(fields) + "\n"


def _collect(
    params: VcfCompareParams,
    labeled: Sources,
    dictionary: Dictionary,
    scope: Scope,
    work: Path,
    publish: Path,
) -> tuple[Summary, Counter[str], int]:
    summary = Summary(tuple(labeled.calls))
    group = Groups(
        tuple(summary.labels.index(label) for label in params.include),
        tuple(summary.labels.index(label) for label in params.exclude),
        params.reference_evidence == "gvcf",
    )
    anchor = group.include[0] if group.include else 0
    thresholds = thresholds_from()
    skipped, inconsistent = 0, Counter[str]()
    outcomes: Counter[str] = Counter()
    fmt = params.table_format
    with ExitStack() as stack:
        streams = []
        readers = []
        for index, (label, item) in enumerate(labeled.calls.items()):
            explicit = labeled.evidence.get(label)
            normalized, evidence, count = prepare(
                item,
                dictionary,
                labeled.reference,
                work / f"call-{index}",
                calls=True,
                evidence=group.strict,
                blockers_only=explicit is not None,
            )
            skipped += count
            primary = None
            if explicit:
                if evidence is not None:
                    primary = EvidenceCursor(evidence, thresholds)
                    stack.callback(primary.close)
                _, evidence, _ = prepare(
                    explicit,
                    dictionary,
                    labeled.reference,
                    work / f"gvcf-{index}",
                    calls=False,
                    evidence=True,
                )
                if evidence is None:
                    raise UsageError(
                        f"{label}: --gvcf does not contain reference confidence records."
                    )
            if group.strict and evidence is None:
                warn(
                    "vcf_compare_no_reference_evidence",
                    f"No gVCF evidence for {label}.",
                    label=label,
                )
            reader = EvidenceCursor(evidence, thresholds) if group.strict else None
            if reader is not None:
                stack.callback(reader.close)
            readers.append((reader, primary))
            streams.append(stack.enter_context(normalized.open(encoding="utf-8")))
        variant_writer = None
        if params.emit_variants or group.include:
            columns = _KEY_COLUMNS + tuple(
                Column(f"{label}_{field}", type_)
                for label in summary.labels
                for field, type_ in (
                    ("state", "TEXT"),
                    ("dosage", "INTEGER"),
                    ("ploidy", "INTEGER"),
                )
            )
            variant_writer = stack.enter_context(
                TableWriter(table_path(publish / "compare.variants", fmt), columns, fmt)
            )
        anchor_file = None
        if params.emit_vcf:
            anchor_file = stack.enter_context(
                (work / "anchor.vcf").open(
                    "w",
                    encoding="utf-8",
                    newline="\n",
                )
            )
            _header(streams[anchor], anchor_file, summary.labels)
        order = {name: rank for rank, name in enumerate(dictionary.names.values())}
        for allele, observations in sites(
            streams, order, scope, check_reference=labeled.reference is None
        ):
            calls = []
            for label, observation, (cursor, primary) in zip(
                summary.labels, observations, readers, strict=True
            ):
                call, conflict = resolve(allele, observation, cursor, primary)
                inconsistent[label] += conflict
                calls.append(call)
            kind = variant_type(allele)
            summary.add(allele, kind, calls)
            decision = group.decision(calls)
            outcomes[decision] += 1
            if variant_writer is not None:
                variant_writer.write(
                    [
                        (
                            *allele,
                            kind,
                            sum(call.carried for call in calls),
                            decision,
                            *(value for call in calls for value in call.signature()),
                        )
                    ]
                )
            if (
                anchor_file is not None
                and (record := calls[anchor].record)
                and (decision == "retained" or not group.include)
            ):
                anchor_file.write(_annotate(record, calls, decision))
    for label, count in inconsistent.items():
        if count:
            warn(
                "vcf_compare_evidence_conflict",
                f"{label}: primary ALT conflicts with callable reference evidence.",
                label=label,
                alleles=count,
            )
    return summary, outcomes, skipped


def _publish(
    params: VcfCompareParams,
    summary: Summary,
    outcomes: Counter[str],
    work: Path,
    staged: Path,
    destination: Path,
) -> None:
    result = current_result()
    fmt = params.table_format
    paths = _result_paths(staged, params)
    pairs, support = summary.pair_rows(), summary.support_rows()
    write_table(paths["compare_summary"], PAIR_COLUMNS, pairs, fmt)
    write_table(paths["compare_support"], SUPPORT_COLUMNS, support, fmt)
    report = paths["compare_summary_json"]
    report.write_text(
        json.dumps(
            {
                "schema": _SCHEMA,
                "labels": summary.labels,
                "mode": params.mode,
                "pairs": [
                    dict(zip((c.name for c in PAIR_COLUMNS), row, strict=True))
                    for row in pairs
                ],
                "support": [
                    dict(zip((c.name for c in SUPPORT_COLUMNS), row, strict=True))
                    for row in support
                ],
                "states": summary.label_states(),
                "decisions": outcomes,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if params.emit_vcf:
        anchor = paths["compare_anchor"]
        run_command(
            [
                "bcftools",
                "view",
                "--no-version",
                "-Oz",
                "-o",
                anchor,
                work / "anchor.vcf",
            ],
            capture_output=True,
        )
        run_command(["bcftools", "index", "--csi", anchor])
    publish_output_set({path: destination / path.name for path in paths.values()})
    for role, path in paths.items():
        result.add_output(destination / path.name, role)


def _result_paths(folder: Path, params: VcfCompareParams) -> dict[str, Path]:
    fmt = params.table_format
    paths = {
        "compare_summary": Path(table_path(folder / "compare.summary", fmt)),
        "compare_support": Path(table_path(folder / "compare.support", fmt)),
    }
    if params.emit_variants or params.mode != "concordance":
        paths["compare_variants"] = Path(table_path(folder / "compare.variants", fmt))
    if params.emit_vcf:
        anchor = folder / "compare.anchor.vcf.gz"
        paths["compare_anchor_index"] = Path(f"{anchor}.csi")
        paths["compare_anchor"] = anchor
    # Use this as the atomic publication lock for every output combination.
    paths["compare_summary_json"] = folder / "compare.summary.json"
    return paths


def _check_destinations(
    outdir: Path,
    params: VcfCompareParams,
    sources: Sources,
) -> None:
    inputs = [
        item.path for item in (*sources.calls.values(), *sources.evidence.values())
    ]
    if sources.bed is not None:
        inputs.append(sources.bed)
    if sources.reference is not None:
        inputs.extend((sources.reference, *reference_indexes(sources.reference)))
    input_paths = {path.resolve() for path in inputs}
    for path in (
        *_result_paths(outdir, params).values(),
        # Where the run publishes its manifest (set_manifest_stem below).
        current_result().manifest_path_for_output(outdir / "compare"),
    ):
        if path.resolve() in input_paths:
            raise UsageError(
                f"Comparison output {path} would replace an input. Choose another --outdir."
            )


def run_compare(params: VcfCompareParams) -> None:
    verify_dependencies(["bcftools"])
    check_format(params.table_format)
    sources = Sources.read(params)
    sources.record()
    all_inputs = {
        **sources.calls,
        **{f"{k} evidence": v for k, v in sources.evidence.items()},
    }
    if sources.reference and any(source.md5s for source in all_inputs.values()):
        verify_dependencies(["samtools"])
    dictionary = verify_build(
        all_inputs, sources.reference, params.allow_unverified_build
    )
    result = current_result()
    result.set_reference(sources.reference, dictionary.build)
    first = next(iter(sources.calls.values())).path
    outdir = Path(params.outdir or output_directory([first]) or first.parent)
    _check_destinations(outdir, params, sources)
    outdir.mkdir(parents=True, exist_ok=True)
    result.set_manifest_stem(outdir / "compare")
    scope = Scope(dictionary, sources.bed, params.region)
    thresholds = thresholds_from()
    result.set_details(
        {
            "callsets": [
                {"label": label, "sample": call.sample, "build": call.build}
                for label, call in sources.calls.items()
            ],
            "normalization": "split_left_align" if sources.reference else "split_only",
            "allow_unverified_build": params.allow_unverified_build,
            "reference_evidence": params.reference_evidence,
            "reference_min_depth": thresholds.min_depth,
            "reference_min_gq": thresholds.min_gq,
        }
    )
    with (
        tempfile.TemporaryDirectory(prefix="wgse_compare_") as work_name,
        tempfile.TemporaryDirectory(prefix=".wgse_compare_", dir=outdir) as staged_name,
    ):
        work, staged = Path(work_name), Path(staged_name)
        summary, outcomes, skipped = _collect(
            params, sources, dictionary, scope, work, staged
        )
        conflicts = sum(
            counts.get("conflicting_records", 0)
            for counts in summary.label_states().values()
        )
        if conflicts:
            warn(
                "vcf_compare_conflicting_records",
                "Conflicting duplicate observations were not counted as support.",
                observations=conflicts,
            )
        if skipped:
            warn(
                "vcf_compare_unsupported_alleles",
                "Skipped symbolic, spanning-deletion or breakend alleles.",
                alleles=skipped,
            )
        _publish(params, summary, outcomes, work, staged, outdir)
    result.add_metrics(
        alleles=sum(outcomes.values()),
        retained=outcomes["retained"],
        reference_build=dictionary.build,
        skipped_alleles=skipped,
    )
    human_output(
        f"Compared {sum(outcomes.values()):,} alleles across {len(sources.calls)} callsets: {outdir}"
    )


cmd_compare = cli_handler(VcfCompareParams, run_compare)
