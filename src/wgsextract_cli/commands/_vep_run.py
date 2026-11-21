import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.api.vep import VepRunParams
from wgsextract_cli.core import read_scope
from wgsextract_cli.core.alignment.metadata import get_vcf_build
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.builds import (
    build_from_path,
    canonical_build,
    ploidy_for_build,
)
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.errors import CodedError
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.output_checks import vcf_record_count
from wgsextract_cli.core.paths import absolute_path, expand_path
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary
from wgsextract_cli.core.resource_estimates import (
    estimate_vep_run,
    preflight_resources,
)
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.sample_ploidy import sex_samples_args
from wgsextract_cli.core.temp_directory import move_file, scratch_parent
from wgsextract_cli.core.utils import (
    WGSExtractError,
    get_resource_defaults,
    run_command,
    run_pipeline,
)
from wgsextract_cli.core.variant_files import (
    calculate_bam_md5,
    ensure_vcf_indexed,
)

from ._analyze_workflows import detect_vcf_type
from ._vep_engines import (
    CHM13,
    VepEngine,
    refuse_unsupported_args,
    require_assembly,
    resolve_engine,
    vep_engine,
)
from ._vep_resources import preprocess_vcf_chr_prefix
from .vcf.call_cleanup import indel_cleanup_pipeline

GENOMIC_SUFFIXES = (".vcf", ".vcf.gz", ".bcf", ".bam", ".cram")
_FORMAT_EXT = {"vcf": ".vcf", "tab": ".txt", "json": ".json"}


class _VepCacheMissingError(CodedError):
    def __init__(self, message: str) -> None:
        super().__init__("vep_cache_missing", message)


# The VEP options of each --preset. 'fast' leaves out what makes VEP slow,
# looking up known variants (population frequencies) and HGVS: about half the
# time of --everything on a genome's chr20.
VEP_PRESETS = {
    "everything": ["--everything"],
    "fast": [
        "--symbol",
        "--canonical",
        "--mane",
        "--biotype",
        "--variant_class",
        "--sift",
        "b",
        "--polyphen",
        "b",
    ],  # fmt: skip
}


def _input_stem(input_path: str) -> str:
    """The file name without its genomic suffix: s.chr1.vcf.gz -> s.chr1."""
    name = Path(input_path).name
    for suffix in sorted(GENOMIC_SUFFIXES, key=len, reverse=True):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def vep_output_paths(input_path: str, outdir: str, fmt: str) -> tuple[str, str]:
    """Return (output path without .gz, final output path) for an input file."""
    output_file = str(Path(outdir, f"{_input_stem(input_path)}_vep{_FORMAT_EXT[fmt]}"))
    return output_file, output_file + ".gz" if fmt == "vcf" else output_file


def _is_vep_output(path: str) -> bool:
    return _input_stem(path).endswith("_vep")


def _vep_input_files(input_path: str) -> list[str]:
    if not Path(input_path).is_dir():
        if not input_path.lower().endswith(GENOMIC_SUFFIXES):
            raise WGSExtractError(
                f"Unsupported VEP input (expected VCF/BCF/BAM/CRAM): {input_path}"
            )
        return [input_path]
    # A rerun over the directory mustn't annotate the outputs written into it.
    input_files = sorted(
        str(Path(input_path, f))
        for f in os.listdir(input_path)
        if f.lower().endswith(GENOMIC_SUFFIXES) and not _is_vep_output(f)
    )
    if not input_files:
        raise WGSExtractError(f"No valid genomic files found in {input_path}")
    # A server job may read only its allowed folders, and a link in the
    # folder can lead elsewhere.
    for path in input_files:
        if not read_scope.may_follow(path):
            raise WGSExtractError(
                f"{path} leads outside the folders this run may read."
            )
    stems: dict[str, str] = {}
    for path in input_files:
        if (other := stems.setdefault(_input_stem(path), path)) != path:
            raise WGSExtractError(
                f"{Path(other).name} and {Path(path).name} would both be annotated "
                f"to {_input_stem(path)}_vep; annotate them separately."
            )
    return input_files


# The reference library's build of each assembly VEP annotates against.
_ASSEMBLY_BUILDS = {"GRCh38": "hg38", "GRCh37": "hg19", CHM13: "t2t"}


def _apply_forced_assembly(lib: ReferenceLibrary, vep_assembly: str | None) -> None:
    """Make the library agree with the explicit assembly override."""
    if not vep_assembly:
        return
    forced_build = _ASSEMBLY_BUILDS[vep_assembly]
    lib.build = forced_build
    if not lib.fasta:
        return
    fasta_build = build_from_path(Path(lib.fasta).name)
    if fasta_build and fasta_build != forced_build:
        logging.warning(
            f"Discarding auto-resolved FASTA '{lib.fasta}' because it conflicts "
            f"with --assembly {vep_assembly}"
        )
        lib.fasta = None


def _call_variants_for_vep(
    params: VepRunParams,
    bam_path: str,
    ref: str,
    lib: ReferenceLibrary,
    threads: str,
    out_vcf: str,
) -> None:
    region_args = ["-r", params.region] if params.region else []
    if params.ploidy_file:
        ploidy_args = ["--ploidy-file", str(params.ploidy_file)]
    elif params.ploidy:
        ploidy_args = ["--ploidy", params.ploidy]
    elif lib.ploidy_file:
        ploidy_args = ["--ploidy-file", lib.ploidy_file]
    else:
        build = canonical_build(lib.build) or "hg19"
        ploidy_args = ["--ploidy", ploidy_for_build(build)]

    with sex_samples_args(
        bam_path,
        str(Path(out_vcf).parent),
        ploidy_args,
        region=params.region,
        reference=ref,
    ) as sex_args:
        run_pipeline(
            [
                ["bcftools", "mpileup", "-B", "-C", "50", "-f", ref, "-Ou"]
                + region_args
                + [bam_path],
                ["bcftools", "call", *ploidy_args, *sex_args, "-mv", "-P", "0"]
                + ["--threads", threads, "-Ou"],
                *indel_cleanup_pipeline(ref, out_vcf, threads, mixed=True),
            ],
            description="Variant calling for VEP",
        )
    ensure_vcf_indexed(out_vcf)


def _build_vep_command(
    params: VepRunParams,
    vep_input_vcf: str,
    output_file: str,
    vcf_type: str,
    lib: ReferenceLibrary,
    threads: str,
) -> list[str]:
    engine = vep_engine(params.engine)
    # One element: run_command splits wrapper commands itself and keeps a path
    # with spaces whole.
    vep_cmd = [get_tool_path(engine.tool) or engine.tool]
    vep_cmd += ["-i", vep_input_vcf, "-o", output_file, "--fork", threads]
    vep_cmd.append({"vcf": "--vcf", "json": "--json", "tab": "--tab"}[params.format])

    # Without a reference library, the VCF's own contigs name the assembly.
    assembly = (
        params.vep_assembly
        or _assembly_of(lib.build)
        or _assembly_of(get_vcf_build(vep_input_vcf))
    )
    require_assembly(engine, assembly)
    if assembly:
        vep_cmd += ["--assembly", assembly]

    if lib.fasta:
        vep_cmd += ["--fasta", lib.fasta]

    raw_args = shlex.split(params.vep_args) if params.vep_args else []
    cache_dir = _vep_cache_dir(params, lib, engine)
    _check_annotation_cache(
        engine, cache_dir, params.vep_cache_version, assembly, raw_args
    )
    vep_cmd += [
        "--offline", "--dir_cache", str(cache_dir), "--cache_version", params.vep_cache_version,
    ]  # fmt: skip
    logging.info(LOG_MESSAGES["vep_using_cache"].format(path=cache_dir))

    vep_cmd += VEP_PRESETS[params.preset]
    if vcf_type in ("sv", "cnv"):
        vep_cmd += ["--overlaps", "--max_sv_size", "300000000", "--buffer_size", "500"]
    return vep_cmd + raw_args


def _assembly_of(build: str | None) -> str | None:
    """VEP's name for a reference build, if it has one."""
    canonical = canonical_build(build)
    return next((a for a, b in _ASSEMBLY_BUILDS.items() if b == canonical), None)


def _vep_cache_dir(
    params: VepRunParams, lib: ReferenceLibrary, engine: VepEngine
) -> Path:
    candidates = [
        engine.cache_root(Path(c))
        for c in (params.vep_cache, lib.vep_cache, expand_path("~/.vep"))
        if c
    ]
    return next((c for c in candidates if c.is_dir()), candidates[0])


def _check_annotation_cache(
    engine: VepEngine,
    cache_dir: Path,
    version: str,
    assembly: str | None,
    raw_args: list[str],
) -> None:
    species = "homo_sapiens"
    suffix = ""
    if engine.name == "ensembl":
        # Ensembl's raw, local-only flags are last on argv, so its cache
        # overrides win. Keep passing the args through unchanged.
        overrides = {}
        for i, token in enumerate(raw_args):
            flag, equals, value = token.lstrip("-").partition("=")
            flag = flag.lower()
            if token.startswith("-") and flag in {"refseq", "merged"}:
                suffix = f"_{flag}"
            if token.startswith("-") and flag in {
                "assembly", "cache_version", "dir_cache", "species",
            }:  # fmt: skip
                if equals:
                    overrides[flag] = value
                elif i + 1 < len(raw_args):
                    overrides[flag] = raw_args[i + 1]
        version = overrides.get("cache_version", version)
        assembly = overrides.get("assembly", assembly)
        cache_dir = Path(overrides.get("dir_cache", str(cache_dir))).expanduser()
        species = overrides.get("species", species)
        if not re.fullmatch(r"[0-9]+", version) or not all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in (".", "..")
            for part in (species, assembly or "unknown")
        ):
            raise WGSExtractError("Invalid Ensembl VEP cache options.")

    species_dir = cache_dir / (species + suffix)
    candidates = (
        [species_dir / f"{version}_{assembly}"]
        if assembly
        else species_dir.glob(f"{version}_*")
    )
    installed = [
        path
        for path in candidates
        if path.is_dir()
        and (
            engine.name != "ensembl"
            or (
                (path / "info.txt").is_file()
                and any(child.is_dir() for child in path.iterdir())
            )
        )
    ]
    if installed:
        if engine.name == "vep-rs" and len(installed) == 1:
            _warn_about_cache_gaps(installed[0])
        return
    location = species_dir / f"{version}_{assembly}" if assembly else species_dir
    assembly_arg = f" --assembly {assembly}" if assembly else ""
    species_arg = f" --species {species}" if species != "homo_sapiens" else ""
    cache_root = cache_dir.parent if engine.cache_subdir else cache_dir
    raise _VepCacheMissingError(
        f"Missing or incomplete {engine.label} cache for release {version} in {location}. "
        f"Run `wgsextract vep download --engine {engine.name} "
        f"--vep-version {version}{assembly_arg}{species_arg} --vep-cache {shlex.quote(str(cache_root))}` "
        "before annotating, or select "
        "an installed cache with --vep-cache and --vep-cache-version.",
    )


def _warn_about_cache_gaps(version_dir: Path) -> None:
    """Say which columns a vep-rs cache leaves empty that Ensembl VEP's
    cache fills: known variants without its variations, SIFT and PolyPhen
    without their prediction matrices."""
    try:
        info = json.loads(Path(version_dir, "info.json").read_text())
    except (OSError, ValueError):
        info = {}
    gaps = []
    if not Path(version_dir, "variations").is_dir():
        gaps.append("Existing_variation and the frequency columns")
    if not (info.get("sift") and info.get("polyphen")):
        gaps.append("SIFT and PolyPhen")
    if gaps:
        warn(
            "vep_cache_gaps",
            f"The vep-rs cache in {version_dir} has no data for "
            f"{' or '.join(gaps)}; those columns stay empty.",
            path=str(version_dir),
        )


def _prepare_vcf_input(params: VepRunParams, vcf: str, work: str, threads: str) -> str:
    """The VCF VEP reads: BCF converted to VCF, and cut to --region if given."""
    if not params.region and not vcf.lower().endswith(".bcf"):
        return vcf
    out = str(Path(work, "input.vcf.gz"))
    region = ["-r", params.region] if params.region else []
    run_command(
        ["bcftools", "view", *region, "--threads", threads, "-Oz", "-o", out, vcf]
    )
    return out


def _run_vep_on_input(
    params: VepRunParams, current_input: str, threads: str
) -> tuple[str, str]:
    """Annotate one input; returns a (status, duration) batch summary row."""
    is_bam = current_input.lower().endswith((".bam", ".cram"))
    outdir = str(params.outdir or absolute_path(current_input).parent)
    Path(outdir).mkdir(parents=True, exist_ok=True)
    logging.debug(f"Input file: {str(absolute_path(current_input))}")
    logging.debug(f"Output directory: {str(absolute_path(outdir))}")

    output_file, final_output = vep_output_paths(current_input, outdir, params.format)
    if Path(final_output).exists() and not params.force:
        message = LOG_MESSAGES["vep_skipping_exists"].format(
            input=current_input, output=final_output
        )
        logging.info(message)
        current_result().warn("output_exists", message, path=final_output)
        _record_vep_outputs(output_file, final_output, params.format)
        return "SKIPPED", "-"

    start_time = time.time()
    try:
        # Inside the try: one bad file fails its own row, not the whole batch.
        if not is_bam:
            ensure_vcf_indexed(current_input)
        md5_sig = calculate_bam_md5(current_input, None) if is_bam else None
        ref = str(params.ref) if params.ref else None
        lib = ReferenceLibrary(ref, md5_sig, input_path=current_input)
        _apply_forced_assembly(lib, params.vep_assembly)
        logging.debug(f"Resolved reference: {lib.fasta}")
        if is_bam and not lib.fasta:
            raise WGSExtractError(
                LOG_MESSAGES["ref_required_for"].format(
                    task="variant calling from BAM/CRAM"
                )
            )
        if is_bam and params.engine == "ensembl":
            # Don't call a whole genome only to discover its cache is missing.
            engine = vep_engine(params.engine)
            _check_annotation_cache(
                engine,
                _vep_cache_dir(params, lib, engine),
                params.vep_cache_version,
                params.vep_assembly or _assembly_of(lib.build),
                shlex.split(params.vep_args) if params.vep_args else [],
            )

        with tempfile.TemporaryDirectory(
            dir=scratch_parent(outdir), prefix=".wgse_vep_"
        ) as work:
            vep_input_vcf = current_input
            if not is_bam:
                vep_input_vcf = _prepare_vcf_input(params, current_input, work, threads)
            if is_bam:
                assert lib.fasta is not None
                logging.info(LOG_MESSAGES["vep_calling_pre"])
                vep_input_vcf = str(Path(work, "variants.vcf.gz"))
                _call_variants_for_vep(
                    params, current_input, lib.fasta, lib, threads, vep_input_vcf
                )

            if params.add_chr:
                chr_vcf = str(Path(work, "with_chr.vcf"))
                preprocess_vcf_chr_prefix(vep_input_vcf, chr_vcf)
                vep_input_vcf = chr_vcf

            vcf_type = (
                detect_vcf_type(current_input)
                if params.vcf_type == "auto"
                else params.vcf_type
            )
            # Written in work and moved into place once complete, so a failed
            # run leaves nothing that a rerun would skip as done.
            vep_output = str(Path(work, "vep" + Path(final_output).suffix))
            vep_cmd = _build_vep_command(
                params, vep_input_vcf, vep_output, vcf_type, lib, threads
            )
            if params.format == "vcf":
                # bgzipped as it's written: a genome's uncompressed VEP VCF is
                # ~25 GB.
                vep_cmd += ["--compress_output", "bgzip"]
            logging.info(LOG_MESSAGES["vep_running"].format(command=" ".join(vep_cmd)))
            run_command(vep_cmd)

            if params.format == "vcf":
                run_command(["tabix", "-f", "-p", "vcf", vep_output])
            with atomic_output(final_output) as staged:
                move_file(vep_output, staged)
                if params.format == "vcf":
                    move_file(vep_output + ".tbi", staged + ".tbi")
            # VEP's report files go beside the output.
            for suffix in ("_summary.html", "_warnings.txt"):
                if Path(vep_output + suffix).exists():
                    move_file(vep_output + suffix, output_file + suffix)
    except _VepCacheMissingError:
        raise
    except (OSError, subprocess.SubprocessError, WGSExtractError, ValueError) as e:
        checkpoint()  # a cancelled run stops here, not at the next input
        logging.error(f"VEP failed for {current_input}: {e}")
        current_result().warn(
            "input_failed", f"VEP failed for {current_input}: {e}", path=current_input
        )
        return "FAILED", "-"

    logging.info(LOG_MESSAGES["vep_complete"].format(path=final_output))
    _record_vep_outputs(output_file, final_output, params.format)
    return "SUCCESS", str(timedelta(seconds=int(time.time() - start_time)))


def _record_vep_outputs(output_file: str, final_output: str, fmt: str) -> None:
    result = current_result()
    metrics = {"variants": vcf_record_count(final_output)} if fmt == "vcf" else None
    result.add_output(final_output, "vep_annotated", metrics=metrics)
    for suffix, role in (
        ("_summary.html", "vep_summary"),
        ("_warnings.txt", "vep_warnings"),
    ):
        if Path(output_file + suffix).is_file():
            result.add_output(output_file + suffix, role)


def _expanded(params: VepRunParams) -> VepRunParams:
    """``params`` with a leading ``~`` expanded in every path."""
    paths: dict[str, Any] = {
        name: Path(expand_path(value))
        for name in ("input", "ref", "outdir", "vep_cache", "ploidy_file")
        if (value := getattr(params, name)) is not None
    }
    return replace(params, **paths)


def _cache_roots(params: VepRunParams) -> list[Path]:
    """VEP cache folders a run may use: --vep-cache (or the configured one),
    the reference library's, then ~/.vep."""
    from ._vep_resources import vep_cache_root

    roots = [params.vep_cache, vep_cache_root(None, params.ref), "~/.vep"]
    return [Path(expand_path(r)) for r in roots if r]


def run_vep(params: VepRunParams) -> None:
    params = _expanded(params)
    input_files = _vep_input_files(str(params.input))
    if params.input.is_dir():
        current_result().set_manifest_stem(Path(params.outdir or params.input, "vep"))
        for path in input_files:
            current_result().add_input(path, "batch_input")

    engine, notice = resolve_engine(params.engine, _cache_roots(params))
    if notice:
        logging.warning(notice)
        current_result().warn("vep_engine_fallback", notice, engine=engine.name)
    params = replace(params, engine=engine.name)
    require_assembly(engine, params.vep_assembly)
    refuse_unsupported_args(engine, params.vep_args)
    preflight_resources(lambda: estimate_vep_run(engine.name))
    tool = "vep-rs" if engine.name == "vep-rs" else "vep"
    verify_dependencies([tool, "tabix", "bcftools"])
    log_dependency_info([tool, "tabix", "bcftools"])
    current_result().add_metrics(engine=engine.name)
    threads, _ = get_resource_defaults(params.threads, None)

    batch_stats = []
    cache_error: _VepCacheMissingError | None = None
    for path in input_files:
        checkpoint()
        try:
            status, duration = _run_vep_on_input(params, path, threads)
        except _VepCacheMissingError as error:
            checkpoint()
            cache_error = cache_error or error
            current_result().warn(error.code, str(error), path=path)
            status, duration = "FAILED", "-"
        batch_stats.append((Path(path).name, status, duration))
    statuses = [status for _, status, _ in batch_stats]
    current_result().add_metrics(
        inputs=len(input_files),
        succeeded=statuses.count("SUCCESS"),
        skipped=statuses.count("SKIPPED"),
        failed=statuses.count("FAILED"),
    )
    if len(input_files) > 1:
        human_output(f"\n{LOG_MESSAGES['vep_batch_summary']}")
        human_output("-" * 60)
        for fname, status, dur in batch_stats:
            human_output(
                LOG_MESSAGES["vep_batch_item"].format(
                    filename=fname, status=status, duration=dur
                )
            )
        human_output("-" * 60)

    if cache_error:
        raise cache_error
    if "FAILED" in statuses:
        raise WGSExtractError("One or more VEP tasks failed.")
