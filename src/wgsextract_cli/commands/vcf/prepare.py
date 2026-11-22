"""``vcf prepare``: diagnose a VCF and repair what stops downstream tools
reading it (#1292). The prepared copy is a coordinate-sorted, BGZF-compressed,
indexed VCF whose header declares every contig (with ``--ref``: named, ordered
and sized as the reference) and every INFO, FORMAT and FILTER ID its records
use; ``--normalize`` also splits multiallelic records and left-aligns indels.

Every repair is a structured warning with its counts, and no record is lost:
the prepared VCF's record count is checked before it is published."""

from __future__ import annotations

import gzip
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.vcf import VcfPrepareParams
from wgsextract_cli.core.bgzf import is_bgzf
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.output_location import single_output_path
from wgsextract_cli.core.ref_library.assets import (
    fasta_build,
    input_stem,
    read_fai_lengths,
)
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.temp_directory import scratch_directory
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    get_resource_threads,
    run_command,
    run_pipeline,
)
from wgsextract_cli.core.variant_confidence import SPLIT_TAG
from wgsextract_cli.core.variant_files import (
    check_header_build,
    publish_indexed_vcf,
    verify_paths_exist,
)

from .basic import require_vcf_input, vcf_input_handler
from .prepare_header import (
    HeaderPlan,
    Scan,
    plan_header,
    refuse_records_beyond_reference,
    scan_records,
    sort_reason,
)

# INFO tag holding a left-aligned record's original CHR|POS|REF|ALT.
OLD_VARIANT_TAG = "OLD_VARIANT"
_EXAMPLES = 5
# Fewer checked records can't tell a few stale REFs from another build.
_WRONG_BUILD_MIN_RECORDS = 20
_COMPRESSION = {"plain": "plain text", "gzip": "plain gzip", "bcf": "BCF"}


def input_compression(path: str) -> str:
    """``bgzf``, ``gzip`` (not BGZF, so it can't be indexed), ``bcf`` or
    ``plain`` text, from the file's content rather than its name."""
    with open(path, "rb") as stream:
        head = stream.read(3)
    if head[:2] != b"\x1f\x8b":
        return "bcf" if head == b"BCF" else "plain"
    try:
        with gzip.open(path, "rb") as stream:
            content = stream.read(3)
    except (OSError, EOFError) as e:
        raise WGSExtractError(f"{path} is not a readable gzip file: {e}") from e
    if content == b"BCF":
        return "bcf"
    return "bgzf" if is_bgzf(path) else "gzip"


def input_index_status(path: str, compression: str, records: int) -> str:
    """``missing``, ``stale`` (older than the VCF), ``invalid`` (unreadable or
    counting other records) or ``valid``; ``none`` for a file that can't have
    a tabix index."""
    if compression != "bgzf":
        return "none"
    indexes = [Path(path + ext) for ext in (".tbi", ".csi")]
    present = [index for index in indexes if index.is_file()]
    if not present:
        return "missing"
    modified = Path(path).stat().st_mtime_ns
    if any(index.stat().st_mtime_ns < modified for index in present):
        return "stale"
    counted = run_command(
        ["bcftools", "index", "--nrecords", path], capture_output=True, check=False
    )
    if counted.returncode or counted.stdout.strip() != str(records):
        return "invalid"
    return "valid"


@dataclass
class NormReport:
    """What ``bcftools norm -c w`` reported: REF mismatches (records kept as
    they are) and its line counts (total, split, realigned, skipped, ...)."""

    mismatches: int = 0
    examples: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def read(cls, path: Path) -> NormReport:
        """Parse bcftools norm's stderr line by line: against another build it
        holds a line per record."""
        report = cls()
        with path.open(encoding="utf-8", errors="replace") as lines:
            report._parse(lines)
        return report

    def _parse(self, lines: Iterable[str]) -> None:
        for line in lines:
            line = line.rstrip("\n")
            if line.startswith("REF_MISMATCH"):
                self.mismatches += 1
                parts = line.split("\t")
                if len(self.examples) < _EXAMPLES and len(parts) >= 5:
                    self.examples.append(
                        f"{parts[1]}:{parts[2]} REF {parts[3]}, --ref {parts[4]}"
                    )
            elif "Reference allele mismatch" in line:  # older bcftools
                self.mismatches += 1
            elif line.startswith("Lines") and ":" in line:
                names, _, values = line.partition(":")
                self.counts = {
                    name: int(value)
                    for name, value in zip(
                        names.split()[-1].split("/"), values.split("/"), strict=False
                    )
                    if value.strip().isdigit()
                }

    @property
    def checked(self) -> int:
        return self.counts.get("total", 0) - self.counts.get("skipped", 0)


@dataclass(frozen=True)
class _Reference:
    fasta: str
    lengths: dict[str, int]


def _reference(params: VcfPrepareParams, input_vcf: str) -> _Reference | None:
    if params.ref is None:
        return None
    fasta = ReferenceLibrary(params.ref, None, input_path=input_vcf).fasta
    if not fasta or not Path(fasta).is_file():
        raise WGSExtractError(f"No reference FASTA found at {params.ref}.")
    lengths = read_fai_lengths(fasta)
    if not lengths:
        raise WGSExtractError(
            f"{fasta} has no FASTA index (.fai); index it with "
            f"`samtools faidx {fasta}`, or give a reference library genome."
        )
    return _Reference(fasta, lengths)


def _exclude(plan: HeaderPlan, scan: Scan) -> list[str]:
    """bcftools norm stops at a record whose contig the FASTA lacks: pass
    such contigs through unchecked."""
    used = {plan.final_name(name) for name in scan.max_pos}
    names = [name for name in plan.off_reference if name in used]
    if not names:
        return []
    return ["-e", " || ".join(f'CHROM=="{name}"' for name in names)]


def _check_ref(vcf: str, ref: str, exclude: list[str], work: Path) -> NormReport:
    """Count REF mismatches, and the records --normalize would left-align,
    without writing anything."""
    err = work / "check.err"
    run_pipeline(
        [["bcftools", "norm", "--no-version", "-f", ref, "-c", "w", *exclude,
          "-Ou", vcf]],
        stdout=subprocess.DEVNULL,
        description="REF check",
        cancel_event=cancel_event(),
        stderr_dir=work,
        keep_stderr={0: err},
    )  # fmt: skip
    return NormReport.read(err)


@dataclass(frozen=True)
class _Rewrite:
    header: HeaderPlan
    sort: bool
    normalize: _Reference | None
    split: bool
    exclude: list[str]
    threads: str


def _rewrite(source: str, job: _Rewrite, staged: Path, work: Path) -> NormReport | None:
    """One bcftools pipeline from the input to the prepared BGZF VCF:
    reheader, rename contigs, split and left-align, then sort or compress."""
    filters: list[list[str]] = []
    if job.header.changed:
        header = work / "header.vcf"
        header.write_text(job.header.text, encoding="utf-8", newline="\n")
        filters.append(["bcftools", "reheader", "-h", str(header), "-"])
    if job.header.renames:
        names = work / "contig_names.txt"
        names.write_text(
            "".join(f"{old}\t{new}\n" for old, new in job.header.renames),
            encoding="utf-8",
            newline="\n",
        )
        filters.append(
            ["bcftools", "annotate", "--no-version", "--rename-chrs", str(names),
             "-Ou", "-"]
        )  # fmt: skip
    norm_stage = None
    if job.normalize is not None:
        if job.split:
            # Split ALTs keep their original record in the tag vcf table and
            # the confidence rules read.
            filters.append(
                ["bcftools", "norm", "--no-version", "-m", "-any", "-N",
                 "--old-rec-tag", SPLIT_TAG, "-Ou", "-"]
            )  # fmt: skip
        # -c w keeps records whose REF differs from the FASTA unchanged.
        filters.append(
            ["bcftools", "norm", "--no-version", "-f", job.normalize.fasta,
             "-c", "w", "--old-rec-tag", OLD_VARIANT_TAG, *job.exclude, "-Ou", "-"]
        )  # fmt: skip
        norm_stage = len(filters)
    reader = ["bcftools", "view", "--no-version"]
    if job.sort or job.normalize is not None:
        sink = ["bcftools", "sort", "-T", str(work / "sort"), "-Oz",
                "-o", str(staged)]  # fmt: skip
    else:
        sink = ["bcftools", "view", "--no-version", "--threads", job.threads,
                "-Oz", "-o", str(staged)]  # fmt: skip
    if filters:
        reader += ["-Ov" if job.header.changed else "-Ou", source]
        stages = [reader, *filters, [*sink, "-"]]
    else:
        stages = [[*sink, source]]
    err = work / "norm.err"
    run_pipeline(
        stages,
        description="VCF preparation",
        cancel_event=cancel_event(),
        stderr_dir=work,
        keep_stderr={norm_stage: err} if norm_stage is not None else None,
    )
    if norm_stage is None:
        return None
    return NormReport.read(err)


def _record_count(vcf: Path) -> int:
    counted = run_command(
        ["bcftools", "index", "--nrecords", str(vcf)], capture_output=True
    )
    return int(counted.stdout.strip() or 0)


def _refuse_wrong_build(report: NormReport) -> None:
    checked = report.checked
    if checked >= _WRONG_BUILD_MIN_RECORDS and report.mismatches * 2 > checked:
        raise WGSExtractError(
            f"{report.mismatches:,} of {checked:,} records have a REF that differs "
            "from --ref: the VCF was called against another build. Give the "
            "reference it was called against."
        )


def _examples(names: list[str]) -> str:
    shown = ", ".join(names[:_EXAMPLES])
    return shown + (
        f" and {len(names) - _EXAMPLES} more" if len(names) > _EXAMPLES else ""
    )


def run_prepare(params: VcfPrepareParams) -> None:
    verify_dependencies(["bcftools", "tabix"])
    input_vcf = require_vcf_input(params)
    verify_paths_exist({"--input": input_vcf})
    output = single_output_path(
        input_vcf,
        params.output,
        params.outdir,
        f"{input_stem(input_vcf)}_prepared.vcf.gz",
    )
    if not output.lower().endswith((".vcf.gz", ".vcf.bgz")):
        raise UsageError(f"--output {output} must end in .vcf.gz: it is BGZF VCF.")
    ref = _reference(params, input_vcf)
    if params.normalize and ref is None:
        raise UsageError("--normalize needs --ref, the FASTA to left-align against.")
    result = current_result()
    result.expect_output(output)
    final = Path(output)
    final.parent.mkdir(parents=True, exist_ok=True)

    compression = input_compression(input_vcf)
    with (
        scratch_directory(final.parent, ".wgse_vcf_prepare_") as work_dir,
        tempfile.TemporaryDirectory(
            dir=final.parent, prefix=".wgse_publish_"
        ) as staging,
    ):
        work = Path(work_dir)
        header = run_command(
            ["bcftools", "view", "-h", "--no-version", input_vcf], capture_output=True
        ).stdout
        if ref is not None:
            check_header_build(header, ref.lengths, "--ref")
        logging.info("Reading %s...", input_vcf)
        scan = scan_records(input_vcf, work)
        plan = plan_header(header, scan, ref.lengths if ref else None)
        if ref is not None:
            refuse_records_beyond_reference(scan, plan, ref.lengths)
        index = input_index_status(input_vcf, compression, scan.records)
        sort = sort_reason(scan, plan)
        exclude = _exclude(plan, scan)
        rewrite = bool(compression != "bgzf" or plan.changed or plan.renames or sort)

        report = None
        if ref is not None and not rewrite:
            report = _check_ref(input_vcf, ref.fasta, exclude, work)
        normalize = params.normalize and (
            rewrite
            or scan.multiallelic > 0
            or (report is not None and report.counts.get("realigned", 0) > 0)
        )
        staged = Path(staging, "prepared.vcf.gz")
        if rewrite or normalize:
            logging.info("Writing the prepared VCF...")
            job = _Rewrite(
                header=plan,
                sort=sort is not None,
                normalize=ref if normalize else None,
                split=scan.multiallelic > 0,
                exclude=exclude,
                threads=get_resource_threads(params.threads, stage="compression"),
            )
            report = _rewrite(input_vcf, job, staged, work) or report
        else:
            logging.info("The input needs no repairs; copying it.")
            shutil.copyfile(input_vcf, staged)
        if ref is not None and report is None:
            report = _check_ref(str(staged), ref.fasta, exclude, work)
        if report is not None:
            _refuse_wrong_build(report)

        expected = scan.records + (scan.extra_alleles if normalize else 0)
        counted: list[int] = []

        def verify(vcf: Path) -> None:
            counted.append(_record_count(vcf))
            if counted[0] != expected:
                raise WGSExtractError(
                    f"The prepared VCF has {counted[0]:,} records where "
                    f"{expected:,} were expected ({scan.records:,} read, "
                    f"{expected - scan.records:,} added by splitting); nothing "
                    "was written."
                )

        final_index = publish_indexed_vcf(staged, final, verify)
        records_out = counted[0]

    repairs = _report(
        compression=compression,
        index=index,
        scan=scan,
        plan=plan,
        sort=sort,
        normalize=bool(normalize),
        norm=report,
        has_ref=ref is not None,
    )
    if ref is not None:
        result.set_reference(ref.fasta, build=fasta_build(ref.fasta))
    result.add_output(final, "prepared_vcf", metrics={"records": records_out})
    result.add_output(final_index, "prepared_vcf_index")
    result.add_metrics(
        records_in=scan.records,
        records_out=records_out,
        input_compression=compression,
        input_index=index,
        repairs=len(repairs),
        multiallelic_records=scan.multiallelic,
        ref_mismatches=report.mismatches if report is not None else None,
        normalized=bool(normalize),
    )
    human_output(f"Prepared {final}: {records_out:,} records.")
    if repairs:
        human_output("Repairs: " + "; ".join(repairs) + ".")
    else:
        human_output("No repairs were needed: the input was copied and indexed.")


def _report(
    *,
    compression: str,
    index: str,
    scan: Scan,
    plan: HeaderPlan,
    sort: str | None,
    normalize: bool,
    norm: NormReport | None,
    has_ref: bool,
) -> list[str]:
    """Warn of each repair made and each problem found; returns the repairs'
    short names."""
    repairs: list[str] = []
    if compression != "bgzf":
        repairs.append("recompressed")
        warn(
            "vcf_prepare_recompressed",
            f"The input was {_COMPRESSION[compression]}; the prepared VCF is BGZF, "
            "which tabix and bcftools can index.",
            input_compression=compression,
        )
    elif index != "valid":
        repairs.append("indexed")
        warn(
            "vcf_prepare_index_rebuilt",
            f"The input's index was {index}; the prepared VCF has a new one.",
            input_index=index,
        )
    if plan.renames:
        repairs.append("contigs renamed")
        warn(
            "vcf_prepare_contigs_renamed",
            f"Renamed {len(plan.renames)} contig(s) to --ref's names ("
            + _examples([f"{old} to {new}" for old, new in plan.renames])
            + ").",
            contigs=len(plan.renames),
        )
    if plan.declared_contigs:
        repairs.append("contigs declared")
        warn(
            "vcf_prepare_contigs_declared",
            f"Declared {len(plan.declared_contigs)} contig(s) the records use but "
            f"the header did not ({_examples(plan.declared_contigs)}).",
            contigs=len(plan.declared_contigs),
        )
    if plan.lengths_added:
        repairs.append("contig lengths added")
        warn(
            "vcf_prepare_contig_lengths_added",
            f"Added --ref's lengths to {len(plan.lengths_added)} contig "
            f"declaration(s) ({_examples(plan.lengths_added)}).",
            contigs=len(plan.lengths_added),
        )
    if plan.reordered:
        repairs.append("contigs reordered")
        warn(
            "vcf_prepare_contigs_reordered",
            "Put the header's contigs in --ref's order.",
        )
    if plan.declared_tags:
        repairs.append("IDs declared")
        warn(
            "vcf_prepare_ids_declared",
            f"Declared {len(plan.declared_tags)} ID(s) the records use without a "
            f"header line ({_examples(plan.declared_tags)}), as String values "
            "kept exactly as written.",
            ids=len(plan.declared_tags),
        )
    if sort is not None:
        repairs.append("sorted")
        warn(
            "vcf_prepare_sorted",
            f"Sorted the records: {scan.out_of_order:,} were out of coordinate order."
            if sort == "positions"
            else "Sorted the records into the header's contig order.",
            reason=sort,
            out_of_order_records=scan.out_of_order,
        )
    counts = norm.counts if norm is not None else {}
    if normalize and scan.multiallelic:
        repairs.append("multiallelics split")
        warn(
            "vcf_prepare_multiallelics_split",
            f"Split {scan.multiallelic:,} multiallelic record(s) into "
            f"{scan.multiallelic + scan.extra_alleles:,} biallelic ones; each "
            f"keeps its original in INFO/{SPLIT_TAG}.",
            records=scan.multiallelic,
            added_records=scan.extra_alleles,
        )
    realigned = counts.get("realigned", 0)
    if normalize and realigned:
        repairs.append("indels left-aligned")
        warn(
            "vcf_prepare_left_aligned",
            f"Left-aligned {realigned:,} record(s) against --ref; each keeps its "
            f"original in INFO/{OLD_VARIANT_TAG}.",
            records=realigned,
        )
    elif realigned:
        warn(
            "vcf_prepare_not_left_aligned",
            f"{realigned:,} record(s) are not left-aligned against --ref; "
            "--normalize would left-align them.",
            records=realigned,
        )
    if norm is not None and norm.mismatches:
        warn(
            "vcf_prepare_ref_mismatch",
            f"{norm.mismatches:,} record(s) have a REF that differs from --ref "
            f"(such as {', '.join(norm.examples)}); they are kept unchanged.",
            records=norm.mismatches,
        )
    if has_ref and plan.off_reference:
        warn(
            "vcf_prepare_contigs_not_in_reference",
            f"{len(plan.off_reference)} contig(s) are not in --ref "
            f"({_examples(plan.off_reference)}); their records are kept, but their "
            "REFs are not checked and their indels are not left-aligned.",
            contigs=len(plan.off_reference),
        )
    if plan.unknown_length:
        warn(
            "vcf_prepare_contig_lengths_unknown",
            f"{len(plan.unknown_length)} contig(s) have no length "
            f"({_examples(plan.unknown_length)})"
            + ("." if has_ref else "; give --ref to add them."),
            contigs=len(plan.unknown_length),
        )
    return repairs


cmd_prepare = vcf_input_handler(VcfPrepareParams, run_prepare)
