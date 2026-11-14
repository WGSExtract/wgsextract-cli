"""Clair3 small-variant calling for Oxford Nanopore reads (optional caller)."""

import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import sys
import tempfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.vcf import VcfClair3Params
from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.atomic_output import safe_output
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.containers import (
    image_record,
    link_chain,
    require_image,
    run_container,
)
from wgsextract_cli.core.cram_staging import stage_cram30
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import (
    get_tool_version,
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.genotype_qc import (
    AUTOSOMES,
    CHR_X,
    CHR_Y,
    contigs_with_reads,
    covered_depths,
    region_targets,
)
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.output_checks import warn_if_no_variants
from wgsextract_cli.core.parabricks import gpu_memory, verify_gpu_docker
from wgsextract_cli.core.paths import absolute_path, expand_path
from wgsextract_cli.core.pixi_tools import pixi_env_prefix
from wgsextract_cli.core.reference_assets import read_fai_lengths
from wgsextract_cli.core.region_selection import Intervals, selected_intervals
from wgsextract_cli.core.resource_estimates import (
    CLAIR3_MEMORY,
    HostRequirement,
    calling_hours,
    check_host_requirement,
    estimate_clair3,
    report_resources,
)
from wgsextract_cli.core.space_free_paths import (
    READS_INDEXES,
    REFERENCE_INDEXES,
    restore_vcf_paths,
    space_free_paths,
)
from wgsextract_cli.core.tool_catalog import clair3_host_supported
from wgsextract_cli.core.ultima import read_groups
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command
from wgsextract_cli.core.variant_files import ensure_vcf_indexed

from .basic import plain_reference, record_variants, setup_caller
from .callers import write_region_bed

CLAIR3 = "run_clair3.sh"
# HKU's CUDA build of the same Clair3 for --gpu, pinned to the build validated
# in #639: Bioconda's Linux Clair3 depends on pytorch-cpu, so its --use_gpu
# falls back to the CPU.
DEFAULT_GPU_IMAGE = (
    "hkubal/clair3:v2.0.5_gpu"
    "@sha256:3f660a693142db0aa8361a58fb6379fc6e3eeead20916eaae5614bd00d21cbeb"
)
DEFAULT_GPU_VERSION = (2, 0, 5)
GPU_CLAIR3 = "/opt/bin/run_clair3.sh"
_CUDA_PROBE = "import torch; print(torch.cuda.is_available())"
# Clair3 starts a full-alignment GPU worker only on a GPU with this much free
# memory (CallVariantsFromCffiGPU); pileup workers need 5000 MiB.
GPU_FREE_MIB = 8000
_GPU_SETUP = (
    "vcf clair3 --gpu needs x86-64 Linux (or WSL2) with an NVIDIA GPU, Docker, "
    "the NVIDIA Container Toolkit and HKU's Clair3 GPU image: docker pull {image}"
)
MODEL_FILES = ("pileup.pt", "full_alignment.pt")
WINDOWS_UNSUPPORTED = (
    "Clair3 is not available on Windows (Bioconda builds it for Linux and macOS "
    "only). Run `vcf clair3` on Linux, for example under WSL2."
)
# Before 2.0.5, libclair3 linked its own htslib, whose CRAM 3.1 decoder fails
# on valid blocks (#604) that the host samtools reads; 2.0.5 bundles htslib
# 1.24, which reads them.
CRAM31_NATIVE = (2, 0, 5)
_CRAM31_BUG = "Clair3's bundled htslib can fail to decode valid CRAM 3.1 blocks (#604)."
_ERROR_LINE = re.compile(r"^\[E::|\[ERROR\]|^Traceback|^[\w.]*(Error|Exception):")
# Clair3 calls these unless given a BED (CheckEnvs' major_contigs).
_MAJOR_CONTIGS = frozenset(
    f"{prefix}{name}"
    for prefix in ("chr", "")
    for name in (*map(str, range(1, 23)), "X", "Y")
)
_FETCH = (
    "Download a model directory (pileup.pt and full_alignment.pt) from "
    "https://www.bio8.cs.hku.hk/clair3/clair3_models_rerio_pytorch/ and pass "
    "it with --model or clair3_model in config.toml; see docs/ont.md."
)


@dataclass(frozen=True)
class Chemistry:
    """Pore/kit/speed must match the model; basecaller accuracy/version should."""

    pore: str
    kit: str | None = None
    speed: str | None = None
    accuracy: str | None = None
    version: str | None = None

    def key(self) -> tuple[str, str | None, str | None]:
        # R9.4.1 models are trained across kits and speeds.
        if self.pore == "941":
            return (self.pore, None, None)
        return (self.pore, self.kit, self.speed)


# Dorado (dna_r10.4.1_e8.2_400bps_sup@v4.1.0) and Guppy (dna_r9.4.1_450bps_hac)
# basecaller model names, as recorded in @RG DS / ID and @PG.
_BASECALLER = re.compile(
    r"dna_r(?P<pore>\d+(?:\.\d+)+)(?:_e(?P<kit>\d+(?:\.\d+)*))?(?:_\d+khz)?"
    r"(?:_(?P<speed>\d+)bps)?(?:_\d+khz)?_(?P<accuracy>fast|hac|sup)"
    r"(?:@v(?P<version>\d+(?:\.\d+)*))?",
    re.IGNORECASE,
)
_MODEL = re.compile(
    r"^r(?P<pore>\d+)(?:_e(?P<kit>\d+))?(?:_(?P<speed>\d+)bps)?(?:_prom)?"
    r"_(?P<accuracy>fast|hac|sup)(?:_(?P<version>[vg][0-9g+]+))?$"
)


def basecaller_chemistries(header: str) -> set[Chemistry]:
    """Basecaller models named in the @RG and @PG header lines."""
    found = set()
    for line in header.splitlines():
        if not line.startswith(("@RG\t", "@PG\t")):
            continue
        for match in _BASECALLER.finditer(line):
            version = match["version"]
            found.add(
                Chemistry(
                    pore=match["pore"].replace(".", ""),
                    kit=match["kit"].replace(".", "") if match["kit"] else None,
                    speed=match["speed"],
                    accuracy=match["accuracy"].lower(),
                    version=f"v{version.replace('.', '')}" if version else None,
                )
            )
    return found


def model_chemistry(name: str) -> Chemistry | None:
    """What a Clair3 ONT model name says it was trained on."""
    if name in ("ont", "ont_guppy5"):
        return Chemistry(pore="941")
    match = _MODEL.match(name)
    if not match:
        return None
    return Chemistry(
        pore=match["pore"],
        kit=match["kit"],
        speed=match["speed"],
        accuracy=match["accuracy"],
        version=match["version"],
    )


def model_for_chemistry(chemistry: Chemistry) -> str | None:
    """The Clair3 model trained for a basecaller, or None when unknown."""
    if chemistry.pore == "941":
        # HKU: also applicable to HAC and fast R9.4.1 reads.
        return "r941_prom_sup_g5014"
    if chemistry.pore == "104" and chemistry.kit == "81" and chemistry.accuracy:
        return f"r104_e81_{chemistry.accuracy}_g5015"
    if (
        chemistry.pore == "1041"
        and chemistry.kit == "82"
        and chemistry.accuracy
        and chemistry.version
    ):
        speed = chemistry.speed or "400"
        return f"r1041_e82_{speed}bps_{chemistry.accuracy}_{chemistry.version}"
    return None


def _chemistry_text(chemistry: Chemistry) -> str:
    pore = {"1041": "R10.4.1", "104": "R10.4", "941": "R9.4.1"}.get(
        chemistry.pore, f"r{chemistry.pore}"
    )
    parts = [pore]
    if chemistry.kit and chemistry.pore != "941":
        parts.append(f"E{chemistry.kit[0]}.{chemistry.kit[1:]}")
    if chemistry.speed and chemistry.pore != "941":
        parts.append(f"{chemistry.speed} bps")
    return " ".join(parts)


@lru_cache(maxsize=1)
def pinned_models() -> dict[str, dict[str, object]]:
    manifest = Path(__file__).resolve().parents[2] / "assets/clair3/models.json"
    models: dict[str, dict[str, object]] = json.loads(manifest.read_text("utf-8"))[
        "models"
    ]
    return models


def require_supported_platform() -> None:
    if sys.platform == "win32":
        raise WGSExtractError(WINDOWS_UNSUPPORTED)
    if not clair3_host_supported():
        raise WGSExtractError(
            "Clair3 runs on Linux (x86-64, ARM64) and Apple Silicon macOS only; "
            "Bioconda has no Clair3 2.x build for this host. Use `vcf deepvariant` "
            "(the ONT default) here."
        )


def clair3_gpu_image() -> str:
    return str(settings.get("clair3_gpu_image") or DEFAULT_GPU_IMAGE)


@dataclass(frozen=True)
class GpuSetup:
    """What `vcf clair3 --gpu` runs: the image, the Clair3 in it (None when
    unknown) and the GPUs (nvidia-smi indexes) with room for its workers."""

    image: str
    version: tuple[int, ...] | None
    devices: tuple[int, ...]


def _gpus_with_room() -> tuple[int, ...]:
    """The GPUs with the memory Clair3 starts a worker on. Clair3 reads every
    GPU it is given from nvidia-smi and fails on one that reports none (N/A),
    so it is given only these."""
    gpus = gpu_memory()
    need = GPU_FREE_MIB * 1024**2
    devices = tuple(
        gpu.index
        for gpu in gpus
        if gpu.free_bytes is not None and gpu.free_bytes >= need
    )
    if devices:
        return devices
    held = ", ".join(
        f"GPU {gpu.index}: "
        + (
            "free memory unknown (N/A)"
            if gpu.free_bytes is None
            else f"{gpu.free_bytes / 1024**3:.1f} GiB free"
        )
        for gpu in gpus
    )
    raise WGSExtractError(
        f"Clair3 on the GPU needs a GPU that nvidia-smi reports {GPU_FREE_MIB / 1024:.1f}"
        f" GiB free on ({held or 'nvidia-smi reported no GPU memory'}). Wait for "
        "other GPU programs to finish, or run without --gpu."
    )


def _require_cuda_torch(image: str) -> None:
    """Clair3 quietly runs on the CPU when its PyTorch can't use CUDA (a CPU
    build, or one too new for the driver), so check the image's first."""
    try:
        probe = run_container(
            image, ["python", "-c", _CUDA_PROBE], [],
            gpus=True, local_only=True, capture_output=True,
        )  # fmt: skip
        found = probe.stdout.split()[-1:]
    except (OSError, subprocess.SubprocessError) as error:
        stderr = getattr(error, "stderr", "") or ""
        detail = stderr.strip().splitlines()[-1:] or [str(error)]
        raise WGSExtractError(
            f"Couldn't check PyTorch's CUDA support in {image}: {detail[0]}"
        ) from error
    if found != ["True"]:
        raise WGSExtractError(
            f"PyTorch in {image} can't use the GPU (torch.cuda.is_available() is "
            "False), so Clair3 would run on the CPU. Use a Clair3 GPU image built "
            "for this NVIDIA driver (clair3_gpu_image in config.toml), or run "
            "without --gpu."
        )


def _image_clair3_version(image: str) -> tuple[int, ...] | None:
    """The Clair3 version in ``image``: the pinned default's is known, others
    are asked (their tags need not name it); None if they can't say."""
    if image == DEFAULT_GPU_IMAGE:
        return DEFAULT_GPU_VERSION
    try:
        result = run_container(
            image, [GPU_CLAIR3, "-v"], [], local_only=True, capture_output=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # NVIDIA's CUDA images print their CUDA version first.
    named = re.search(r"Clair3 v?(\d+\.\d+\.\d+)", result.stdout or "")
    return _parse_version(named[1]) if named else None


def verify_gpu() -> GpuSetup:
    """What --gpu runs on, once this host can run Clair3 on a GPU with the
    memory it asks for; else name what's missing."""
    image = clair3_gpu_image()
    setup = _GPU_SETUP.format(image=image)
    machine = platform.machine().lower()
    if not sys.platform.startswith("linux") or machine not in ("x86_64", "amd64"):
        raise UsageError(
            "vcf clair3 --gpu runs on x86-64 Linux with an NVIDIA GPU only; Clair3 "
            "has no Apple GPU (MPS) or ARM64 GPU build. Run without --gpu here."
        )
    verify_gpu_docker(setup)
    require_image(
        image,
        f"The Clair3 GPU image isn't downloaded: docker pull {image} "
        "(set clair3_gpu_image in config.toml for another build).",
    )
    devices = _gpus_with_room()
    _require_cuda_torch(image)
    return GpuSetup(image, _image_clair3_version(image), devices)


def _parse_version(text: str | None) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(part) for part in match.groups()) if match else None


def clair3_version() -> tuple[int, ...] | None:
    """The installed Clair3's version (``Clair3 v2.0.5``), or None if unknown."""
    return _parse_version(get_tool_version(CLAIR3))


def needs_cram30_staging(version: tuple[int, ...] | None) -> bool:
    """Older (or unidentified) Clair3 builds get a CRAM 3.0 copy of CRAM 3.1."""
    return version is None or version < CRAM31_NATIVE


def bundled_models_dir() -> Path | None:
    """Bioconda ships its pre-trained models in the package's bin/models."""
    tool = get_tool_path(CLAIR3)
    candidates = []
    if tool and Path(tool).is_file():
        candidates += [
            Path(tool).parent / "models",
            Path(tool).resolve().parent / "models",
        ]
    elif prefix := pixi_env_prefix("clair3"):
        candidates.append(prefix / "bin" / "models")
    return next((path for path in candidates if path.is_dir()), None)


def _fetch_hint(name: str) -> str:
    pin = pinned_models().get(name)
    if not pin:
        return _FETCH
    return (
        f"Download {name} ({', '.join(MODEL_FILES)}) from {pin['url']} into a "
        f"directory named {name} and pass it with --model or clair3_model in "
        "config.toml; see docs/ont.md."
    )


def resolve_model(value: str, bundled: Path | None) -> Path:
    """A model directory, or the name of one Clair3 bundles."""
    # Normalised, so "." and ".." name the real model folder.
    path = Path(os.path.abspath(expand_path(value)))
    if path.is_dir():
        model = path
    elif bundled and "/" not in value and (bundled / value).is_dir():
        model = bundled / value
    else:
        raise WGSExtractError(
            f"Clair3 model {value} isn't installed. {_fetch_hint(path.name)}"
        )
    missing = [name for name in MODEL_FILES if not (model / name).is_file()]
    if missing:
        raise WGSExtractError(
            f"Clair3 model {model} lacks {', '.join(missing)}; Clair3 2.x needs "
            f"PyTorch models. {_fetch_hint(model.name)}"
        )
    return model


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model(model: Path) -> None:
    """Check a known model against its pinned SHA-256 digests."""
    pin = pinned_models().get(model.name)
    if pin is None:
        logging.warning(
            f"Clair3 model {model.name} has no pinned checksum; its integrity and "
            "chemistry are unverified."
        )
        return
    expected = pin["files"]
    assert isinstance(expected, dict)
    for name, digest in expected.items():
        if _sha256(model / name) != digest:
            raise WGSExtractError(
                f"Clair3 model file {model / name} doesn't match its pinned "
                f"SHA-256; the download is corrupt or not {model.name}. "
                f"{_fetch_hint(model.name)}"
            )
    logging.info(f"Verified Clair3 model {model.name} (SHA-256).")


def check_model_matches(model_name: str, detected: set[Chemistry]) -> None:
    """Refuse a model for another chemistry; warn on another basecaller."""
    if model_name.startswith(("hifi", "ilmn")):
        raise UsageError(
            f"Clair3 model {model_name} is not an ONT model; `vcf clair3` calls "
            "Nanopore reads."
        )
    if "with_mv" in model_name:
        raise UsageError(
            f"Clair3 model {model_name} needs Dorado move tables (mv tags) and "
            "--enable_dwell_time, which this command does not use; choose the "
            "matching model without _with_mv."
        )
    trained = model_chemistry(model_name)
    if trained is None or not detected:
        return
    for chemistry in detected:
        model_key = trained.key()
        reads_key = chemistry.key()
        if any(
            mine is not None and theirs is not None and mine != theirs
            for mine, theirs in zip(model_key, reads_key, strict=True)
        ):
            raise WGSExtractError(
                f"Clair3 model {model_name} is for {_chemistry_text(trained)} reads, "
                f"but the basecaller metadata says {_chemistry_text(chemistry)}. "
                f"Use {model_for_chemistry(chemistry) or 'a model for that chemistry'}."
            )
        if trained.pore == "941":
            continue
        for field in ("accuracy", "version"):
            mine, theirs = getattr(trained, field), getattr(chemistry, field)
            if mine and theirs and mine != theirs:
                logging.warning(
                    f"Clair3 model {model_name} was trained for a different basecaller "
                    f"{field} ({mine}) than these reads ({theirs}); accuracy may "
                    f"suffer. Expected model: {model_for_chemistry(chemistry)}."
                )
                break


def select_model(choice: str | None, header: str) -> Path:
    """``choice`` (--model), then clair3_model in config.toml, then the
    basecaller metadata."""
    detected = basecaller_chemistries(header)
    bundled = bundled_models_dir()
    value = choice or settings.get("clair3_model")
    if not value:
        names = {model_for_chemistry(chemistry) for chemistry in detected}
        if len(names) != 1 or None in names:
            reason = (
                "the reads come from several basecaller models"
                if len(detected) > 1
                else "the BAM/CRAM header doesn't name a known basecaller model"
            )
            available = ", ".join(
                sorted(
                    p.name
                    for p in (bundled.iterdir() if bundled else ())
                    if p.is_dir() and model_chemistry(p.name)
                )
            )
            raise WGSExtractError(
                f"Can't choose a Clair3 model: {reason}. Pass --model NAME|DIR "
                f"(or set clair3_model in config.toml) for the basecaller that made "
                f"these reads. Bundled ONT models: {available or 'none found'}."
            )
        value = str(names.pop())
        logging.info(f"Basecaller metadata selects Clair3 model {value}.")
    model = resolve_model(str(value), bundled)
    check_model_matches(model.name, detected)
    verify_model(model)
    return model


def sample_name(header: str, reads: str) -> str:
    """The reads' @RG SM (Clair3 would otherwise write "SAMPLE")."""
    samples = {group["SM"] for group in read_groups(header) if group.get("SM")}
    if len(samples) > 1:
        raise WGSExtractError(
            f"The read groups name several samples ({', '.join(sorted(samples))}); "
            "Clair3 calls one sample per run. Split the BAM/CRAM by sample first."
        )
    if samples:
        return samples.pop()
    name = Path(reads).name.split(".")[0]
    logging.warning(f"No @RG SM sample name in {reads}; naming the sample {name}.")
    return name


def require_ont_read_groups(header: str) -> None:
    """Reads from other platforms must not enter an ONT model."""
    platforms = {
        group["PL"].upper() for group in read_groups(header) if group.get("PL")
    }
    if platforms - {"ONT"}:
        raise WGSExtractError(
            f"The read groups name platform(s) {', '.join(sorted(platforms))}; "
            "`vcf clair3` calls Nanopore (PL:ONT) reads only. Use `vcf deepvariant` "
            "for other platforms, or split out the ONT read groups."
        )


def clair3_command(
    reads: str,
    ref: str,
    model: Path,
    threads: str,
    output: str,
    bed: str | None,
    sample: str,
    gpu_devices: tuple[int, ...] = (),
) -> list[str]:
    command = [
        GPU_CLAIR3 if gpu_devices else CLAIR3,
        f"--bam_fn={reads}",
        f"--ref_fn={ref}",
        f"--threads={threads}",
        "--platform=ont",
        f"--model_path={model}",
        f"--sample_name={sample}",
        f"--output={output}",
    ]
    if bed:
        command.append(f"--bed_fn={bed}")
    if gpu_devices:
        devices = ",".join(str(device) for device in gpu_devices)
        command += ["--use_gpu=True", f"--device=cuda:{devices}"]
    return command


def container_paths(paths: list[str]) -> list[str]:
    """``paths`` and their index files, with each link's chain to its target,
    so a container that mounts their folders opens them all."""
    found = []
    for path in paths:
        candidates = [path]
        candidates += [f"{path}{ext}" for ext in (*READS_INDEXES, *REFERENCE_INDEXES)]
        if Path(path).suffix.lower() in (".bam", ".cram"):
            candidates += [str(Path(path).with_suffix(ext)) for ext in READS_INDEXES]
        for candidate in candidates:
            if candidate == path or Path(candidate).exists():
                found += link_chain(candidate)
    return found


def space_free_run_paths(
    reads: str, ref: str, model: Path, work: str
) -> AbstractContextManager[list[str]]:
    """The reads, reference, model and work folder, as space-free links where
    they have spaces: Clair3's shell and GNU parallel steps split paths at
    spaces (#623). Clair3 reads the model's chemistry from its folder name,
    which the link keeps."""
    model_name = re.sub(r"\s", "_", model.name) or "model"
    return space_free_paths(
        "Clair3",
        [
            (reads, f"reads{''.join(Path(reads).suffixes[-1:])}", READS_INDEXES),
            (ref, "ref.fa", REFERENCE_INDEXES),
            (model, f"model/{model_name}", ()),
            (work, "work", ()),
        ],
    )


def _clair3_env() -> dict[str, str]:
    # Clair3 runs its own Python; don't leak ours into it.
    env = dict(os.environ)
    for var in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(var, None)
    return env


def clair3_errors(output: Path, limit: int = 8) -> str:
    """Distinct error lines from Clair3's logs: it reports them on stdout
    (tee'd to ``output/run_clair3.log`` and the step logs in ``output/log``),
    so they never reach the failed command's stderr."""
    seen: dict[str, None] = {}
    logs = [output / "run_clair3.log", *sorted(output.glob("log/*.log"))]
    for log in logs:
        if not log.is_file() or log.name.startswith("parallel_"):
            continue  # GNU parallel job tables.
        for line in log.read_text(errors="replace").splitlines():
            line = line.strip()
            if _ERROR_LINE.search(line):
                seen.setdefault(line, None)
    return "\n".join(list(seen)[:limit])


def called_bases(reads: str, ref: str, intervals: Intervals | None) -> int:
    """Reference bases Clair3 calls: the region's, else chr1-22, X and Y
    (its default), on the contigs the index places reads on (all of them
    when the index can't say)."""
    try:
        with_reads = contigs_with_reads(reads, ref)
    except (OSError, ValueError, subprocess.SubprocessError, WGSExtractError) as e:
        logging.debug(f"Could not list the contigs with reads in {reads}: {e}")
        with_reads = {}
    if intervals:
        return sum(
            end - start
            for contig, ranges in intervals.items()
            if not with_reads or contig in with_reads
            for start, end in ranges
        )
    lengths = with_reads or read_fai_lengths(ref)
    return sum(n for contig, n in lengths.items() if contig in _MAJOR_CONTIGS)


def mean_depth(reads: str, ref: str, region: str | None) -> float | None:
    """The reads' sampled mean depth: over ``region`` (its shallowest
    chromosome group, such as a man's chrX), else over the bases they cover,
    autosomes first. None when it can't be sampled."""
    try:
        if region:
            found = covered_depths(
                reads, region_targets(region), region=region, ref=ref
            )
            if depth := min((d for d in found.values() if d > 0), default=0.0):
                return depth
        depths = covered_depths(reads, (AUTOSOMES, CHR_X, CHR_Y), ref=ref)
    except (OSError, ValueError, WGSExtractError) as error:
        logging.debug(f"Could not sample the depth of {reads}: {error}")
        return None
    return depths.get(AUTOSOMES) or max(depths.values(), default=0.0) or None


@dataclass(frozen=True)
class TempSpace:
    """What Clair3's work folder holds at its peak: ``bases`` called at
    ``depth`` (None when unknown), on the GPU or not."""

    bases: int
    depth: float | None
    gpu: bool

    def check(self, outdir: str, threads: str, *, enforce: bool = True) -> None:
        """Report the peak beside the output with Clair3's host requirement,
        and refuse a run that wouldn't fit (#811, #690): it fails hours in
        otherwise."""
        estimate = estimate_clair3(outdir, self.bases, gpu=self.gpu, depth=self.depth)
        report_resources(estimate)
        (disk,) = estimate.resources
        caller = "clair3-gpu" if self.gpu else "clair3"
        check_host_requirement(
            HostRequirement(
                "Clair3",
                ((outdir, disk.estimated_bytes),),
                CLAIR3_MEMORY,
                int(threads),
                calling_hours(caller, self.bases, int(threads)),
            ),
            enforce=enforce,
            disk_advice="Free up space, choose an --outdir on a bigger disk or "
            "call a smaller --region.",
            memory_advice="Lower --threads.",
        )


def temp_space(
    reads: str, ref: str, intervals: Intervals | None, region: str | None, gpu: bool
) -> TempSpace | None:
    """Clair3's temporary space for ``reads``, or None when its called bases
    can't be counted."""
    try:
        bases = called_bases(reads, ref, intervals)
    except (OSError, ValueError) as e:
        logging.warning(f"Clair3's temporary space wasn't estimated: {e}")
        return None
    return TempSpace(bases, mean_depth(reads, ref, region) if gpu else None, gpu)


def run_clair3(params: VcfClair3Params) -> None:
    """``vcf clair3``: Clair3 on Nanopore reads into ``clair3.vcf.gz``."""
    require_supported_platform()
    gpu = verify_gpu() if params.gpu else None
    if gpu:
        verify_dependencies(["samtools", "bcftools", "tabix"])
        logging.info(
            f"Running Clair3 on GPU {', '.join(map(str, gpu.devices))} from "
            f"{image_record(gpu.image)['image']}."
        )
    else:
        verify_dependencies([CLAIR3, "samtools", "bcftools", "tabix"])
        log_dependency_info([CLAIR3])
    setup = setup_caller(params)
    threads, outdir, ref = setup.threads, setup.outdir, setup.ref
    input_file, region = str(params.input), params.region
    detected = setup.platform
    if detected not in (None, "ont"):
        raise WGSExtractError(
            f"The input looks like {detected} reads; `vcf clair3` calls Nanopore "
            "reads. Use `vcf deepvariant` for other platforms."
        )
    header = get_bam_header(input_file, ref)
    require_ont_read_groups(header)
    sample = sample_name(header, input_file)
    model = select_model(params.model, header)

    out_vcf = str(Path(outdir, "clair3.vcf.gz"))
    logging.info(LOG_MESSAGES["vcf_calling_clair3"].format(output=out_vcf))
    with (
        plain_reference(ref, outdir) as use_ref,
        tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_clair3_") as work,
    ):
        bed, intervals = None, None
        if region:
            lengths = read_fai_lengths(use_ref)
            bed = str(Path(work, "regions.bed"))
            write_region_bed(region, lengths, bed)
            try:
                intervals = selected_intervals(region, lengths, commas=True)
            except (ValueError, OSError) as error:
                raise WGSExtractError(
                    f"Invalid Clair3 region selection: {region}: {error}"
                ) from error
        space = temp_space(input_file, use_ref, intervals, region, gpu is not None)
        if space:
            space.check(outdir, threads, enforce=not params.no_preflight)
        reads = input_file
        if needs_cram30_staging(gpu.version if gpu else clair3_version()):
            staging = Path(work, "reads")
            staging.mkdir()
            reads = stage_cram30(
                input_file, use_ref, str(staging), threads, intervals,
                tool="Clair3", why=_CRAM31_BUG,
            )  # fmt: skip
            if space and reads != input_file:
                # The staged copy stays beside the tensors while Clair3 runs.
                space.check(outdir, threads, enforce=not params.no_preflight)
        output = str(Path(work, "clair3"))
        # Absolute, so the header's paths can be told apart and restored.
        abs_reads, abs_ref, abs_work = (
            str(absolute_path(p)) for p in (reads, use_ref, work)
        )
        with space_free_run_paths(abs_reads, abs_ref, model, abs_work) as links:
            run_reads, run_ref, run_model, run_work = links
            command = clair3_command(
                run_reads, run_ref, Path(run_model), threads,
                str(Path(run_work, "clair3")),
                str(Path(run_work, "regions.bed")) if bed else None, sample,
                gpu.devices if gpu else (),
            )  # fmt: skip
            try:
                if gpu:
                    weights = [str(Path(run_model, name)) for name in MODEL_FILES]
                    # Clair3 starts GPU workers by free GPU memory, not
                    # --threads; the CPU cap keeps it to the thread budget.
                    run_container(
                        gpu.image, command,
                        container_paths([run_reads, run_ref, run_model, *weights,
                                         run_work]),
                        gpus=True, cpus=int(threads), local_only=True,
                        temporary_directory=run_work,
                    )  # fmt: skip
                else:
                    # stdin closed: Clair3's GNU parallel would read it otherwise.
                    run_command(command, env=_clair3_env(), stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError) as e:
                errors = clair3_errors(Path(output))
                if not errors or not isinstance(e, subprocess.CalledProcessError):
                    raise WGSExtractError(f"Clair3 failed: {e}") from e
                stderr = (
                    e.stderr.decode(errors="replace")
                    if isinstance(e.stderr, bytes)
                    else (e.stderr or "")
                )
                # Clair3's stderr (when any) as run_command reports it, then its logs.
                reported = f"{e}\n" if stderr.strip() else f"exit {e.returncode}; "
                raise WGSExtractError(
                    f"Clair3 failed: {reported}Clair3's log errors:\n{errors}"
                ) from e
        merged = Path(output, "merge_output.vcf.gz")
        if not merged.is_file():
            raise WGSExtractError("Clair3 failed to produce merge_output.vcf.gz.")
        # Name the user's files, not the deleted links, in the header.
        real_paths = {
            run_reads: input_file, run_ref: ref, run_model: str(model),
            run_work: work,
        }  # fmt: skip
        named = restore_vcf_paths(str(merged), real_paths, work)
        with safe_output(out_vcf) as staged:
            run_command(["bcftools", "view", "-Oz", "-o", staged, named])
    ensure_vcf_indexed(out_vcf)
    record_variants(out_vcf, "variants", warn_if_no_variants(out_vcf, ref, region))


cmd_clair3 = cli_handler(VcfClair3Params, run_clair3)
