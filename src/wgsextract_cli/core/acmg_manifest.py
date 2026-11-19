"""Persistence of the ACMG reference library's build manifest."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from wgsextract_cli.core.utils import WGSExtractError

MANIFEST = "acmg_data.json"
SITE_DATA = ("library", "per-run")


def load_manifest(sa_dir: str | Path) -> dict[str, Any]:
    path = Path(sa_dir, MANIFEST)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise WGSExtractError(f"Can't read {path}: {e}") from e
    return data if isinstance(data, dict) else {}


def site_data_of(manifest: dict[str, Any]) -> str:
    """Whether gnomAD and SpliceAI are built in the library or read per run."""
    value = manifest.get("site_data")
    return value if value in SITE_DATA else "library"


def record_site_data(
    sa_dir: str | Path, site_data: str, spliceai: Path | None = None
) -> None:
    """Record the selected site-data mode and any explicitly chosen source."""
    path = Path(sa_dir, MANIFEST)
    manifest = load_manifest(sa_dir)
    if manifest.get("site_data") == site_data and spliceai is None:
        return
    manifest["site_data"] = site_data
    if spliceai is not None:
        manifest["spliceai_file"] = str(spliceai.resolve())
    Path(sa_dir).mkdir(parents=True, exist_ok=True)
    save_manifest(path, manifest)


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".acmg_manifest_") as work:
        partial = Path(work, MANIFEST)
        partial.write_text(json.dumps(manifest, indent=1) + "\n", newline="\n")
        partial.replace(path)
