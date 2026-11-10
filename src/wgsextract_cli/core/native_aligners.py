"""WGS Extract's Apple Silicon aligner builds (native/README.md): wgse-bwa,
wgse-bwa-mem2 and wgse-minimap2 write their upstream aligner's alignments,
faster. wgse-bwa-mem3 (bwa-mem3) writes bwa's or bwa-mem2's exact records with
``--compat``, faster still, and so can stand in for either. All but
wgse-minimap2 can also run on the Metal GPU. They are used when installed on
PATH; they are never installed here.

Each tool is asked once per process what it can do (``capabilities --json``),
and anything unexpected falls back to the upstream aligner.
"""

from __future__ import annotations

import json
import logging
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


@dataclass(frozen=True)
class AlignerCommand:
    """How to run an aligner: upstream ``tool`` itself, or ``native`` (on the
    Metal GPU when ``metal``) in its place. ``options`` are bwa-mem3's ``mem``
    options that choose its output: ``--compat=<target>`` or ``--fast``."""

    tool: str
    native: NativeAligner | None = None
    metal: bool = False
    options: tuple[str, ...] = ()

    @property
    def binary(self) -> str:
        return self.native.binary if self.native else self.tool

    @property
    def engine(self) -> str:
        """The aligner that runs: ``tool``, or bwa-mem3 standing in for it."""
        return self.native.tool if self.native else self.tool

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
        ``--backend auto`` rather than ``--metal``: a GPU failure then
        finishes on the CPU (same alignments) instead of failing the run."""
        if self.native is None:
            return [self.tool]
        return [self.native.command, *(["--backend", "auto"] if self.metal else [])]

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

    @property
    def backend(self) -> str:
        return "metal" if self.metal else "cpu"

    def announce(self) -> None:
        """Log which build runs, and where."""
        if self.native is None:
            return
        backend = "the Metal GPU, falling back to the CPU" if self.metal else "the CPU"
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
        logging.info(
            f"Aligning with {self.native.binary} {self.native.version}{options} on "
            f"{backend} ({self.native.path}); its alignments are {output}."
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
    return platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"}


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
    )


def find_native_aligner(
    tool: str, setting: str | None, *, quiet: bool = False, instead: str | None = None
) -> NativeAligner | None:
    """The build that can replace upstream ``tool`` in this run, or None:
    off, not Apple Silicon, not installed (logged unless ``quiet``), or
    unusable (with a warning that ``instead``, else ``tool``, aligns)."""
    binary = BINARIES.get(tool)
    if binary is None or native_setting(setting) == "off" or not apple_silicon():
        return None
    path = shutil.which(binary)
    if path is None:
        if quiet:
            return None
        logging.info(
            f"Aligning with {tool}: {binary}, which writes the same alignments "
            f"faster on Apple Silicon, isn't installed ({INSTALL_HINT})."
        )
        return None
    try:
        return _check(tool, binary, path)
    except (NativeAlignerUnusable, OSError) as error:
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
