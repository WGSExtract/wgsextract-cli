import csv
import logging
import os
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from wgsextract_cli.api._params import job_values
from wgsextract_cli.api.analyze import AnalyzeBatchGenParams, AnalyzeComprehensiveParams
from wgsextract_cli.cli.registration.analyze import register as register
from wgsextract_cli.core.config import typed_settings
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.steps import Step, StepJournal, resume_policy
from wgsextract_cli.core.utils import UsageError, WGSExtractError
from wgsextract_cli.core.variant_confidence import thresholds_from
from wgsextract_cli.core.variant_files import verify_paths_exist

from ._analyze_workflows import (
    JOURNAL,
    AnalyzeJob,
    AnalyzeResult,
    _filename_key,
    _print,
    _step_params,
    _vcf_output_tag,
    detect_vcf_type,
    generate_summary_report,
    run_bam_chain,
    run_step,
    run_vcf_workflow,
    subcommand_action,
)


class BatchSample(TypedDict):
    input: str | None
    vcfs: list[str]


def run_comprehensive(
    params: AnalyzeComprehensiveParams,
    debug: bool = False,
    keep_awake: bool | None = None,
    input_from_config: bool = False,
) -> None:
    """The ``analyze comprehensive`` operation (``api.analyze.comprehensive``).
    ``debug`` and ``keep_awake`` are passed on to the steps' subcommands."""
    job = AnalyzeJob(
        **job_values(params, AnalyzeJob),
        debug=debug,
        keep_awake=keep_awake,
        input_from_config=input_from_config,
    )
    cmd_comprehensive(job)


def cmd_comprehensive(job: AnalyzeJob) -> None:
    from wgsextract_cli.core.config import settings

    verify_dependencies(["bcftools", "samtools", "tabix"])
    log_dependency_info(["bcftools", "samtools", "tabix"])
    thresholds_from(job)  # reject crossed thresholds from config before any work

    batch_sample = job.batch_sample
    has_inputs = job.input or job.vcf_inputs or batch_sample
    batch_file = job.batch or (None if has_inputs else settings.get("batch_file_path"))
    if batch_file:
        run_batch_comprehensive(job, batch_file)
        return

    input_file = job.input
    vcf_inputs = list(job.vcf_inputs or ())
    # CLI alignment and VCF defaults can be configured together, but a selected
    # alignment (including --genome) must never borrow VCFs from that config.
    configured_vcfs = (
        settings.get("vcf_input_paths")
        if not (vcf_inputs or batch_sample)
        and (not input_file or job.input_from_config)
        else None
    )
    if configured_vcfs:
        if isinstance(configured_vcfs, list):
            vcf_inputs = configured_vcfs
        else:
            vcf_inputs = configured_vcfs.split()

    if not input_file and not vcf_inputs:
        raise UsageError(LOG_MESSAGES["input_required"])

    # Pre-flight Validation
    paths_to_verify = {}
    if input_file:
        paths_to_verify["--input"] = input_file
    for i, v in enumerate(vcf_inputs):
        paths_to_verify[f"vcf-input-{i}"] = v

    verify_paths_exist(paths_to_verify)

    outdir = str(absolute_path(job.outdir or os.getcwd()))
    vcf_plan = _plan_vcf_workflows(vcf_inputs, outdir, input_file)
    Path(outdir).mkdir(parents=True, exist_ok=True)
    result = current_result()
    for vcf in vcf_inputs:
        result.add_input(vcf, "vcf_input")
    if not batch_sample:
        result.set_manifest_stem(Path(outdir, "analyze"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _print(f"\nSTAGE: Starting Comprehensive Analysis ({now})")
    _print(f"Output Directory: {outdir}")

    _print("-" * 60)

    with StepJournal(Path(outdir, JOURNAL), "analyze comprehensive",
                     resume_policy(job)) as journal:  # fmt: skip
        results = _run_comprehensive(job, journal, input_file, vcf_plan, outdir)
    if journal.dry_run:
        return

    # 3. Final Summary Report
    generate_summary_report(results, outdir)
    if not batch_sample:
        result.add_metrics(
            vcfs_analyzed=len(results),
            significant_variants=sum(r["count"] for r in results),
        )


def _plan_vcf_workflows(
    vcfs: Sequence[str], outdir: str, alignment: str | None
) -> list[tuple[str, str, str]]:
    """Plan (source, type, output tag) before the workflow writes anything."""
    types = [detect_vcf_type(vcf) for vcf in vcfs]
    tags = [_vcf_output_tag(vcf, kind) for vcf, kind in zip(vcfs, types, strict=True)]
    keys = [_filename_key(tag) for tag in tags]
    counts = Counter(keys)
    used = set(keys)
    sources = [Path(vcf).parent.resolve() / Path(vcf).name for vcf in vcfs]
    protected = {*sources, *(path.resolve() for path in sources)}
    if alignment:
        protected.update((absolute_path(alignment), Path(alignment).resolve()))
    source_paths = {_filename_key(str(path)) for path in protected}
    source_dirs = {
        _filename_key(str(parent)) for path in protected for parent in path.parents
    }
    output_dir = Path(outdir).resolve()

    def overlaps_source(tag: str) -> Path | None:
        work_dir = output_dir / f"work_{tag}"
        output = output_dir / f"significant_{tag}.vcf.gz"
        if any(
            _filename_key(str(path)) in source_dirs
            for path in (work_dir, work_dir.resolve())
        ):
            return work_dir
        if any(
            _filename_key(str(path)) in source_paths
            for path in (output, output.resolve())
        ):
            return output
        return None

    for tag, key in zip(tags, keys, strict=True):
        if counts[key] == 1 and (conflict := overlaps_source(tag)):
            raise UsageError(
                f"Planned VCF output overlaps an input: {conflict}. "
                "Use a different --outdir."
            )

    # Reserve existing names and assign suffixes in source order, independent
    # of the order the user passes the same inputs on a resumed run.
    suffixes: Counter[str] = Counter()
    for i in sorted(range(len(vcfs)), key=lambda i: (str(sources[i]), vcfs[i])):
        key = keys[i]
        if counts[key] == 1:
            continue
        while True:
            suffixes[key] += 1
            candidate = f"{tags[i]}-{suffixes[key]}"
            candidate_key = _filename_key(candidate)
            if candidate_key in used or overlaps_source(candidate):
                continue
            used.add(candidate_key)
            tags[i] = candidate
            break
    return list(zip(vcfs, types, tags, strict=True))


def _run_comprehensive(
    job: AnalyzeJob,
    journal: StepJournal,
    input_file: str | None,
    vcf_plan: list[tuple[str, str, str]],
    outdir: str,
) -> list[AnalyzeResult]:
    result = current_result()
    # 1. BAM/CRAM Analysis (Info, QC, Lineage)
    if input_file:
        result.add_input(input_file)
        sex = run_bam_chain(job, input_file, outdir, journal)
        if not job.batch_sample:
            result.add_metrics(sex=sex)

    # 2. VCF Processing (Independently per type)
    results: list[AnalyzeResult] = []
    if vcf_plan:
        _print(f"\nSTAGE: Processing Input VCFs ({len(vcf_plan)} files)")
        for vcf, v_type, tag in vcf_plan:
            res = run_vcf_workflow(job, vcf, v_type, outdir, journal, output_tag=tag)
            if res:
                results.append(res)
    elif input_file and not job.skip_calling:
        _print("\nSTAGE: Variant Calling (No input VCFs provided)")
        # Call SNV and InDels if nothing else provided
        called = []
        # One gVCF per sample: the SNP step writes it (and reruns when the gVCF
        # or its index is gone), the indel step doesn't repeat it.
        gvcf = typed_settings().reference_evidence == "gvcf"
        baq = typed_settings().baq
        for name, file in (("snp", "snps.vcf.gz"), ("indel", "indels.vcf.gz")):
            vcf = str(Path(outdir, file))
            command = ["vcf", name, "--input", input_file, "--outdir", outdir]
            if baq:
                command += ["--baq"]
            outputs = [vcf]
            if gvcf:
                command += ["--reference-evidence", "gvcf" if name == "snp" else "none"]
            if gvcf and name == "snp":
                from wgsextract_cli.commands.vcf.reference_evidence import GVCF_NAME

                evidence = str(Path(outdir, GVCF_NAME))
                outputs += [evidence, evidence + ".tbi"]
            decision, _ = run_step(
                journal,
                Step(name, inputs={"input": input_file}, outputs=outputs,
                     params=_step_params(job, command), tools=["bcftools"]),
                subcommand_action(command, job),
            )  # fmt: skip
            called.append((vcf, decision))
        for vcf, decision in called:
            if journal.dry_run or Path(vcf).exists():
                res = run_vcf_workflow(
                    job, vcf, "snp-indel", outdir, journal, after=[decision]
                )
                if res:
                    results.append(res)
    return results


def run_batch_comprehensive(job: AnalyzeJob, batch_file: str) -> None:
    """Runs comprehensive analysis for multiple samples defined in batch_file."""

    if not Path(batch_file).exists():
        raise WGSExtractError(f"Batch file not found: {batch_file}")

    with open(batch_file, encoding="utf-8-sig", newline="") as f:
        preview = f.read(1024)
        sniffer = csv.Sniffer()
        dialect: str | type[csv.Dialect]
        try:
            dialect = sniffer.sniff(preview, delimiters=",\t")
        except csv.Error:
            # The sample may end mid-row. Try the complete header before
            # assuming there is no delimiter.
            f.seek(0)
            try:
                dialect = sniffer.sniff(f.readline(), delimiters=",\t")
            except csv.Error:
                dialect = "excel-tab"
        f.seek(0)
        reader = csv.DictReader(f, dialect=dialect)

        # Map header names flexibly
        headers = reader.fieldnames or []
        name_col = next(
            (c for c in headers if c.lower() in ["name", "sample", "id"]), "name"
        )
        input_col = next(
            (c for c in headers if c.lower() in ["input", "bam", "cram"]), "input"
        )
        vcf_col = next((c for c in headers if "vcf" in c.lower()), "vcf")

        failed_samples: list[str] = []
        samples = 0
        current_result().set_manifest_stem(
            Path(job.outdir if job.outdir else os.getcwd(), "analyze")
        )

        for row_number, row in enumerate(reader, 1):
            samples += 1
            # Each sample gets its own folder, so a blank name gets a unique one
            # and a name can't point outside --outdir.
            name = (row.get(name_col) or "").strip() or f"sample{row_number}"
            if Path(name).name != name or name in (".", ".."):
                logging.error(f"Sample name {name!r} isn't a plain folder name.")
                failed_samples.append(name)
                continue
            input_path = row.get(input_col)
            vcf_paths = row.get(vcf_col) or ""

            # Clean up vcf_paths - handle comma or space separated
            vcf_list = []
            if vcf_paths:
                vcf_list = [
                    v.strip()
                    for v in vcf_paths.replace(",", ";").split(";")
                    if v.strip()
                ]

            _print("\n" + "#" * 80)
            _print(f"### BATCH PROCESSING: {name}")
            _print("#" * 80)

            # Create sample-specific outdir
            sample_outdir = str(Path(job.outdir if job.outdir else os.getcwd(), name))
            Path(sample_outdir).mkdir(parents=True, exist_ok=True)

            sample = replace(
                job,
                input=input_path,
                vcf_inputs=tuple(vcf_list),
                outdir=sample_outdir,
                batch=None,
                batch_sample=True,
            )
            try:
                cmd_comprehensive(sample)
            except (OSError, WGSExtractError) as e:
                logging.error(f"Failed to process sample {name}: {e}")
                failed_samples.append(name)

        current_result().add_metrics(
            samples=samples, failed_samples=len(failed_samples)
        )
        if not samples:
            raise UsageError("--batch must contain at least one sample row.")
        if failed_samples:
            raise WGSExtractError(
                "Batch analysis failed for sample(s): " + ", ".join(failed_samples)
            )


def run_batch_gen(params: AnalyzeBatchGenParams) -> None:
    """The ``analyze batch-gen`` operation (``api.analyze.batch_gen``):
    writes a batch file by scanning a directory."""
    scan_dir = str(params.directory)
    if not Path(scan_dir).exists():
        raise WGSExtractError(f"Directory not found: {scan_dir}")

    _print(f"Scanning directory: {scan_dir}")

    samples: dict[str, BatchSample] = {}

    for root, _, files in os.walk(scan_dir):
        for f in files:
            full_path = str(absolute_path(Path(root, f)))
            lower_f = f.lower()

            # Heuristic: sample1.bam -> name sample1
            # sample1.vcf.gz -> name sample1
            # Strip common suffixes for better grouping
            def clean_name(n: str) -> str:
                # Strip all extensions first
                name = n
                while "." in name:
                    base, ext = Path(name).stem, Path(name).suffix
                    if ext.lower() in [
                        ".vcf",
                        ".gz",
                        ".bam",
                        ".cram",
                        ".tbi",
                        ".csi",
                        ".bai",
                        ".crai",
                    ]:
                        name = base
                    else:
                        break

                # Strip common genomic suffixes
                for suffix in [
                    "_snp",
                    "_sv",
                    "_indel",
                    "_cnv",
                    ".snp",
                    ".sv",
                    ".indel",
                    ".cnv",
                    "_metrics",
                    "_info",
                ]:
                    if name.lower().endswith(suffix):
                        name = name[: -len(suffix)]

                return name

            # An earlier analyze run's outputs aren't samples.
            if f.startswith(("significant_", "chain_annotated")):
                continue
            if lower_f.endswith((".bam", ".cram")):
                name = clean_name(f)
                if name not in samples:
                    samples[name] = {"input": None, "vcfs": []}
                if (other := samples[name]["input"]) is not None:
                    raise WGSExtractError(
                        f"{other} and {full_path} would both be sample {name}; "
                        "rename one or scan their folders separately."
                    )
                samples[name]["input"] = full_path

            elif lower_f.endswith((".vcf.gz", ".vcf")):
                name = clean_name(f)
                if name not in samples:
                    samples[name] = {"input": None, "vcfs": []}
                samples[name]["vcfs"].append(full_path)

    if not samples:
        # Writing no batch file and exiting 0 looked like success for a wrong path.
        raise WGSExtractError(f"No BAM, CRAM or VCF files found in {scan_dir}.")

    output_path = str(params.output)
    _print(f"Writing batch file to: {output_path}")

    with open(output_path, "w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["name", "input", "vcf"])
        for name, data in sorted(samples.items()):
            vcf_str = ";".join(data["vcfs"])
            writer.writerow([name, data["input"] or "", vcf_str])

    current_result().add_output(output_path, "batch_file")
    current_result().add_metrics(samples=len(samples))
    _print(f"Generated batch file with {len(samples)} samples.")
