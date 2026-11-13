"""WGS Extract's aligner builds (native/README.md): wgse-bwa, wgse-bwa-mem2
and wgse-minimap2 write their upstream aligner's alignments, faster.
wgse-bwa-mem3 (bwa-mem3) writes bwa's or bwa-mem2's exact records with
``--compat``, faster still, and so can stand in for either. On Apple Silicon
all of them are built, and all but wgse-minimap2 can also run on the Metal GPU;
on Linux, wgse-bwa is, and on x86-64 wgse-bwa-mem3 and wgse-minimap2 too, all of
which can also run on an NVIDIA GPU (CUDA) there. They are used when installed on
PATH; they are never installed here.

Each tool is asked once per process what it can do (``capabilities --json``),
and anything unexpected falls back to the upstream aligner.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import UsageError

SETTINGS = ("auto", "off")
INSTALL_HINT = (
    "see the Installing section of native/README.md "
    "(https://github.com/WGSExtract/wgsextract-cli/blob/main/native/README.md#installing)"
)
# The upstream aligner each build replaces, and the oldest release used.
BINARIES = {
    "bwa": "wgse-bwa",
    "bwa-mem2": "wgse-bwa-mem2",
    "bwa-mem3": "wgse-bwa-mem3",
    "minimap2": "wgse-minimap2",
}
# The builds for Linux (from wgse-bwa 0.10.0: CUDA on x86-64, NEON on ARM64),
# and for x86-64 also wgse-bwa-mem3 and wgse-minimap2 (from 0.4.0, with CUDA).
LINUX_BINARIES = {"bwa": "wgse-bwa"}
LINUX_X86_64_BINARIES = {
    **LINUX_BINARIES,
    "bwa-mem3": "wgse-bwa-mem3",
    "minimap2": "wgse-minimap2",
}
MINIMUM_VERSIONS = {
    "wgse-bwa": (0, 7, 0),
    "wgse-bwa-mem2": (0, 4, 0),
    "wgse-bwa-mem3": (0, 2, 0),
    "wgse-minimap2": (0, 1, 0),
}
# bwa-mem3's --compat target that writes each upstream aligner's exact records
# (@PG aside; native/README.md).
BWA_MEM3_COMPAT = {"bwa": "bwa-mem", "bwa-mem2": "bwa-mem2"}
BWA_MEM3_FAST = "--fast"
# The oldest Bioconda bwa-mem3 that stands in on Linux (find_bwa_mem3): the
# release whose --compat records were checked against bwa's and bwa-mem2's.
BIOCONDA_BWA_MEM3_MINIMUM = (0, 16, 0)
# From this release wgse-bwa-mem2 maps (CPU and --metal) without bwa-mem2's
# 6.2 GB .0123 file (native/README.md, Memory).
BWA_MEM2_WITHOUT_0123 = (0, 5, 0)
# From this release wgse-minimap2 takes --backend auto (its gap fills on CUDA,
# native/README.md). Its Mac --metal stays unused here.
MINIMAP2_GPU_VERSION = (0, 4, 0)
# From this release wgse-bwa can automatically read dense suffix-array files.
DENSE_SA_VERSION = (0, 11, 0)
# Earlier production builds compiled the on/off option out of their parser.
DENSE_SA_OPTION_VERSION = (0, 12, 1)
CAPABILITIES_SCHEMA = 1
_PROBE_TIMEOUT_SECONDS = 30
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class NativeAlignerUnusable(Exception):
    """The installed build can't be used; the message says why."""


@dataclass(frozen=True)
class NativeAligner:
    """An installed build that passed its capabilities check."""

    tool: str  # the upstream aligner it builds (its capabilities' engine)
    binary: str
    command: str  # how to run it: its name on PATH
    path: str
    version: str
    engine_version: str
    metal_available: bool
    cuda_available: bool = False
    # A usable NVIDIA GPU, busy or not: capabilities are asked once per process,
    # and cuda_available also depends on the GPU's free memory at that moment.
    cuda_hardware_supported: bool = False

    @property
    def gpu(self) -> str | None:
        """The GPU backend it can run on (metal or cuda), if any."""
        if self.metal_available:
            return "metal"
        return "cuda" if self.cuda_available else None

    @property
    def minimap2_gpu(self) -> bool:
        """Whether this is a wgse-minimap2 whose long-read gap fills can run on
        an NVIDIA GPU with --backend auto. From its hardware support, not
        cuda_available: --backend auto checks the GPU's free memory itself, as
        each run starts, and otherwise aligns on the CPU."""
        version = _version_tuple(self.version)
        return (
            self.binary == "wgse-minimap2"
            and not self.metal_available
            and self.cuda_hardware_supported
            and version is not None
            and version >= MINIMAP2_GPU_VERSION
        )

    @property
    def reads_0123(self) -> bool:
        """Whether this bwa-mem2 build loads (and needs) the index's .0123 file."""
        version = _version_tuple(self.version)
        return not (
            self.binary == "wgse-bwa-mem2"
            and version is not None
            and version >= BWA_MEM2_WITHOUT_0123
        )

    @property
    def reads_dense_sa(self) -> bool:
        """Whether this is a wgse-bwa that reads dense suffix-array files."""
        version = _version_tuple(self.version)
        return (
            self.binary == "wgse-bwa"
            and version is not None
            and version >= DENSE_SA_VERSION
        )

    @property
    def supports_dense_sa_option(self) -> bool:
        """Whether the production parser accepts ``--dense-sa on|off``."""
        version = _version_tuple(self.version)
        return (
            self.binary == "wgse-bwa"
            and version is not None
            and version >= DENSE_SA_OPTION_VERSION
        )


@dataclass(frozen=True)
class AlignerCommand:
    """How to run an aligner: upstream ``tool`` itself, or ``native`` (on its
    GPU, Metal or CUDA, when ``gpu``) in its place. ``options`` are bwa-mem3's
    ``mem`` options that choose its output: ``--compat=<target>`` or ``--fast``.
    ``dense_sa`` is the dense suffix-array file wgse-bwa reads, if any."""

    tool: str
    native: NativeAligner | None = None
    gpu: bool = False
    options: tuple[str, ...] = ()
    dense_sa: Path | None = None

    @property
    def dense_sa_bytes(self) -> int:
        """The RAM the dense suffix-array file takes (wgse-bwa holds it)."""
        return self.dense_sa.stat().st_size if self.dense_sa else 0

    @property
    def binary(self) -> str:
        return self.native.binary if self.native else self.tool

    @property
    def engine(self) -> str:
        """The aligner that runs: ``tool``, or bwa-mem3 standing in for it."""
        return self.native.tool if self.native else self.tool

    @property
    def memory_model(self) -> str:
        """The aligner whose memory estimate (alignment_memory) applies:
        ``engine``, or wgse-bwa-mem2 when it maps without the .0123 file."""
        if self.engine == "bwa-mem2" and self.native and not self.native.reads_0123:
            return "wgse-bwa-mem2"
        return self.engine

    @property
    def bwa_mem3_mode(self) -> str | None:
        """``fast`` or ``compat=<target>`` when bwa-mem3 runs."""
        if self.engine != "bwa-mem3":
            return None
        if BWA_MEM3_FAST in self.options:
            return "fast"
        return next(
            (o.removeprefix("--") for o in self.options if o.startswith("--compat=")),
            "default",
        )

    def mem_argv(self) -> list[str]:
        """argv up to and including ``mem`` and its output options."""
        return [*self.argv(), "mem", *self.options]

    def argv(self) -> list[str]:
        """The executable and backend option to put before the subcommand.
        ``--backend auto`` rather than ``--metal`` or ``--cuda``: a GPU failure
        then finishes on the CPU (same alignments) instead of failing the run."""
        if self.native is None:
            return [self.tool]
        argv = [self.native.command, *(["--backend", "auto"] if self.gpu else [])]
        if self.native.supports_dense_sa_option:
            # The file is opt-in: wgse-bwa would otherwise read any present.
            argv += ["--dense-sa", "on" if self.dense_sa else "off"]
        return argv

    def record(self) -> None:
        """Name the build that ran on the result: its version under the
        manifest's tools, and the native_aligner metrics; and bwa-mem3's
        output mode (bwa_mem3_mode)."""
        result = current_result()
        if self.bwa_mem3_mode:
            result.add_metrics(bwa_mem3_mode=self.bwa_mem3_mode)
        if self.native is None:
            return
        result.record_tool_version(self.native.binary, self.native.version)
        result.add_metrics(
            native_aligner=self.native.binary,
            native_aligner_version=self.native.version,
            native_aligner_backend=self.backend,
        )
        if self.dense_sa:
            from wgsextract_cli.core.dense_suffix_array import interval_of

            result.add_metrics(dense_suffix_array_interval=interval_of(self.dense_sa))

    @property
    def backend(self) -> str:
        """cpu, or the GPU backend (metal or cuda) it runs on."""
        if not self.gpu or not self.native:
            return "cpu"
        if self.native.gpu:
            return self.native.gpu
        return "cuda" if self.native.minimap2_gpu else "cpu"

    def announce(self) -> None:
        """Log which build runs, and where."""
        if self.native is None:
            return
        backend = {
            "metal": "the Metal GPU, falling back to the CPU",
            "cuda": "the NVIDIA GPU (CUDA), falling back to the CPU",
        }.get(self.backend, "the CPU")
        mode = self.bwa_mem3_mode
        version = self.native.engine_version
        if mode == "fast":
            output = f"bwa-mem3 {version} --fast's, which differ from BWA's"
        elif mode:
            targets = {f"compat={c}": t for t, c in BWA_MEM3_COMPAT.items()}
            target = targets.get(mode)
            output = (
                f"{target}'s, from bwa-mem3 {version}"
                if target
                else f"bwa-mem3 {version}'s"
            )
        else:
            output = f"{self.tool} {version}'s"
        options = "".join(f" {option}" for option in self.options)
        dense = (
            f" It locates seeds with the dense suffix-array file {self.dense_sa} "
            f"({self.dense_sa_bytes / 1e9:.2f} GB, held in RAM)."
            if self.dense_sa
            else ""
        )
        logging.info(
            f"Aligning with {self.native.binary} {self.native.version}{options} on "
            f"{backend} ({self.native.path}); its alignments are {output}.{dense}"
        )


def native_setting(value: str | None) -> str:
    """The run's native_aligners setting: the option, else config.toml, else auto."""
    from wgsextract_cli.core.config import settings

    setting = str(value or settings.get("native_aligners") or "auto").lower()
    if setting not in SETTINGS:
        raise UsageError(
            f"native_aligners must be one of {', '.join(SETTINGS)}, not {setting!r}."
        )
    return setting


def apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine().lower() in {
        "arm64",
        "aarch64",
    }


def on_linux() -> bool:
    return platform.system() == "Linux"


def platform_binaries() -> dict[str, str]:
    """The builds for this machine (BINARIES on Apple Silicon), by the upstream
    aligner each replaces."""
    if apple_silicon():
        return BINARIES
    if platform.system() != "Linux":
        return {}
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return LINUX_X86_64_BINARIES
    if machine in {"aarch64", "arm64"}:
        return LINUX_BINARIES
    return {}


def _version_tuple(version: object) -> tuple[int, int, int] | None:
    match = _VERSION.match(version) if isinstance(version, str) else None
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


@lru_cache(maxsize=16)
def _probe(path: str, mtime_ns: int, size: int) -> dict[str, object] | str:
    """``capabilities --json`` of the executable at ``path``, or why it can't
    be used. Cached by its modification time and size, failures included, so
    a hung build costs one timeout per process and a replaced one is asked
    again."""
    del mtime_ns, size
    try:
        completed = subprocess.run(
            [path, "capabilities", "--json"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"`capabilities --json` took over {_PROBE_TIMEOUT_SECONDS} s"
    except OSError as error:
        return f"it could not be run: {error}"
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1:] or [""]
        return f"`capabilities --json` exited with {completed.returncode} {detail[0]}".rstrip()
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return "`capabilities --json` did not print JSON"
    if not isinstance(document, dict):
        return "`capabilities --json` did not print an object"
    return document


def _capabilities(path: str) -> dict[str, object]:
    stat = Path(path).stat()
    probed = _probe(path, stat.st_mtime_ns, stat.st_size)
    if isinstance(probed, str):
        raise NativeAlignerUnusable(probed)
    return probed


def _check(tool: str, binary: str, path: str) -> NativeAligner:
    document = _capabilities(path)
    schema = document.get("schema_version")
    if schema != CAPABILITIES_SCHEMA:
        raise NativeAlignerUnusable(
            f"its capabilities schema_version {schema!r} isn't {CAPABILITIES_SCHEMA}"
        )
    if document.get("engine") != tool:
        raise NativeAlignerUnusable(
            f"it reports engine {document.get('engine')!r}, not {tool}"
        )
    if document.get("cpu_available") is not True:
        raise NativeAlignerUnusable("it reports no CPU backend")
    version = document.get("version")
    parsed = _version_tuple(version)
    minimum = MINIMUM_VERSIONS[binary]
    if parsed is None or parsed < minimum:
        raise NativeAlignerUnusable(
            f"version {version!r} is older than the required "
            f"{'.'.join(map(str, minimum))}"
        )
    return NativeAligner(
        tool=tool,
        binary=binary,
        command=binary,
        path=path,
        version=str(version),
        engine_version=str(document.get("engine_version") or ""),
        metal_available=document.get("metal_available") is True,
        cuda_available=document.get("cuda_available") is True,
        cuda_hardware_supported=document.get("cuda_hardware_supported") is True,
    )


def find_bwa_mem3(setting: str | None, *, instead: str) -> NativeAligner | None:
    """The bwa-mem3 that can stand in for upstream ``instead`` (bwa or
    bwa-mem2) with ``--compat``, or None: wgse-bwa-mem3 on Apple Silicon and
    Linux x86-64 (find_native_aligner), else on Linux Bioconda's bwa-mem3
    (its own Pixi environment, #591), which has the same output modes (#672).
    Off with native_aligners off, like the builds."""
    if platform.system() != "Linux":
        return find_native_aligner("bwa-mem3", setting, quiet=True, instead=instead)
    if native_setting(setting) == "off":
        return None
    native = find_native_aligner("bwa-mem3", setting, probe=True)
    if native is not None:
        return native
    bioconda = _bioconda_bwa_mem3(instead)
    # An installed but unusable wgse-bwa-mem3 warns, naming what aligns instead.
    find_native_aligner(
        "bwa-mem3", setting, quiet=True, instead="bwa-mem3" if bioconda else instead
    )
    return bioconda


def _bioconda_bwa_mem3(instead: str) -> NativeAligner | None:
    """Bioconda's bwa-mem3 from PATH or its Pixi environment, when it is
    0.16.0 or later and runs; otherwise None, with a warning when installed."""
    from wgsextract_cli.core.dependencies import get_tool_path
    from wgsextract_cli.core.dependency_checks import get_tool_version

    path = get_tool_path("bwa-mem3")
    if path is None:
        return None
    # Failed probes aren't cached, and realign asks several times: once is enough.
    if any(
        w.code == "native_aligner_unusable" and w.details.get("path") == path
        for w in current_result().warnings
    ):
        return None
    version = get_tool_version("bwa-mem3")
    parsed = _version_tuple(version)
    if parsed is None or parsed < BIOCONDA_BWA_MEM3_MINIMUM:
        minimum = ".".join(map(str, BIOCONDA_BWA_MEM3_MINIMUM))
        # get_tool_version says "Available" when it found no version.
        detail = (
            "no recognizable version"
            if version in (None, "", "Available")
            else str(version).removeprefix("Error: ")
        )
        problem = (
            f"its version {version!r} isn't {minimum} or later"
            if parsed
            else f"`bwa-mem3 version` failed ({detail}{bwa_mem3_crash_cause(detail)})"
        )
        warn(
            "native_aligner_unusable",
            f"Not using bwa-mem3 ({path}): {problem}. Aligning with {instead} instead.",
            binary="bwa-mem3",
            path=path,
            aligner=instead,
        )
        return None
    return NativeAligner(
        tool="bwa-mem3",
        binary="bwa-mem3",
        command="bwa-mem3",
        path=path,
        version=str(version),
        engine_version=str(version),
        metal_available=False,
    )


def bwa_mem3_crash_cause(detail: str) -> str:
    """What may explain a bwa-mem3 crash (``detail`` from its version probe)
    on this computer: Bioconda's Linux build, which overrides malloc with
    mimalloc, crashes at startup with glibc 2.44 (#727)."""
    if "crashed" not in detail or platform.system() != "Linux":
        return ""
    try:
        library = os.confstr("CS_GNU_LIBC_VERSION") or ""
    except (AttributeError, ValueError, OSError):
        return ""
    name, _, version = library.partition(" ")
    parsed = tuple(int(part) for part in re.findall(r"\d+", version)[:2])
    if name != "glibc" or len(parsed) < 2:
        return ""
    if parsed < (2, 44):
        return f"; this computer has glibc {version}"
    return (
        f"; this computer has glibc {version}, and Bioconda's bwa-mem3 0.16.0 "
        "crashes at startup with glibc 2.44 (#727)"
    )


def bwa_mem3_failure() -> str | None:
    """Why the bwa-mem3 on PATH or in its Pixi environment can't run (its
    ``version`` crashed or failed), or None. Run before an explicit
    ``--aligner bwa-mem3`` or ``--fast`` so a broken build stops with an
    explanation rather than a crashed alignment pipeline."""
    from wgsextract_cli.core.dependency_checks import get_tool_version

    version = get_tool_version("bwa-mem3")
    if version is None or not version.startswith("Error:"):
        return None
    detail = version.removeprefix("Error: ")
    return f"`bwa-mem3 version` failed ({detail}{bwa_mem3_crash_cause(detail)})"


def installed_build(tool: str) -> NativeAligner:
    """The build on PATH that replaces upstream ``tool``, wherever this runs;
    NativeAlignerUnusable says why there is none."""
    binary = BINARIES[tool]
    path = shutil.which(binary)
    if path is None:
        raise NativeAlignerUnusable("isn't installed")
    try:
        return _check(tool, binary, path)
    except OSError as error:
        raise NativeAlignerUnusable(str(error)) from error


def find_native_aligner(
    tool: str,
    setting: str | None,
    *,
    quiet: bool = False,
    instead: str | None = None,
    probe: bool = False,
) -> NativeAligner | None:
    """The build that can replace upstream ``tool`` in this run, or None:
    off, none for this machine (platform_binaries), not installed (logged
    unless ``quiet``), or unusable (with a warning that ``instead``, else
    ``tool``, aligns). A ``probe`` before the aligner is chosen logs nothing."""
    binary = platform_binaries().get(tool)
    if binary is None or native_setting(setting) == "off":
        return None
    path = shutil.which(binary)
    if path is None:
        if quiet or probe:
            return None
        # No Linux package is published yet, so only Macs get the hint.
        log = logging.info if apple_silicon() else logging.debug
        log(
            f"Aligning with {tool}: {binary}, which writes the same alignments "
            f"faster, isn't installed ({INSTALL_HINT})."
        )
        return None
    try:
        return _check(tool, binary, path)
    except (NativeAlignerUnusable, OSError) as error:
        if probe:
            return None
        # Checks before an alignment ask again; one warning per run is enough.
        if any(
            w.code == "native_aligner_unusable" and w.details.get("path") == path
            for w in current_result().warnings
        ):
            return None
        warn(
            "native_aligner_unusable",
            f"Not using {path}: {error}. Aligning with {instead or tool} instead.",
            binary=binary,
            path=path,
            aligner=instead or tool,
        )
        return None
