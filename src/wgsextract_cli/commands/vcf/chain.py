import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import cast

from wgsextract_cli.api.vcf import VcfChainAnnotateParams, VcfTableParams
from wgsextract_cli.api.vcf_specs.annotations import CHAIN_ANNOTATIONS
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.config import config_args
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.output_checks import vcf_record_count
from wgsextract_cli.core.paths import PathInput, absolute_path
from wgsextract_cli.core.ref_library.assets import read_fai_lengths
from wgsextract_cli.core.ref_library.resolver import (
    ReferenceLibrary,
    header_contig_lengths,
)
from wgsextract_cli.core.reference_evidence import canonical_region
from wgsextract_cli.core.region_selection import (
    bed_intervals,
    is_region_file,
    literal_region,
    region_items,
)
from wgsextract_cli.core.resource_policy import get_resource_threads
from wgsextract_cli.core.results import MANIFEST_SUFFIX, current_result, warn
from wgsextract_cli.core.steps import (
    Decision,
    Step,
    StepJournal,
    StepRun,
    resume_policy,
)
from wgsextract_cli.core.table_writer import check_format, table_path
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command
from wgsextract_cli.core.variant_files import (
    calculate_bam_md5,
    ensure_vcf_indexed,
    match_chromosome_name,
)

from .._analyze_workflows import run_step
from .._vep_engines import VepEngine, configured_engine
from .._vep_run import vep_output_paths
from .basic import record_variants, require_vcf_input, vcf_input_handler
from .clinvar_revel import CLINVAR_ANNOTATED, REVEL_ANNOTATED
from .deleteriousness import CADD_ANNOTATED, GERP_ANNOTATED
from .population import GNOMAD_ANNOTATED, PHYLOP_ANNOTATED
from .splicing import ALPHAMISSENSE_ANNOTATED, PHARMGKB_ANNOTATED, SPLICEAI_ANNOTATED
from .table import run_table, table_stem

# annotation -> (ReferenceLibrary attribute holding its data, step output name)
_VCF_STEPS = {
    "clinvar": ("clinvar_vcf", CLINVAR_ANNOTATED),
    "revel": ("revel_file", REVEL_ANNOTATED),
    "phylop": ("phylop_file", PHYLOP_ANNOTATED),
    "cadd": ("cadd_file", CADD_ANNOTATED),
    "gerp": ("gerp_file", GERP_ANNOTATED),
    "gnomad": ("gnomad_vcf", GNOMAD_ANNOTATED),
    "spliceai": ("spliceai_vcf", SPLICEAI_ANNOTATED),
    "alphamissense": ("alphamissense_vcf", ALPHAMISSENSE_ANNOTATED),
    "pharmgkb": ("pharmgkb_vcf", PHARMGKB_ANNOTATED),
}


def _step_command(ann: str) -> list[str]:
    if ann == "vep":
        return ["vep", "run"]
    return ["vcf", ann]


def _step_output(ann: str, step_input: str, step_outdir: str) -> str:
    if ann == "vep":
        return vep_output_paths(step_input, step_outdir, "vcf")[1]
    return str(Path(step_outdir, _VCF_STEPS[ann][1]))


def _forward_step_warnings(step: str, ann: str, step_outdir: str) -> None:
    """Repeat what a step warned about its annotation data (``annotation_*``,
    such as resource records it excluded): its log was captured and its folder
    is removed. Findings about the sample stay with the step's own command."""
    for manifest in sorted(Path(step_outdir).glob(f"*{MANIFEST_SUFFIX}")):
        if manifest.name.endswith(".failed" + MANIFEST_SUFFIX):
            continue  # an earlier, failed attempt's
        for item in json.loads(manifest.read_text(encoding="utf-8"))["warnings"]:
            if item["code"].startswith("annotation_"):
                warn(
                    item["code"],
                    f"{step} '{ann}': {item['message']}",
                    **item["details"],
                )


def dataset(ann: str, lib: ReferenceLibrary) -> str | None:
    """The reference data an annotation step reads: a file, or VEP's cache
    directory; None when it isn't installed."""
    # chain-annotate is meant to run offline, so VEP also needs its cache: the
    # configured engine's (vep-rs keeps its own in the cache's vep-rs folder).
    if ann == "vep":
        if not lib.vep_cache:
            return None
        cache = vep_step_engine(lib).cache_root(Path(lib.vep_cache))
        return str(cache) if cache.is_dir() else None
    return getattr(lib, _VCF_STEPS[ann][0], None) or None


def _step_resource_args(ann: str, lib: ReferenceLibrary) -> list[str]:
    if ann == "vep":
        return ["--vep-cache", lib.vep_cache] if lib.vep_cache else []
    resource = dataset(ann, lib)
    if not resource:
        return []
    flag = "--am-file" if ann == "alphamissense" else f"--{ann}-file"
    args = [flag, resource]
    if ann == "cadd" and lib.cadd_indel_file:
        args += ["--cadd-indel-file", lib.cadd_indel_file]
    return args


def vep_step_engine(lib: ReferenceLibrary) -> VepEngine:
    """The engine the VEP step's `vep run` will use with this library's cache."""
    return configured_engine([Path(lib.vep_cache)] if lib.vep_cache else [])


def skip_reason(ann: str, lib: ReferenceLibrary) -> str | None:
    """Why an annotation step can't run here, or None if it can."""
    tool = "bcftools"
    if ann == "vep":
        tool = "vep-rs" if vep_step_engine(lib).name == "vep-rs" else "vep"
    if not get_tool_path(tool):
        return f"{tool} is not installed."
    if not dataset(ann, lib):
        return (
            "required reference data not found. "
            f"Run 'wgsextract ref {ann}' to download."
        )
    if ann == "clinvar" and not getattr(lib, "fasta", None):
        return "matching reference FASTA not found; pass --ref with an indexed FASTA."
    return None


def _contig_lengths(vcf: str, fasta: str | None) -> dict[str, int]:
    """The VCF's ##contig lengths, else the reference's: what --region and
    --bed are matched and clipped against."""
    lengths = header_contig_lengths(get_bam_header(vcf))
    if not lengths and fasta:
        lengths = read_fai_lengths(fasta)
    if not lengths:
        raise WGSExtractError(
            f"{vcf} names no contigs (##contig header lines), so --region and "
            "--bed can't be matched to it; pass --ref with an indexed FASTA."
        )
    return lengths


def _missing_contig(contig: str, vcf: str, lengths: dict[str, int]) -> str:
    shown = ", ".join(list(lengths)[:5]) + (", ..." if len(lengths) > 5 else "")
    return f"{contig} isn't a contig of {vcf} (it has {shown})."


def _scope(
    params: VcfChainAnnotateParams, vcf: str, fasta: str | None
) -> list[tuple[str, int, int]] | None:
    """The (contig, 0-based start, end) intervals --region or --bed keeps,
    named as the VCF names its contigs (with or without 'chr'), clipped to
    the contigs and merged; None annotates every call."""
    if not (params.region or params.bed):
        return None
    if vcf.lower().endswith((".bam", ".cram")):
        raise UsageError("--region and --bed need a VCF input.")
    lengths = _contig_lengths(vcf, fasta)
    rows: list[tuple[str, int, int]] = []
    if params.region:
        if is_region_file(params.region):
            raise UsageError(f"--region {params.region} is a file; pass it with --bed.")
        for item in region_items(params.region, commas=True):
            # A whole name first: HLA allele contigs contain colons.
            contig = item if item in lengths else item.rsplit(":", 1)[0]
            named = match_chromosome_name(contig, lengths)
            if named is None:
                raise WGSExtractError(
                    f"--region {item}: {_missing_contig(contig, vcf, lengths)}"
                )
            try:
                chrom, start, end = literal_region(named + item[len(contig) :], lengths)
            except ValueError as error:
                raise UsageError(f"--region: {error}") from None
            if start >= lengths[chrom]:
                raise WGSExtractError(
                    f"--region {item} starts beyond the end of {chrom} "
                    f"({lengths[chrom]:,} bp in {vcf})."
                )
            rows.append((chrom, start, min(end, lengths[chrom])))
    else:
        bed = os.fspath(cast(Path, params.bed))
        try:
            listed = bed_intervals(bed)
        except (OSError, ValueError) as error:
            raise UsageError(f"--bed {bed}: {error}") from None
        absent: set[str] = set()
        for contig, start, end in listed:
            named = match_chromosome_name(contig, lengths)
            if named is None:
                absent.add(contig)
            elif min(end, lengths[named]) > start:
                rows.append((named, start, min(end, lengths[named])))
        if absent:
            warn(
                "bed_contigs_not_in_vcf",
                f"--bed names contigs {vcf} lacks; their regions are left out: "
                f"{', '.join(sorted(absent))}.",
                contigs=",".join(sorted(absent)),
                path=bed,
            )
        if not rows:
            raise WGSExtractError(f"--bed {bed} covers none of {vcf}'s contigs.")
    order = {name: index for index, name in enumerate(lengths)}
    rows.sort(key=lambda row: (order[row[0]], row[1]))
    merged: list[tuple[str, int, int]] = []
    for item in canonical_region(rows).split(","):
        contig, _, span = item.rpartition(":")
        first, _, last = span.partition("-")
        merged.append((contig, int(first) - 1, int(last)))
    return merged


def _write_subset(
    vcf: str, intervals: list[tuple[str, int, int]], out: str, threads: str
) -> None:
    """Keep the calls overlapping ``intervals``: by the index when the VCF is
    bgzipped (one is made beside it if missing), else in one streaming pass."""
    regions = Path(out).parent / "regions.tsv"
    # A regions file, as -r can't parse contig names with ':' (hs38DH's HLA-*).
    regions.write_text(
        "".join(f"{c}\t{s + 1}\t{e}\n" for c, s, e in intervals),
        encoding="utf-8",
        newline="\n",
    )
    if vcf.lower().endswith((".gz", ".bgz")):
        ensure_vcf_indexed(vcf)
        select = ["-R", str(regions), "--regions-overlap", "record"]
    else:
        select = ["-T", str(regions), "--targets-overlap", "record"]
    with atomic_output(out) as staged:
        run_command(
            ["bcftools", "view", "--no-version", *select, "--threads", threads,
             "-Oz", "-o", staged, "--write-index=tbi", vcf],
        )  # fmt: skip


def run_chain_annotate(
    params: VcfChainAnnotateParams, keep_awake: bool | None = None
) -> None:
    verify_dependencies(["bcftools", "tabix"])
    check_format(params.table_format)
    input_file = require_vcf_input(params)
    outdir = str(params.outdir or absolute_path(input_file).parent)
    annotations = [
        a.strip().lower() for a in params.annotations.split(",") if a.strip()
    ]
    if not annotations:
        raise WGSExtractError("No valid annotations provided in --annotations.")
    unknown = [a for a in annotations if a not in CHAIN_ANNOTATIONS]
    if unknown:
        raise WGSExtractError(
            f"Unknown annotation type(s): {', '.join(unknown)}. Valid: "
            f"{', '.join(CHAIN_ANNOTATIONS)}"
        )
    repeated = sorted({a for a in annotations if annotations.count(a) > 1})
    if repeated:
        raise WGSExtractError(
            f"--annotations lists {', '.join(repeated)} more than once."
        )

    logging.info(f"Starting chained annotation with: {', '.join(annotations)}")

    md5_sig = (
        calculate_bam_md5(input_file, None)
        if input_file.lower().endswith((".bam", ".cram"))
        else None
    )
    lib = ReferenceLibrary(params.ref, md5_sig, input_path=input_file)
    fasta = str(lib.fasta) if getattr(lib, "fasta", None) else None
    reference = fasta or params.ref
    intervals = _scope(params, input_file, fasta)
    if intervals is not None:
        current_result().add_metrics(
            region_intervals=len(intervals),
            region_bases=sum(end - start for _, start, end in intervals),
        )
        if params.bed:
            current_result().add_input(params.bed, "regions_bed")

    runnable: list[tuple[int, str]] = []
    skipped: list[str] = []
    for i, ann in enumerate(annotations):
        reason = skip_reason(ann, lib)
        if reason:
            warn(
                "annotation_skipped",
                f"[{i + 1}/{len(annotations)}] Skipping '{ann}': {reason}",
                annotation=ann,
                reason=reason,
            )
            skipped.append(ann)
        else:
            runnable.append((i, ann))
    if not runnable:
        raise WGSExtractError(
            "No annotation was applied: every requested step was skipped."
        )
    applied = [ann for _, ann in runnable]
    vep_tool = [vep_step_engine(lib).tool] if "vep" in applied else []
    scope = canonical_region(intervals) if intervals is not None else None

    final_out = str(Path(outdir, "chain_annotated.vcf.gz"))
    chain: list[tuple[Step, Callable[[StepRun], None]]] = []
    current_input = input_file
    if intervals is not None:
        subset_vcf = str(Path(outdir, "chain_step_0_subset", "subset.vcf.gz"))

        def subset(handle: StepRun) -> None:
            Path(subset_vcf).parent.mkdir(parents=True, exist_ok=True)
            threads = get_resource_threads(params.threads, stage="compression")
            logging.info(f"Keeping the calls in {len(intervals)} region(s)...")
            _write_subset(input_file, intervals, subset_vcf, threads)

        subset_step = Step(
            "subset",
            inputs={"vcf": input_file, "bed": params.bed},
            outputs=[subset_vcf, subset_vcf + ".tbi"],
            # The resolved scope, not the text: "22" and "chr22" agree.
            params={"scope_sha256": sha256(str(scope).encode()).hexdigest()},
            tools=["bcftools"],
        )
        chain.append((subset_step, subset))
        current_input = subset_vcf

    step_dirs: list[tuple[str, str, str]] = []
    for i, ann in runnable:
        label = f"[{i + 1}/{len(annotations)}]"
        step_outdir = str(Path(outdir, f"chain_step_{i + 1}_{ann}"))
        step_out = _step_output(ann, current_input, step_outdir)
        command = [*_step_command(ann), *_step_resource_args(ann, lib)]

        def annotate(
            handle: StepRun,
            ann: str = ann,
            label: str = label,
            step_input: str = current_input,
            step_outdir: str = step_outdir,
            step_out: str = step_out,
            command: list[str] = command,
        ) -> None:
            Path(step_outdir).mkdir(parents=True, exist_ok=True)
            logging.info(f"{label} Running '{ann}' annotation...")
            cmd = [
                sys.executable,
                "-m",
                "wgsextract_cli.main",
                *config_args(keep_awake=keep_awake),
                *command,
                "--input",
                step_input,
                "--outdir",
                step_outdir,
            ]
            if reference:
                cmd += ["--ref", str(reference)]
            if params.threads:
                # Steps run one at a time, so each may use every thread.
                cmd += ["--threads", str(params.threads)]
            try:
                # Captured so chained runs stay readable; surfaced on failure.
                res = run_command(cmd, capture_output=True)
            except subprocess.CalledProcessError as e:
                for line in (e.stderr or "").strip().splitlines():
                    logging.error(f"  [{ann} ERROR] {line}")
                raise WGSExtractError(f"{label} Annotation step '{ann}' failed.") from e
            if not Path(step_out).exists():
                for line in (res.stderr or "").strip().splitlines():
                    logging.error(f"  [{ann} STDERR] {line}")
                raise WGSExtractError(f"{label} '{ann}' did not produce {step_out}.")
            for ext in (".tbi", ".csi"):
                if Path(step_out + ext).exists():
                    handle.add_output(step_out + ext)
            logging.info(f"{label} '{ann}' completed: {step_out}")

        step = Step(
            ann,
            inputs={
                "vcf": current_input,
                "dataset": dataset(ann, lib),
                "reference": fasta,
            },
            outputs=[step_out],
            params={"command": command, "ref": reference},
            tools=["bcftools", *(vep_tool if ann == "vep" else [])],
        )
        chain.append((step, annotate))
        step_dirs.append((label, ann, step_outdir))
        current_input = step_out
    last_output = current_input

    def publish(handle: StepRun) -> None:
        # Here, so a rerun that skips the whole chain reports them again.
        for label, ann, step_outdir in step_dirs:
            _forward_step_warnings(label, ann, step_outdir)
        with atomic_output(final_out) as staged:
            shutil.copy2(last_output, staged)
            for ext in (".tbi", ".csi"):
                if Path(last_output + ext).exists():
                    shutil.copy2(last_output + ext, staged + ext)
        for ext in (".tbi", ".csi"):
            if Path(final_out + ext).exists():
                handle.add_output(final_out + ext)
        count = vcf_record_count(final_out)
        record_variants(final_out, "annotated_vcf", count)
        if count == 0 and intervals is not None:
            warn(
                "no_variants",
                f"{input_file} has no calls in --region/--bed; the annotated "
                "VCF is empty.",
                path=input_file,
            )
        logging.info(f"Chain annotation complete: {final_out}")

    # Keyed by what the whole chain read and did, so a rerun of a finished
    # chain is skipped even after its intermediate files were removed.
    publish_inputs: dict[str, PathInput | None] = {
        "vcf": input_file,
        "bed": params.bed,
        "reference": fasta,
    }
    publish_inputs |= {f"dataset:{a}": dataset(a, lib) for a in applied}
    publish_step = Step(
        "publish",
        inputs=publish_inputs,
        outputs=[final_out],
        params={
            "annotations": applied,
            "steps": {step.name: dict(step.params) for step, _ in chain},
        },
        tools=["bcftools", *vep_tool],
    )

    policy = resume_policy(params)
    with StepJournal.for_output(final_out, "vcf chain-annotate", policy) as journal:
        decisions: list[Decision] = []
        finished = not any(policy.forced(step.name) for step, _ in chain) and (
            not journal.check(publish_step).run
        )
        try:
            if not finished:
                for step, action in chain:
                    folder = Path(step.outputs[0]).parent
                    decision, _ = run_step(journal, step, action, clean=[folder])
                    decisions.append(decision)
            published, _ = run_step(journal, publish_step, publish, after=decisions)
        except WGSExtractError:
            if not journal.dry_run:
                logging.warning(
                    "Keeping the completed steps' files: rerun the same command "
                    "to resume from the step that failed."
                )
            raise
        current_result().add_metrics(
            annotations_applied=",".join(applied), annotations_skipped=len(skipped)
        )
        if params.table_format:
            _table_step(journal, final_out, outdir, params.table_format, published)
        if journal.dry_run:
            return
        if not params.keep_intermediates:
            done = [step for step, _ in chain if Path(step.outputs[0]).parent.exists()]
            if done:
                logging.info("Cleaning up intermediate files...")
            for step in done:
                shutil.rmtree(Path(step.outputs[0]).parent, ignore_errors=True)
            for step, _ in chain:
                journal.retire(step.name)


def _table_step(
    journal: StepJournal,
    final_out: str,
    outdir: str,
    table_format: str,
    published: Decision,
) -> None:
    """``vcf table`` of the annotated VCF, as the ``table`` step."""
    stem = Path(outdir, table_stem(final_out))
    step = Step(
        "table",
        inputs={"vcf": final_out},
        outputs=[table_path(stem, table_format), f"{stem}.sqlite"],
        params={"format": table_format},
        tools=["bcftools"],
    )

    def table(handle: StepRun) -> None:
        run_table(
            VcfTableParams(
                input=Path(final_out), outdir=Path(outdir), table_format=table_format
            )
        )

    run_step(journal, step, table, after=[published])


cmd_chain_annotate = vcf_input_handler(
    VcfChainAnnotateParams, run_chain_annotate, keep_awake="keep_awake"
)
