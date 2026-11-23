"""Lossless FASTQ archive and restore commands."""

from __future__ import annotations

import os
import unicodedata
import zlib
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.api.fastq import FastqArchiveParams, FastqRestoreParams
from wgsextract_cli.core.atomic_output import publication_lock
from wgsextract_cli.core.errors import CodedError
from wgsextract_cli.core.fastq_archive import (
    FastqArchiveError,
    FastqWriter,
    restore_archive,
    write_archive,
)
from wgsextract_cli.core.fastq_archive_streams import StreamCodec
from wgsextract_cli.core.result_card import CARD_SUFFIX
from wgsextract_cli.core.results import (
    MANIFEST_SUFFIX,
    current_result,
    manifest_path_for,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


def _destination_key(path: Path) -> str:
    return unicodedata.normalize(
        "NFC", os.path.normcase(str(path.resolve())).casefold()
    )


def _validate_sources(paths: tuple[Path, ...]) -> None:
    for src in paths:
        if not src.is_file():
            raise CodedError("fastq_input_missing", f"Input is not a local file: {src}")
    if len(paths) == 2 and paths[0].samefile(paths[1]):
        raise CodedError(
            "fastq_invalid_input", "Pass two distinct FASTQ files for paired reads."
        )


def _validate_manifest(sources: tuple[Path, ...], outputs: tuple[Path, ...]) -> None:
    manifest = manifest_path_for(outputs[0])
    stem = manifest.name.removesuffix(MANIFEST_SUFFIX)
    sidecars = {
        _destination_key(manifest),
        _destination_key(manifest.with_name(stem + CARD_SUFFIX)),
        _destination_key(manifest.with_name(stem + ".failed" + CARD_SUFFIX)),
    }
    if sidecars.intersection(_destination_key(path) for path in (*sources, *outputs)):
        raise CodedError(
            "fastq_output_conflict",
            "A result sidecar would replace a FASTQ input or output.",
        )


@contextmanager
def _stage_outputs(paths: tuple[Path, ...]) -> Iterator[tuple[FastqWriter, ...]]:
    keys = [_destination_key(path) for path in paths]
    if len(set(keys)) != len(paths):
        raise CodedError(
            "fastq_output_conflict", "FASTQ output paths must be distinct."
        )
    if len(keys) == 2:
        first, second = map(Path, keys)
        if first in second.parents or second in first.parents:
            raise CodedError(
                "fastq_output_conflict", "One FASTQ output cannot contain the other."
            )
    with ExitStack() as staged:
        ordered = sorted(zip(keys, paths, strict=True))
        for _, path in ordered:
            path.parent.mkdir(parents=True, exist_ok=True)
            staged.enter_context(publication_lock(path, checkpoint=checkpoint))
            if path.exists() or path.is_symlink():
                raise CodedError(
                    "fastq_output_conflict", f"Output already exists: {path}"
                )
        staged_paths: list[Path] = []
        with ExitStack() as files:
            outputs = []
            for path in paths:
                output = files.enter_context(
                    NamedTemporaryFile(
                        mode="wb", dir=path.parent, prefix=".wgse_fastq_", delete=False
                    )
                )
                temporary = Path(output.name)
                staged.callback(temporary.unlink, missing_ok=True)
                staged_paths.append(temporary)
                outputs.append(output)
            yield tuple(outputs)
        checkpoint()
        published = []
        try:
            for temporary, path in zip(staged_paths, paths, strict=True):
                # Linking publishes the validated file without replacing a name
                # created by another process while this run was working.
                path.hardlink_to(temporary)
                published.append((temporary, path))
        except OSError:
            for temporary, path in published:
                with suppress(OSError):
                    if path.samefile(temporary):
                        path.unlink()
            raise


def run_archive(params: FastqArchiveParams) -> None:
    sources = (params.r1, params.r2) if params.r2 else (params.r1,)
    _validate_sources(sources)
    _validate_manifest(sources, (params.output,))
    result = current_result()
    for index, path in enumerate(sources, 1):
        result.add_input(path, f"fastq_r{index}")
    codec = StreamCodec()
    try:
        with _stage_outputs((params.output,)) as (archive,):
            write_archive(sources, archive, codec, checkpoint=checkpoint)
    except (OSError, EOFError, FastqArchiveError, zlib.error) as error:
        raise CodedError("fastq_archive_failed", str(error)) from error
    result.add_output(params.output, "fastq_archive")


def run_restore(params: FastqRestoreParams) -> None:
    _validate_sources((params.input,))
    outputs = (
        (params.output, params.output_r2) if params.output_r2 else (params.output,)
    )
    _validate_manifest((params.input,), outputs)
    if any(path.suffix.lower() in {".gz", ".bgz", ".bgzf"} for path in outputs):
        raise CodedError(
            "fastq_invalid_output",
            "Restore writes plain FASTQ; choose an uncompressed output name.",
        )
    result = current_result()
    result.add_input(params.input, "fastq_archive")
    codec = StreamCodec()
    try:
        with params.input.open("rb") as archive, _stage_outputs(outputs) as staged:
            restore_archive(archive, staged, codec, checkpoint=checkpoint)
    except (OSError, FastqArchiveError, zlib.error) as error:
        raise CodedError("fastq_restore_failed", str(error)) from error
    for index, path in enumerate(outputs, 1):
        result.add_output(path, f"fastq_r{index}")


cmd_archive = cli_handler(FastqArchiveParams, run_archive)
cmd_restore = cli_handler(FastqRestoreParams, run_restore)
