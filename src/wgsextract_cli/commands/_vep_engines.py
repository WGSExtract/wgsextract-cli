"""The engines ``vep run`` can annotate with, and what each needs.

Each engine names its executable, where its cache lives under the VEP cache
folder, and the VEP options it can't honour. ``vep run``, ``vep download``,
``vep verify`` and ``deps check`` look the engine up here, so another engine
(fastVEP, say) is one more entry plus its command and cache builder.
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.utils import WGSExtractError

ENGINE_NAMES = ("ensembl", "vep-rs")


@dataclass(frozen=True)
class VepEngine:
    name: str
    label: str
    # The tool that annotates, as tool_catalog names it.
    tool: str
    # The tools `vep download` runs to build this engine's cache.
    cache_tools: tuple[str, ...]
    # Folder under the VEP cache folder holding this engine's
    # <species>/<release>_<assembly> caches ("" for the folder itself).
    cache_subdir: str
    # Whether a missing cache may fall back to Ensembl's database.
    database_fallback: bool
    # VEP options the engine accepts but ignores or can't honour: refused,
    # so a run never silently differs from Ensembl VEP's.
    refused_args: tuple[str, ...]
    # sys.platform prefixes the engine runs on.
    platforms: tuple[str, ...]

    def cache_root(self, vep_cache_root: Path) -> Path:
        return (
            vep_cache_root / self.cache_subdir if self.cache_subdir else vep_cache_root
        )

    def supported_here(self) -> bool:
        return sys.platform.startswith(self.platforms)


ENGINES = {
    "ensembl": VepEngine(
        name="ensembl",
        label="Ensembl VEP",
        tool="vep",
        cache_tools=("curl", "tar"),
        cache_subdir="",
        database_fallback=True,
        refused_args=(),
        platforms=("linux", "darwin"),
    ),
    "vep-rs": VepEngine(
        name="vep-rs",
        label="vep-rs",
        tool="vep-rs",
        cache_tools=("vep-cache-builder", "samtools"),
        cache_subdir="vep-rs",
        database_fallback=False,
        # vep-rs reads no --custom source (accepted and ignored in 0.5) and
        # never connects to a database; the rest it refuses itself.
        refused_args=(
            "--custom",
            "--database",
            "--host",
            "--port",
            "--user",
            "--password",
            "--registry",
        ),
        platforms=("linux", "darwin", "win32"),
    ),
}


def vep_engine(name: str | None) -> VepEngine:
    """The engine called ``name`` (default Ensembl VEP)."""
    try:
        return ENGINES[name or "ensembl"]
    except KeyError:
        raise WGSExtractError(
            f"Unknown VEP engine {name!r}; choose one of {', '.join(ENGINE_NAMES)}."
        ) from None


def require_engine_here(engine: VepEngine) -> None:
    if not engine.supported_here():
        others = [e.name for e in ENGINES.values() if e.supported_here()]
        raise WGSExtractError(
            f"{engine.label} is not supported on this platform; use "
            f"--engine {' or '.join(others)} (or set vep_engine in config.toml)."
        )


def refuse_unsupported_args(engine: VepEngine, vep_args: str | None) -> None:
    """Refuse --vep-args options ``engine`` would ignore or can't honour."""
    if not vep_args or not engine.refused_args:
        return
    given = {arg.split("=", 1)[0] for arg in shlex.split(vep_args)}
    refused = sorted(
        arg
        for arg in given
        if arg in engine.refused_args
        or any(arg.startswith(f"{r}_") for r in engine.refused_args)
    )
    if refused:
        raise WGSExtractError(
            f"{engine.label} does not support {', '.join(refused)}; "
            "use --engine ensembl for these options."
        )
