"""Pinned public GRCh38 research panel reference data."""

from __future__ import annotations

import csv
import gzip
import hashlib
from collections.abc import Iterator
from pathlib import Path

from wgsextract_cli.core.network_policy import require_download
from wgsextract_cli.core.ref_library.assets import resolve_reference_library
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.ref_library.resolver import library_roots
from wgsextract_cli.core.utils import WGSExtractError

FOLDER = "panel"
_RELEASE = (
    "https://github.com/WGSExtract/wgsextract-cli/releases/download/panel-reference-1/"
)
MANE_NAME = "mane-grch38-v1.tsv.gz"
MANE_SHA256 = "8d5b02c3fcc267d5b7bc420dfc0f67feb9c239c58c703230e6fa72462ed61ad0"
EXPECTED_NAME = "gnomad-v4.1-mane-expected-syn.tsv.gz"
EXPECTED_SHA256 = "7bde625f0fa82ef0309df659f24d493ad6adfa968534ed86d4006d29b83f70b3"


def panel_asset_path(ref: Path | None, name: str) -> Path:
    """Resolve a pinned panel asset's location without reading or downloading it."""
    root = resolve_reference_library(library_roots(ref).resource_root(FOLDER))
    return Path(root, FOLDER, name)


def panel_staging_paths(path: Path) -> Iterator[Path]:
    """Files the direct downloader may use beside an asset."""
    for suffix in (
        ".partial",
        ".partial.source",
        ".partial.segments",
        ".partial.segments.tmp",
    ):
        yield path.with_name(path.name + suffix)


def mane_model(ref: Path | None) -> Path:
    """Get the verified MANE data; no network without download consent."""
    return _pinned(ref, MANE_NAME, MANE_SHA256, "GRCh38 MANE Select exon model")


def expected_syn(ref: Path | None) -> tuple[Path, dict[str, float]]:
    """The exact-transcript gnomAD v4.1 synonymous expectations (CC0 1.0)."""
    path = _pinned(ref, EXPECTED_NAME, EXPECTED_SHA256, "gnomAD v4.1 expected counts")
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        rows = csv.DictReader(
            (line for line in source if not line.startswith("#")), delimiter="\t"
        )
        return path, {row["transcript"]: float(row["expected_syn"]) for row in rows}


def _pinned(ref: Path | None, name: str, sha256: str, label: str) -> Path:
    path = panel_asset_path(ref, name)
    if not path.is_file():
        require_download(f"{name} at {path} ({label})")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not download_file(
            _RELEASE + name,
            str(path),
            expected_sha256=sha256,
            resolve_release_digest=False,
            use_cache=False,
            use_mirrors=False,
        ):
            raise WGSExtractError(f"Could not download {label}: {name}.")
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for data in iter(lambda: stream.read(1 << 20), b""):
            digest.update(data)
    if digest.hexdigest() != sha256:
        raise WGSExtractError(
            f"{path} does not match its pinned SHA-256. Remove it and rerun "
            "with --download to fetch the verified public model."
        )
    return path
