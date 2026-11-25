"""Pinned public reference exon model for GRCh38 research panels."""

from __future__ import annotations

import hashlib
from pathlib import Path

from wgsextract_cli.core.network_policy import require_download
from wgsextract_cli.core.ref_library.assets import resolve_reference_library
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.ref_library.resolver import library_roots
from wgsextract_cli.core.utils import WGSExtractError

FOLDER = "panel"
MANE_NAME = "mane-grch38-v1.tsv.gz"
MANE_URL = (
    "https://github.com/WGSExtract/wgsextract-cli/releases/download/"
    f"panel-reference-1/{MANE_NAME}"
)
MANE_SHA256 = "8d5b02c3fcc267d5b7bc420dfc0f67feb9c239c58c703230e6fa72462ed61ad0"


def mane_model(ref: Path | None) -> Path:
    """Get the verified MANE data; no network without download consent."""
    root = resolve_reference_library(library_roots(ref).resource_root(FOLDER))
    path = Path(root, FOLDER, MANE_NAME)
    if not path.is_file():
        require_download(f"{MANE_NAME} at {path} (GRCh38 MANE Select exon model)")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not download_file(
            MANE_URL,
            str(path),
            expected_sha256=MANE_SHA256,
            resolve_release_digest=False,
        ):
            raise WGSExtractError(
                f"Could not download the public MANE model {MANE_NAME}."
            )
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for data in iter(lambda: stream.read(1 << 20), b""):
            digest.update(data)
    if digest.hexdigest() != MANE_SHA256:
        raise WGSExtractError(
            f"{path} does not match its pinned SHA-256. Remove it and rerun "
            "with --download to fetch the verified public model."
        )
    return path
