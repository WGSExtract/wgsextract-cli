"""Create and restore lossless, reference-free FASTQ archives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.results import CommandResult

from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import input_param


@dataclass(frozen=True, kw_only=True)
class FastqArchiveParams(Params):
    """Archive a single-end FASTQ or a synchronized pair without changing the input."""

    COMMAND = "fastq archive"

    r1: Path = param(doc="First or only FASTQ; plain, gzip or BGZF.", path="file")
    r2: Path | None = param(None, doc="Matching read 2 FASTQ.", path="file")
    output: Path = param(doc="New .wgfq archive to write.", path="file", writes=True)


@dataclass(frozen=True, kw_only=True)
class FastqRestoreParams(Params):
    """Verify a FASTQ archive and restore the original, uncompressed FASTQ bytes."""

    COMMAND = "fastq restore"

    input: Path = input_param("The .wgfq archive to restore.")
    output: Path = param(
        doc="New plain FASTQ for read 1 (or single-end reads).",
        path="file",
        writes=True,
    )
    output_r2: Path | None = param(
        None,
        doc="New plain FASTQ for read 2; required for a paired archive.",
        path="file",
        writes=True,
    )


def archive(
    params: FastqArchiveParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Write a new archive (output role ``fastq_archive``)."""
    from wgsextract_cli.commands.fastq import run_archive

    return execute(params, run_archive, progress=progress, cancel=cancel, check=check)


def restore(
    params: FastqRestoreParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Restore verified reads (output roles ``fastq_r1`` and ``fastq_r2``)."""
    from wgsextract_cli.commands.fastq import run_restore

    return execute(params, run_restore, progress=progress, cancel=cancel, check=check)


__all__ = ["FastqArchiveParams", "FastqRestoreParams", "archive", "restore"]
