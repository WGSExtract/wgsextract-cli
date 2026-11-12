"""mimalloc for bcftools on Apple Silicon (#646).

About a third of ``bcftools mpileup``'s time on macOS goes to the system
malloc. WGS Extract's ``wgse-mimalloc`` package (packaging/mimalloc) is
mimalloc built to replace malloc in the process it is injected into, so
``vcf snp``/``vcf indel`` start their bcftools processes with
``DYLD_INSERT_LIBRARIES`` pointing at it: the same records, about a fifth
faster. It is used only on macOS arm64 when the package is installed beside
bcftools; it is never installed here. A probe checks once per process that
bcftools actually loads it (macOS drops ``DYLD_*`` variables for protected
binaries), and anything unexpected falls back to the system allocator.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from wgsextract_cli.core.native_aligners import apple_silicon
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.utils import UsageError

SETTINGS = ("auto", "off")
INJECT = "DYLD_INSERT_LIBRARIES"
# Where wgse-mimalloc installs the library, relative to its environment.
LIBRARY = Path("lib", "wgse-mimalloc", "libmimalloc.dylib")


def mimalloc_setting(value: str | None) -> str:
    """The run's mimalloc setting: the option, else config.toml, else auto."""
    from wgsextract_cli.core.config import settings

    setting = str(value or settings.get("mimalloc") or "auto").lower()
    if setting not in SETTINGS:
        raise UsageError(
            f"mimalloc must be one of {', '.join(SETTINGS)}, not {setting!r}."
        )
    return setting


@lru_cache(maxsize=8)
def _loads(bcftools: str, library: str) -> bool:
    """Whether ``bcftools`` starts with ``library`` injected and loaded."""
    env = {**os.environ, INJECT: library, "MIMALLOC_VERBOSE": "1"}
    try:
        probe = subprocess.run(
            [bcftools, "--version"],
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        logging.debug(f"mimalloc probe could not start bcftools: {error}")
        return False
    loaded = probe.returncode == 0 and "mimalloc:" in probe.stderr
    if not loaded:
        logging.debug(
            f"mimalloc probe: exit {probe.returncode}, stderr {probe.stderr[-500:]!r}"
        )
    return loaded


def _apple_silicon_environment() -> dict[str, str]:
    bcftools = shutil.which("bcftools")
    library = Path(bcftools).parent.parent / LIBRARY if bcftools else None
    if bcftools is None or library is None or not library.is_file():
        logging.info(
            "bcftools uses the system allocator: wgse-mimalloc, which makes "
            "variant calling about a fifth faster on Apple Silicon, isn't "
            "installed in its environment."
        )
        return {}
    if os.environ.get(INJECT):
        logging.info(
            f"bcftools uses the system allocator: {INJECT} is already set, "
            "so wgse-mimalloc is left out."
        )
        return {}
    if not _loads(bcftools, str(library)):
        logging.info(
            f"bcftools uses the system allocator: it didn't load {library} "
            "when injected."
        )
        return {}
    logging.info(f"bcftools mpileup and call use mimalloc ({library}).")
    return {INJECT: str(library)}


def bcftools_environment(value: str | None) -> dict[str, str]:
    """Variables to add to the environment of the bcftools processes that call
    variants: mimalloc's injection, or none for the system allocator. The
    choice is logged and recorded as the ``bcftools_allocator`` metric."""
    setting = mimalloc_setting(value)
    extra: dict[str, str] = {}
    if setting == "off":
        logging.info("bcftools uses the system allocator (mimalloc off).")
    elif apple_silicon():
        extra = _apple_silicon_environment()
    current_result().add_metrics(bcftools_allocator="mimalloc" if extra else "system")
    return extra
