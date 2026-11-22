import ctypes
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from functools import partial
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.vcf import VcfFreebayesParams, VcfGatkParams
from wgsextract_cli.core import ultima
from wgsextract_cli.core.alignment.cram_staging import (
    cram_version,
    htsjdk_cram_index,
    needs_cram30,
)
from wgsextract_cli.core.atomic_output import safe_output
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import get_tool_version, verify_dependencies
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.native_pairhmm import (
    cuda_pairhmm_java_options,
    neon_pairhmm_command,
)
from wgsextract_cli.core.native_support import require_native_tool
from wgsextract_cli.core.output_checks import warn_if_no_variants
from wgsextract_cli.core.paths import java_local_path
from wgsextract_cli.core.ref_library.assets import read_fai_lengths, reference_dict_path
from wgsextract_cli.core.region_selection import (
    is_region_file,
    literal_region,
    region_items,
)
from wgsextract_cli.core.resource_estimates import (
    estimate_resources,
    preflight_resources,
)
from wgsextract_cli.core.temp_directory import configured_temp_directory, scratch_parent
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    run_command,
    run_pipeline,
)
from wgsextract_cli.core.variant_files import ensure_vcf_indexed

from .basic import CallerSetup, plain_reference, record_variants, setup_caller
from .gatk_scatter import call_scattered, scatter_intervals


def _prepare_gatk_input(
    input_file: str, region: str | None, ref: str, outdir: str
) -> tuple[str, str | None]:
    """Return an input GATK can read, converting regional CRAM input when needed."""
    if not input_file.lower().endswith(".cram"):
        return input_file, None

    if not region:
        return input_file, None

    fd, temp_bam = tempfile.mkstemp(
        suffix=".bam", dir=configured_temp_directory() or outdir
    )
    os.close(fd)
    logging.info(f"Preparing regional BAM for GATK from CRAM input ({region})...")
    try:
        samtools = get_tool_path("samtools")
        bed = is_region_file(region)
        regions = [] if bed else region_items(region, commas=True)
        run_command(
            [
                samtools,
                "view",
                "-bh",
                *(["-M", "-L", region] if bed else ["-M"] if len(regions) > 1 else []),
                "-T",
                ref,
                "-o",
                temp_bam,
                "--",  # a region can't be read as another option
                input_file,
                *regions,
            ],
            check=True,
        )
        run_command([samtools, "index", temp_bam], check=True)
    except (OSError, subprocess.SubprocessError, WGSExtractError):
        _cleanup_gatk_temp_input(temp_bam)
        raise
    return temp_bam, temp_bam


def _gatk_region_args(region: str | None, outdir: str) -> tuple[list[str], str | None]:
    """-L through an interval-list file: on Windows GATK parses a bare `20:1-200`
    as a file path and fails on the ':'; a .intervals file reads the same everywhere."""
    if not region:
        return [], None
    if is_region_file(region):
        return ["-L", java_local_path(region)], None
    # A new file, never one a link at a fixed name points to.
    fd, intervals = tempfile.mkstemp(
        dir=scratch_parent(outdir), prefix=".gatk_region_", suffix=".intervals"
    )
    # One interval per line: a comma-separated --region is several.
    lines = "".join(f"{r}\n" for r in region_items(region, commas=True))
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(lines)
    return ["-L", java_local_path(intervals)], intervals


def _gatk_tmp_dir() -> str:
    """A temp dir for GATK without spaces. GATK 4.6 can't load its codecs'
    bundled config from a temp path with a space ("Unable to automatically
    instantiate codec"), and on Windows %TEMP% is under the user's profile, so
    any user whose name has a space would hit it; use the 8.3 short path there."""
    path = tempfile.mkdtemp(prefix="wgse-gatk-", dir=configured_temp_directory())
    if " " in path and sys.platform == "win32":
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(path, buffer, len(buffer)):
            path = buffer.value
    if " " in path:
        logging.warning(
            f"GATK's temp directory has a space ({path}); GATK may fail to start."
        )
    return path


def _cleanup_gatk_temp_input(temp_input: str | None) -> None:
    if not temp_input:
        return
    for path in (temp_input, temp_input + ".bai", temp_input + ".csi"):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as e:
            logging.warning(f"Failed to remove temporary GATK artifact {path}: {e}")


_REGION_NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"
_REGION_SPAN = rf"(?:{_REGION_NUMBER}(?:-{_REGION_NUMBER}?)?|-{_REGION_NUMBER})"
_REGION_FIELD = re.compile(rf"\s*[^,\s]+:{_REGION_SPAN}(?=\s*(?:,|$))|[^,]+")


def write_region_bed(region: str, lengths: dict[str, int], bed: str) -> None:
    """Write 1-based inclusive regions as 0-based half-open BED intervals."""
    if is_region_file(region):
        shutil.copyfile(region, bed)
        return
    rows = []
    for match in _REGION_FIELD.finditer(region):
        part = match[0].strip()
        if not part:
            continue
        # A three-digit contig after a range can also look like a digit group.
        if any(fragment in lengths for fragment in part.split(",")[1:]):
            raise UsageError(f"Ambiguous --region {part!r}; use a BED file.")
        selection = part
        contig, colon, span = part.rpartition(":")
        if (
            part in lengths
            and contig in lengths
            and re.fullmatch(r"[0-9]+(?:-[0-9]*)?|-[0-9]+", span)
        ):
            raise UsageError(f"Ambiguous --region {part!r}; use a BED file.")
        if part not in lengths and colon:
            start, dash, end = span.partition("-")
            if not end:
                if contig not in lengths:
                    raise UsageError(f"Unknown contig in --region: {contig!r}.")
                selection = f"{contig}:{start or '1'}-{lengths[contig]}"
            elif dash and not start:
                selection = f"{contig}:1-{end}"
        try:
            chrom, first, last = literal_region(selection, lengths)
        except ValueError as exc:
            raise UsageError(
                f"Invalid --region {part!r}; expected a contig, contig:start-end, or a BED file."
            ) from exc
        rows.append(f"{chrom}\t{first}\t{last}\n")
    if not rows:
        raise UsageError("--region must contain at least one interval.")
    Path(bed).write_text("".join(rows), encoding="utf-8", newline="\n")


def _gatk_cram_hint(input_file: str, stderr: object) -> str | None:
    """A hint for GATK failing on CRAM input: CRAM 3.1 needs GATK 4.7, and
    otherwise htsjdk's own CRAM error, when its output names one."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    # The message of htsjdk's exception, e.g. "...CRAMException: <message>".
    error = next(
        (
            match.group(1).strip()
            for line in str(stderr or "").splitlines()
            if "cram" in line.lower()
            and (match := re.search(r"[\w.]*(?:Exception|Error): (.+)", line))
        ),
        None,
    )
    version = cram_version(input_file) if Path(input_file).is_file() else None
    if version is not None and version >= (3, 1):
        return (
            f"Hint: GATK before 4.7 can't read CRAM {version[0]}.{version[1]}"
            f"{f' ({error})' if error else ''}. Upgrade GATK or convert to BAM."
        )
    if version is not None and error:
        return (
            f"Hint: GATK (htsjdk) couldn't read the CRAM: {error}. "
            "Converting it to BAM may help."
        )
    return None


def run_freebayes(params: VcfFreebayesParams) -> None:
    """``vcf freebayes``: FreeBayes into ``freebayes.vcf.gz``."""
    verify_dependencies(["freebayes", "bcftools", "tabix", "samtools"])
    setup = setup_caller(params)
    outdir, ref = setup.outdir, setup.ref
    input_file, region = str(params.input), params.region
    if ultima.alignment_is_ultima(input_file):
        ultima.warn_no_flow_model("FreeBayes")

    out_vcf = str(Path(outdir, "freebayes.vcf.gz"))
    logging.info(LOG_MESSAGES["vcf_calling_freebayes"].format(output=out_vcf))

    # FreeBayes needs an uncompressed reference.
    with (
        plain_reference(ref, outdir) as use_ref,
        tempfile.TemporaryDirectory(
            dir=scratch_parent(outdir), prefix=".wgse_fb_"
        ) as work,
    ):
        # Regions as a BED: freebayes reads -r as 0-based (so chr1:1000-1050
        # lost position 1000).
        bed = None
        if region:
            bed = str(Path(work, "regions.bed"))
            write_region_bed(region, read_fai_lengths(use_ref), bed)
        if input_file.lower().endswith(".cram"):
            # Decode CRAM with samtools and stream uncompressed BAM to freebayes.
            # It can't seek in a stream, so its calls are cut to the region
            # afterwards: the streamed reads overlap the region's edges.
            view_cmd = ["samtools", "view", "-u", "-h", "-T", use_ref]
            if bed:
                view_cmd += ["-M", "-L", bed]
            view_cmd += ["--", input_file]
            stages = [view_cmd, ["freebayes", "-f", use_ref, "--stdin"]]
            keep = ["-T", bed] if bed else []
        else:
            targets = ["--targets", bed] if bed else []
            stages = [["freebayes", "-f", use_ref, *targets, input_file]]
            keep = []
        with safe_output(out_vcf) as staged:
            stages.append(["bcftools", "view", *keep, "-Oz", "-o", staged])
            run_pipeline(stages, description="FreeBayes variant calling")
    ensure_vcf_indexed(out_vcf)
    record_variants(out_vcf, "variants", warn_if_no_variants(out_vcf, ref, region))


def _gatk_gpu(params: VcfGatkParams, setup: CallerSetup | None = None) -> None:
    """HaplotypeCaller on an NVIDIA GPU (Parabricks), which reads CRAM itself."""
    from wgsextract_cli.core.parabricks import (
        call_variants,
        plan_gpu_memory,
        verify_parabricks,
    )

    verify_dependencies(["samtools", "tabix"])
    verify_parabricks()
    fit = plan_gpu_memory(
        "haplotypecaller", "gatk", check=not params.no_gpu_memory_check
    )
    setup = setup or setup_caller(params)
    outdir, ref = setup.outdir, setup.ref
    input_file, region = str(params.input), params.region
    if ultima.alignment_is_ultima(input_file):
        ultima.warn_no_flow_model("Parabricks HaplotypeCaller (--gpu)")
    out_vcf = str(Path(outdir, "gatk.vcf.gz"))
    logging.info(LOG_MESSAGES["vcf_calling_gatk"].format(output=out_vcf))
    with safe_output(out_vcf) as staged:
        call_variants("haplotypecaller", input_file, ref, staged, region, fit)
    ensure_vcf_indexed(out_vcf)
    record_variants(out_vcf, "variants", warn_if_no_variants(out_vcf, ref, region))


def _gatk_version() -> str:
    text = get_tool_version("gatk") or ""
    match = re.search(
        r"(?:GATK|Genome Analysis Toolkit)[^\n]*?\bv?(\d+(?:\.\d+)+)",
        text,
        re.IGNORECASE,
    )
    return (
        match.group(1)
        if match
        else text.strip().splitlines()[0]
        if text.strip()
        else ""
    )


def run_gatk(params: VcfGatkParams) -> None:
    from wgsextract_cli.core import reference_evidence as evidence

    from .caller_ploidy import GvcfEngine, call_partitioned

    gatk_command = ["gatk"]
    if params.pair_hmm_neon is not None:
        if params.gpu or params.scatter:
            raise UsageError(
                "--pair-hmm-neon cannot be combined with --gpu or --scatter."
            )
        gatk_command = neon_pairhmm_command(params.pair_hmm_neon, _gatk_version())
        if ultima.alignment_flow_data(str(params.input), params.ref).flow_aware:
            raise UsageError(
                "--pair-hmm-neon does not accelerate Ultima's flow likelihood model."
            )
    require_native_tool("gatk")
    if params.pair_hmm_cuda is not None:
        gatk_command += cuda_pairhmm_java_options(params.pair_hmm_cuda, _gatk_version())
    if params.gpu and params.scatter:
        raise UsageError("GATK --scatter cannot be combined with --gpu.")
    gvcf = None
    if params.reference_evidence == "gvcf":
        if params.gpu or params.scatter:
            raise UsageError(
                "--reference-evidence gvcf needs CPU HaplotypeCaller in one "
                "process; it can't be combined with --gpu or --scatter. Use "
                "--reference-evidence none."
            )
        verify_dependencies(["gatk", "samtools", "bcftools", "tabix"])
        gvcf = GvcfEngine(
            "gatk-haplotypecaller",
            _gatk_version(),
            evidence.GATK_BANDS,
            evidence.GATK_MIN_MAPPING_QUALITY,
        )
    setup = setup_caller(params)
    with ExitStack() as stack:
        read_index = None
        # A --region is cut to a BAM by samtools first; GATK reads the CRAM
        # itself, through its .crai, only without one.
        if not params.gpu and params.region is None and Path(params.input).is_file():
            work = stack.enter_context(
                tempfile.TemporaryDirectory(
                    dir=configured_temp_directory() or setup.outdir,
                    prefix=".wgse_gatk_index_",
                )
            )
            read_index = htsjdk_cram_index(
                str(params.input), work, setup.threads, tool="GATK"
            )
        call_partitioned(
            params,
            setup,
            "gatk",
            partial(
                _run_gatk,
                stage_region=params.region is not None,
                read_index=read_index,
                gatk_command=gatk_command,
            ),
            gvcf,
        )


def _run_gatk(
    params: VcfGatkParams,
    sample_ploidy: int = 2,
    setup: CallerSetup | None = None,
    record: bool = True,
    gvcf: str | None = None,
    *,
    stage_region: bool = True,
    read_index: str | None = None,
    gatk_command: list[str] | None = None,
) -> None:
    """``vcf gatk``: HaplotypeCaller into ``gatk.vcf.gz``, or Parabricks.

    With ``gvcf``, HaplotypeCaller writes its gVCF there (-ERC GVCF, GQ
    bands GATK_BANDS) and GenotypeGVCFs genotypes it into the VCF.
    ``read_index`` replaces the CRAM's own index when GATK reads it as is."""
    require_native_tool("gatk")
    scatter = params.scatter
    if params.gpu:
        if scatter:
            raise UsageError("GATK --scatter cannot be combined with --gpu.")
        _gatk_gpu(params, setup)
        return
    verify_dependencies(
        ["gatk", "samtools", *(["bcftools", "tabix"] if scatter else [])]
    )
    gatk_command = gatk_command or ["gatk"]
    if (
        Path(params.input).is_file()
        and not params.region
        and needs_cram30(str(params.input))
    ):
        version = get_tool_version("gatk") or ""
        match = re.search(
            r"(?:GATK|Genome Analysis Toolkit)[^\n]*?\bv?(\d+)\.(\d+)\.(\d+)",
            version,
            re.IGNORECASE,
        )
        if match and tuple(map(int, match.groups())) < (4, 7, 0):
            raise UsageError(
                f"GATK {'.'.join(match.groups())} cannot read CRAM 3.1 directly; "
                "GATK 4.7 or newer can. Upgrade GATK, use --output-profile "
                "calling when aligning, convert to BAM, or pass --region "
                "for automatic regional conversion."
            )
    if sys.platform == "win32":
        logging.warning(
            "Native Windows GATK uses AVX OpenMP PairHMM and AVX2 Smith-Waterman; "
            "compression remains Java-based. Runtime and numeric annotations may differ "
            "from Linux; check GATK's kernel-selection logs."
        )
    setup = setup or setup_caller(params)
    _threads, outdir, ref, lib = setup.threads, setup.outdir, setup.ref, setup.lib
    input_file, region = str(params.input), params.region
    shards = scatter_intervals(ref, region, _threads) if scatter else []
    preflight_resources(
        lambda: estimate_resources(
            "gatk",
            input_paths=[input_file],
            output_dir=outdir,
            reference=ref,
            threads=_threads,
            region=region,
            temp_dir=configured_temp_directory() or tempfile.gettempdir(),
            gatk_staging_dir=configured_temp_directory(),
            gatk_workers=len(shards) or 1,
            reference_evidence=gvcf is not None,
        )
    )
    out_vcf = str(Path(outdir, "gatk.vcf.gz"))
    # Ultima's own pipeline calls with the flow-based likelihood model.
    flow_args = []
    if ultima.alignment_flow_data(input_file, ref).flow_aware:
        if params.pair_hmm_cuda is not None:
            raise UsageError(
                "--pair-hmm-cuda does not accelerate Ultima's flow likelihood model."
            )
        if params.pair_hmm_neon is not None:
            raise UsageError(
                "--pair-hmm-neon does not accelerate Ultima's flow likelihood model."
            )
        logging.info(
            "Ultima reads: calling in GATK's flow mode (--flow-mode ADVANCED)."
        )
        flow_args = ["--flow-mode", "ADVANCED"]
    elif scatter:
        raise WGSExtractError("GATK --scatter requires flow-aware Ultima input.")
    if len(shards) == 1:
        logging.info("GATK scattering fits one worker; using the unscattered path.")
        shards = []

    # GATK requires a .dict file
    if not lib.dict_file:
        logging.info(LOG_MESSAGES["vcf_generating_dict"])
        dict_file = java_local_path(reference_dict_path(ref))
        try:
            run_command(
                ["samtools", "dict", "-o", dict_file, java_local_path(ref)],
                check=True,
            )
            lib.dict_file = dict_file
        except (OSError, subprocess.SubprocessError) as e:
            raise WGSExtractError(f"Failed to generate GATK dict file: {e}") from e

    logging.info(LOG_MESSAGES["vcf_calling_gatk"].format(output=out_vcf))
    region_args, region_file = _gatk_region_args(region, outdir)
    gatk_input: str | None = None
    temp_gatk_input = None
    gatk_tmp = _gatk_tmp_dir()

    try:
        gatk_input, temp_gatk_input = _prepare_gatk_input(
            input_file, region if stage_region else None, ref, outdir
        )
        reads = ["-I", java_local_path(gatk_input)]
        if read_index and gatk_input == input_file:
            reads += ["--read-index", java_local_path(read_index)]
        if shards:
            with tempfile.TemporaryDirectory(
                dir=configured_temp_directory() or outdir, prefix=".wgse_gatk_scatter_"
            ) as work:
                with safe_output(out_vcf) as staged:
                    call_scattered(
                        [
                            "gatk",
                            "HaplotypeCaller",
                            "-R",
                            java_local_path(ref),
                            *reads,
                            *flow_args,
                            "--sample-ploidy",
                            str(sample_ploidy),
                        ],
                        shards,
                        staged,
                        Path(work),
                        gatk_tmp,
                    )
            ensure_vcf_indexed(out_vcf)
            if record:
                record_variants(
                    out_vcf, "variants", warn_if_no_variants(out_vcf, ref, region)
                )
            return
        cmd = (
            [
                *gatk_command,
                "HaplotypeCaller",
                "-R",
                java_local_path(ref),
                *reads,
                "-O",
                java_local_path(gvcf or out_vcf),
                "--tmp-dir",
                java_local_path(gatk_tmp),
            ]
            + flow_args
            + ["--sample-ploidy", str(sample_ploidy)]
            + region_args
        )
        if params.pair_hmm_cuda is not None or params.pair_hmm_neon is not None:
            cmd += ["--native-pair-hmm-threads", _threads]
        if params.pair_hmm_neon is not None:
            # contract: require double to avoid the known float block-DP
            # regression; difficult-region exact parity remains unqualified.
            cmd += [
                "--native-pair-hmm-use-double-precision",
                "true",
            ]
        if gvcf:
            from wgsextract_cli.core.reference_evidence import GATK_BANDS

            # contract: reference confidence blocks banded by GQ (GATK_BANDS)
            # are what the gVCF's evidence means.
            cmd += ["-ERC", "GVCF"]
            for band in GATK_BANDS.split(","):
                cmd += ["-GQB", band]
            run_command(cmd)
            with safe_output(out_vcf) as staged:
                run_command(
                    [
                        "gatk",
                        "GenotypeGVCFs",
                        "-R",
                        java_local_path(ref),
                        "-V",
                        java_local_path(gvcf),
                        "-O",
                        java_local_path(staged),
                        "--tmp-dir",
                        java_local_path(gatk_tmp),
                    ]
                )
        else:
            with safe_output(out_vcf) as staged:
                cmd[cmd.index("-O") + 1] = java_local_path(staged)
                run_command(cmd)
        ensure_vcf_indexed(out_vcf)
        if record:
            record_variants(
                out_vcf, "variants", warn_if_no_variants(out_vcf, ref, region)
            )
    except subprocess.CalledProcessError as e:
        logging.error(f"GATK failed: {e}")
        # Only GATK's own errors on what it read; not samtools' cutting a region.
        if gatk_input and (hint := _gatk_cram_hint(gatk_input, e.stderr)):
            logging.error(hint)
        raise WGSExtractError("GATK failed.") from e
    finally:
        _cleanup_gatk_temp_input(temp_gatk_input)
        if region_file:
            Path(region_file).unlink(missing_ok=True)
        shutil.rmtree(gatk_tmp, ignore_errors=True)


cmd_freebayes = cli_handler(VcfFreebayesParams, run_freebayes)
cmd_gatk = cli_handler(VcfGatkParams, run_gatk)
