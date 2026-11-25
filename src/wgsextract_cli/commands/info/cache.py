"""Where `info` caches its metrics and how a cache is matched to its file:
``<outdir>/manifests/<input>.wgse_info.json``, or beside the files as
earlier releases wrote it."""

import json
import os
from pathlib import Path
from typing import Any

from wgsextract_cli.core import read_scope
from wgsextract_cli.core.config import output_directory
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.results import fingerprint, manifest_dir_for
from wgsextract_cli.core.variant_files import calculate_bam_md5


def input_signature(path: str) -> dict[str, int | str] | None:
    """Local identity and a bounded content check, even for whole-genome files.

    The sampled digest is not a full integrity check. On Windows, ctime
    records creation, not edits, so a change outside the samples that also
    preserves size and mtime can go undetected.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    content = fingerprint(path)
    if content is None:
        return None
    return {
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "ctime_ns": st.st_ctime_ns,
        "device": st.st_dev,
        "inode": st.st_ino,
        "fingerprint": content,
    }


def info_cache_path(
    input_path: str | os.PathLike[str], outdir: str | os.PathLike[str]
) -> Path:
    """``<outdir>/manifests/<input_name>.wgse_info.json``."""
    return manifest_dir_for(outdir) / f"{Path(input_path).name}.wgse_info.json"


def cached_info(
    input_path: str, outdir: str | None = None, key: str = "gender"
) -> dict[str, Any] | None:
    """`info`'s cached metrics for this very file, if a cache with `key` exists
    in the output folder's ``manifests/`` subfolder (or a legacy beside-file cache)."""
    name = f"{Path(input_path).name}.wgse_info.json"
    this_file = input_signature(input_path)
    if this_file is None:
        return None
    signature = calculate_bam_md5(input_path, None)
    folders = [
        folder
        for folder in (
            outdir,
            output_directory([input_path]),
            str(absolute_path(input_path).parent),
        )
        if folder
    ]
    candidates = [
        *(info_cache_path(input_path, folder) for folder in folders),
        *(Path(folder, name) for folder in folders),
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if not read_scope.may_follow(candidate):
            continue
        try:
            with open(candidate) as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        # The header signature is shared by every sample on that reference, so
        # the cache must also be from this very file.
        if (
            isinstance(info, dict)
            and info.get("md5_signature") == signature
            and info.get("input_signature") == this_file
            and info.get(key)
        ):
            return info
    return None
