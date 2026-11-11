"""``ref dense-sa``: build, list or delete wgse-bwa's optional dense
suffix-array files beside a FASTA's BWA index (core/dense_suffix_array.py)."""

import logging
import shutil
from os import fspath
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.ref import RefDenseSaParams
from wgsextract_cli.core import dense_suffix_array, library_removal
from wgsextract_cli.core.bwa_index import INDEX_SUFFIXES, has_index
from wgsextract_cli.core.file_lock import build_lock
from wgsextract_cli.core.native_aligners import NativeAligner, NativeAlignerUnusable
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.utils import (
    MissingDependencyError,
    UsageError,
    WGSExtractError,
    get_resource_defaults,
    run_command,
)

_GB = 1e9
# Room on the disk beyond the file itself.
_DISK_MARGIN_BYTES = 1 << 30


def _fasta(params: RefDenseSaParams) -> str:
    if not params.ref:
        raise UsageError("--ref is required.")
    ref = fspath(params.ref)
    if not Path(ref).is_file():
        raise UsageError(f"Reference FASTA not found: {ref}")
    return ref


def _build_or_none() -> NativeAligner | None:
    try:
        return dense_suffix_array.build()
    except NativeAlignerUnusable:
        return None


def run_dense_sa(params: RefDenseSaParams) -> None:
    ref = _fasta(params)
    if params.delete:
        _delete(ref)
    elif params.list_files:
        _list(ref)
    else:
        _build(ref, params.interval, params.threads)


def _delete(ref: str) -> None:
    files = dense_suffix_array.side_files(ref)
    partial = [Path(f"{path}.partial") for path in files]
    partial += Path(ref).parent.glob(f"{Path(ref).name}.wgse-sa*.partial")
    removed = library_removal.remove([*files, *dict.fromkeys(partial)])
    freed = sum(1 for path in removed if path in files)
    human_output(
        f"Deleted {freed} dense suffix-array file(s) of {ref}."
        if freed
        else f"{ref} has no dense suffix-array files; nothing to delete."
    )


def _list(ref: str) -> None:
    files = dense_suffix_array.side_files(ref)
    native = _build_or_none()
    readable = dense_suffix_array.readable_interval(native, ref) if native else None
    rows = []
    for path in files:
        interval = dense_suffix_array.interval_of(path)
        size = path.stat().st_size
        rows.append({"path": str(path), "interval": interval, "bytes": size})
        mark = " (wgse-bwa reads this one)" if interval == readable else ""
        human_output(f"{path}: every {interval} rows, {size / _GB:.2f} GB{mark}")
    if not files:
        planned = dense_suffix_array.planned_bytes(
            ref, dense_suffix_array.DEFAULT_INTERVAL
        )
        cost = f" ({planned / _GB:.2f} GB)" if planned else ""
        human_output(
            f"{ref} has no dense suffix-array files; `wgsextract ref dense-sa "
            f"--ref {ref}` builds one{cost}."
        )
    elif native is None:
        human_output("wgse-bwa 0.11.0 or later isn't installed, so none is read.")
    elif readable is None:
        human_output(
            "wgse-bwa reads none of them on this computer: it needs 16 GiB of "
            "RAM or more, a file of at most an eighth of it, and an intact file "
            "made from this index."
        )
    current_result().set_details({"dense_suffix_array_files": rows})
    current_result().add_metrics(dense_suffix_array_files=len(files))
    if readable is not None:
        current_result().add_metrics(dense_suffix_array_interval=readable)


def _build(ref: str, interval: int, threads: int | None) -> None:
    if not has_index(ref, INDEX_SUFFIXES, "bwa"):
        raise UsageError(
            f"{ref} has no complete BWA index; run `wgsextract ref index --ref "
            f"{ref}` first."
        )
    try:
        native = dense_suffix_array.build()
    except NativeAlignerUnusable as error:
        raise MissingDependencyError(f"`ref dense-sa` needs {error}.") from error
    planned = dense_suffix_array.planned_bytes(ref, interval)
    if planned is None:
        raise UsageError(
            f"{ref}'s BWA index can't be sampled every {interval} rows (its own "
            "sample interval must be a larger multiple of it)."
        )
    target = dense_suffix_array.side_file(ref, interval)
    free = shutil.disk_usage(Path(ref).parent).free
    existing = target.stat().st_size if target.is_file() else 0
    if planned + _DISK_MARGIN_BYTES > free + existing:
        raise WGSExtractError(
            f"Writing {target} needs {planned / _GB:.2f} GB of disk; "
            f"{free / _GB:.2f} GB is free."
        )
    logging.info(
        f"Writing {target}: {planned / _GB:.2f} GB on disk, which wgse-bwa holds "
        "in RAM while aligning when asked to (--dense-suffix-array)."
    )
    count, _ = get_resource_defaults(threads, None)
    with build_lock(f"{ref}.dense-sa.lock"):
        run_command([native.path, "densify-sa", "-i", str(interval), "-t", count, ref])
    if not target.is_file():
        raise WGSExtractError(f"wgse-bwa densify-sa did not write {target}")
    size = target.stat().st_size
    current_result().record_tool_version(native.binary, native.version)
    current_result().add_output(str(target), "reference_index")
    current_result().add_metrics(
        dense_suffix_array_interval=interval, dense_suffix_array_bytes=size
    )
    current_result().set_reference(ref)
    human_output(
        f"Wrote {target} ({size / _GB:.2f} GB). Align with --dense-suffix-array, "
        "or set dense_suffix_array = true in config.toml, to use it; it is held "
        "in RAM while aligning."
    )
    readable = dense_suffix_array.readable_interval(native, ref)
    if readable != interval:
        warn(
            dense_suffix_array.UNUSED,
            f"wgse-bwa won't read {target} on this computer: it needs 16 GiB "
            "of RAM or more and a file of at most an eighth of it"
            + (f" (it reads the every-{readable} file instead)" if readable else "")
            + ".",
            reason="unusable",
            path=str(target),
        )


cmd_dense_sa = cli_handler(RefDenseSaParams, run_dense_sa)
