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


DEFAULT_ENGINE = "vep-rs"


def vep_engine(name: str | None) -> VepEngine:
    """The engine called ``name`` (default vep-rs)."""
    try:
        return ENGINES[name or DEFAULT_ENGINE]
    except KeyError:
        raise WGSExtractError(
            f"Unknown VEP engine {name!r}; choose one of {', '.join(ENGINE_NAMES)}."
        ) from None


def configured_engine(
    cache_roots: list[Path] | None = None, *, to_run: bool = True
) -> VepEngine:
    """The engine config.toml's ``vep_engine`` names, else the one
    ``resolve_engine`` picks for ``cache_roots``."""
    from wgsextract_cli.core.config import settings

    return resolve_engine(settings.get("vep_engine"), cache_roots or [], to_run=to_run)[
        0
    ]


def resolve_engine(
    chosen: str | None, cache_roots: list[Path], *, to_run: bool = True
) -> tuple[VepEngine, str | None]:
    """The engine to annotate with, and a notice when it isn't the default.

    An engine chosen with --engine or config ``vep_engine`` is used as is.
    Otherwise it's vep-rs, except for a setup from before vep-rs became the
    default: an Ensembl VEP cache in one of ``cache_roots`` but no vep-rs
    cache, with Ensembl VEP installed. That keeps annotating with Ensembl VEP
    and says how to switch. ``to_run=False`` (to show, check or delete a
    cache) doesn't need Ensembl VEP to be installed or to run here."""
    if chosen:
        return vep_engine(chosen), None
    default, ensembl = ENGINES[DEFAULT_ENGINE], ENGINES["ensembl"]
    if any((default.cache_root(r) / "homo_sapiens").is_dir() for r in cache_roots):
        return default, None
    from wgsextract_cli.core.dependencies import get_tool_path

    old = next((r for r in cache_roots if (r / "homo_sapiens").is_dir()), None)
    runnable = not to_run or (ensembl.supported_here() and get_tool_path(ensembl.tool))
    if old is not None and runnable:
        return ensembl, (
            f"There's no vep-rs cache yet, so this run uses Ensembl VEP and its "
            f"cache in {old}. vep-rs is now the default and about 70 times "
            "faster: build its cache with 'wgsextract vep download', or keep "
            'Ensembl VEP with --engine ensembl or vep_engine = "ensembl" in '
            "config.toml."
        )
    return default, None


def _refuses(engine: VepEngine, token: str) -> bool:
    """Whether ``token`` names an option ``engine`` refuses. vep-rs, like
    Ensembl VEP's Getopt, takes one or two dashes, any case and unambiguous
    abbreviations (``-CUST``), so those count too."""
    if not token.startswith("-"):
        return False
    name = token.lstrip("-").split("=", 1)[0].lower()
    if not name:
        return False
    for refused in engine.refused_args:
        full = refused.lstrip("-")
        if name == full or name.startswith(f"{full}_"):
            return True
        if len(name) >= 3 and full.startswith(name):
            return True
    return False


def refuse_unsupported_args(engine: VepEngine, vep_args: str | None) -> None:
    """Refuse --vep-args options ``engine`` would ignore or can't honour."""
    if not vep_args or not engine.refused_args:
        return
    refused = sorted({t for t in shlex.split(vep_args) if _refuses(engine, t)})
    if refused:
        raise WGSExtractError(
            f"{engine.label} does not support {', '.join(refused)}; "
            "use --engine ensembl for these options."
        )
