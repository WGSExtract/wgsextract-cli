"""Lossless FASTQ archive and restore commands."""

from __future__ import annotations

import os
import zlib
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.api.fastq import FastqArchiveParams, FastqRestoreParams
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
    return os.path.normcase(str(path.resolve()))


def _validate_sources(paths: tuple[Path, ...]) -> None:
    for src in paths:
        if not src.is_file():
            raise CodedError("fastq_input_missing", f"Input is not a local file: {src}")
    if len(paths) == 2 and paths[0].samefile(paths[1]):
        raise CodedError(
            "fastq_invalid_input", "Pass two distinct FASTQ files for paired reads."
        )


def _aliases(first: Path, second: Path) -> bool:
    if _destination_key(first) == _destination_key(second):
        return True
    with suppress(FileNotFoundError):
        return first.samefile(second)
    return False


def _validate_manifest(sources: tuple[Path, ...], outputs: tuple[Path, ...]) -> None:
    manifest = manifest_path_for(outputs[0])
    stem = manifest.name.removesuffix(MANIFEST_SUFFIX)
    sidecars = (
        manifest,
        manifest.with_name(stem + CARD_SUFFIX),
        manifest.with_name(stem + ".failed" + CARD_SUFFIX),
    )
    if any(
        _aliases(sidecar, path) for sidecar in sidecars for path in sources + outputs
    ):
        raise CodedError(
            "fastq_output_conflict",
            "A result sidecar would replace a FASTQ input or output.",
        )


@contextmanager
def _stage_outputs(
    paths: tuple[Path, ...], sources: tuple[Path, ...]
) -> Iterator[tuple[FastqWriter, ...]]:
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
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
        for path in paths:
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
        complete = False
        try:
            for temporary, path in zip(staged_paths, paths, strict=True):
                # Linking publishes the validated file without replacing a name
                # created by another process while this run was working.
                path.hardlink_to(temporary)
                published.append((temporary, path))
            # A new mate can only alias a sidecar by file identity once it exists.
            _validate_manifest(sources, paths)
            complete = True
        except FileExistsError as error:
            raise CodedError(
                "fastq_output_conflict",
                f"Output already exists: {error.filename or path}",
            ) from error
        except OSError as error:
            raise CodedError(
                "fastq_publication_failed",
                f"Could not publish {path} without replacing it: {error}",
            ) from error
        finally:
            if not complete:
                _rollback_outputs(published)


def _rollback_outputs(published: list[tuple[Path, Path]]) -> None:
    for temporary, path in published:
        with suppress(OSError):
            if path.samefile(temporary):
                path.unlink()


def run_archive(params: FastqArchiveParams) -> None:
    sources = (
        (params.r1.expanduser(), params.r2.expanduser())
        if params.r2
        else (params.r1.expanduser(),)
    )
    output = params.output.expanduser()
    _validate_sources(sources)
    _validate_manifest(sources, (output,))
    result = current_result()
    for index, path in enumerate(sources, 1):
        result.add_input(path, f"fastq_r{index}")
    codec = StreamCodec()
    try:
        with _stage_outputs((output,), sources) as (archive,):
            write_archive(sources, archive, codec, checkpoint=checkpoint)
    except (OSError, EOFError, FastqArchiveError, zlib.error) as error:
        raise CodedError("fastq_archive_failed", str(error)) from error
    result.add_output(output, "fastq_archive")


def run_restore(params: FastqRestoreParams) -> None:
    source = params.input.expanduser()
    _validate_sources((source,))
    outputs = (
        (params.output.expanduser(), params.output_r2.expanduser())
        if params.output_r2
        else (params.output.expanduser(),)
    )
    _validate_manifest((source,), outputs)
    if any(path.suffix.lower() in {".gz", ".bgz", ".bgzf"} for path in outputs):
        raise CodedError(
            "fastq_invalid_output",
            "Restore writes plain FASTQ; choose an uncompressed output name.",
        )
    result = current_result()
    result.add_input(source, "fastq_archive")
    codec = StreamCodec()
    try:
        with source.open("rb") as archive, _stage_outputs(outputs, (source,)) as staged:
            restore_archive(archive, staged, codec, checkpoint=checkpoint)
    except (OSError, FastqArchiveError, zlib.error) as error:
        raise CodedError("fastq_restore_failed", str(error)) from error
    for index, path in enumerate(outputs, 1):
        result.add_output(path, f"fastq_r{index}")


cmd_archive = cli_handler(FastqArchiveParams, run_archive)
cmd_restore = cli_handler(FastqRestoreParams, run_restore)
