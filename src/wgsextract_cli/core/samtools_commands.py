import logging
import math
import platform
import re
from collections.abc import Iterable
from os import fspath
from pathlib import Path

from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.paths import PathInput, append_suffix


def alignment_index(path: PathInput) -> str | None:
    """The index of a BAM/CRAM (x.bam.bai, x.bai, x.cram.crai, ...), if any."""
    file = Path(path)
    candidates = [append_suffix(file, ext) for ext in (".bai", ".csi", ".crai")]
    candidates += [file.with_suffix(".bai"), file.with_suffix(".crai")]
    return next((str(index) for index in candidates if index.exists()), None)


def written_index(path: PathInput) -> str:
    """The index ``--write-index`` writes beside a BAM or CRAM: x.bam.bai or
    x.cram.crai. Unnamed, samtools would write a CSI for BAM, which htsjdk
    tools such as GATK can't read."""
    file = fspath(path)
    return file + (".crai" if file.lower().endswith(".cram") else ".bai")


def indexed_output(path: PathInput) -> str:
    """The output name that makes samtools ``--write-index`` write
    written_index(path) as it writes path, without a separate index pass."""
    return f"{fspath(path)}##idx##{written_index(path)}"


def parse_memory(memory: str) -> tuple[float, str]:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([GgMm])(?:[Bb])?\s*", memory)
    if not match:
        raise ValueError(f"Invalid memory value: {memory}")
    return float(match.group(1)), match.group(2).upper()


def _format_memory(amount: float, unit: str) -> str:
    return f"{amount:g}{unit}"


_MIB = 1024**2
# A read file decodes to at most this many bytes of records: even
# reference-compressed CRAM with binned qualities holds fewer than 16 bases per
# byte, and a record takes about 5.4 bytes per base.
DECODED_BYTES_PER_INPUT_BYTE = 96
# samtools sort mallocs all of -@ × -m when it starts, and the kernel refuses
# an allocation beyond RAM plus swap, however few reads arrive (#781).
SORT_RAM_FRACTION = 0.5
# The least an input-filled buffer is lowered to: buffers this small only hold
# inputs smaller than they are.
_MIN_SORT_MIB = 16


def regular_files_bytes(paths: Iterable[PathInput]) -> int | None:
    """The total size of ``paths``, or None if one is not a regular file
    (a URL, pipe or device reports no size)."""
    files = [Path(path) for path in paths]
    if not all(file.is_file() for file in files):
        return None
    try:
        return sum(file.stat().st_size for file in files)
    except OSError:
        return None


def sort_memory(
    threads: str, memory: str, *, input_bytes: int | None = None, log: bool = True
) -> str:
    """The per-thread memory (-m) to give samtools (or sambamba) sort for a
    ``memory`` budget: no more than ``input_bytes`` of read files can fill
    (but at least 16M), and with -@ × -m at most SORT_RAM_FRACTION of this
    computer's RAM, so the sort can reserve its buffers. ``memory`` itself is
    returned when it is no larger; ``log`` says when it is lowered."""
    import psutil

    count = max(1, int(threads))
    amount, unit = parse_memory(memory)
    requested = amount * (1024 if unit == "G" else 1)
    total = int(psutil.virtual_memory().total)
    chosen = int(total * SORT_RAM_FRACTION) // count // _MIB
    reason = (
        f"-@ × -m stays within half of this computer's {total / 1024**3:.1f} GiB RAM"
    )
    if input_bytes is not None:
        fill = max(0, input_bytes) * DECODED_BYTES_PER_INPUT_BYTE
        filled = max(math.ceil(fill / count / _MIB), _MIN_SORT_MIB)
        if filled < chosen:
            chosen = filled
            reason = f"{input_bytes / 1e6:.1f} MB of reads fill at most {fill / _MIB:.0f} MiB"
    # samtools refuses less than 1M a thread.
    chosen = max(chosen, 1)
    if chosen >= requested:
        return memory
    if log:
        logging.info(
            "Lowering samtools sort memory from %s to %dM per thread (%d threads): "
            "%s, and the sort reserves all of -@ × -m when it starts.",
            memory,
            chosen,
            count,
            reason,
        )
    return f"{chosen}M"


def _use_sambamba() -> bool:
    """sambamba is preferred where installed, except on macOS."""
    return platform.system() != "Darwin" and get_tool_path("sambamba") is not None


def get_sam_sort_cmd(
    out_file: PathInput,
    threads: str,
    memory: str,
    fmt: str = "BAM",
    reference: PathInput | None = None,
    name_sort: bool = False,
    temp_dir: PathInput | None = None,
    sam_input: bool = False,
    write_index: bool = False,
) -> list[str]:
    """
    Return a command list for sorting BAM/CRAM read from stdin.
    Uses sambamba for BAM output when available, else samtools. sambamba sort
    cannot read SAM, so pass sam_input=True for aligner output streams.
    write_index has samtools index the BAM/CRAM as it writes it (written_index).
    """
    from wgsextract_cli.core.temp_directory import configured_temp_directory

    if temp_dir is None:
        temp_dir = configured_temp_directory()
    threads_val = int(threads)
    mem_val, mem_unit = parse_memory(memory)

    if fmt == "BAM" and not sam_input and not write_index and _use_sambamba():
        total_mem = mem_val * threads_val
        total_mem_str = _format_memory(total_mem, mem_unit)
        cmd = [
            "sambamba",
            "sort",
            "-t",
            threads,
            "-m",
            total_mem_str,
            "-o",
            fspath(out_file),
            "/dev/stdin",
        ]
        if name_sort:
            cmd.insert(2, "-n")
        if temp_dir:
            cmd.insert(2, "--tmpdir")
            cmd.insert(3, fspath(temp_dir))
        return cmd

    if write_index and (name_sort or fmt not in ("BAM", "CRAM")):
        raise ValueError("Only a coordinate-sorted BAM or CRAM can be indexed")
    out = indexed_output(out_file) if write_index else fspath(out_file)
    cmd = ["samtools", "sort", "-@", threads, "-m", memory, "-o", out]
    if write_index:
        cmd.append("--write-index")
    if name_sort:
        cmd.append("-n")
    if temp_dir:
        cmd += ["-T", fspath(temp_dir)]
    elif write_index:
        # samtools before 1.14 derives the spill names from -o, ##idx## and
        # all, and htslib cuts each at ##idx##: every spill opens as out_file.
        cmd += ["-T", f"{fspath(out_file)}.tmp"]
    if fmt == "CRAM":
        cmd += ["-O", "CRAM"]
        if reference:
            cmd += ["--reference", fspath(reference)]
    elif fmt == "SAM":
        cmd += ["-O", "SAM"]
    else:
        cmd += ["-O", "BAM"]
    return cmd


def get_sam_index_cmd(file_path: PathInput, threads: str = "1") -> list[str]:
    """
    Return a command list for indexing BAM/CRAM.
    Uses sambamba if available (except on macOS) and file is BAM, else samtools.
    """
    file = fspath(file_path)
    if file.lower().endswith(".bam") and _use_sambamba():
        return ["sambamba", "index", "-t", threads, file]
    return ["samtools", "index", "-@", threads, file]


def get_sam_view_cmd(
    threads: str = "1",
    fmt: str = "BAM",
    reference: PathInput | None = None,
    is_input_sam: bool = False,
) -> list[str]:
    """
    Return a command list for viewing/converting BAM/CRAM.
    Uses sambamba if available (except on macOS) and fmt is BAM, else samtools.
    """
    if fmt == "BAM" and not reference and _use_sambamba():
        cmd = ["sambamba", "view", "-t", threads, "-f", "bam"]
        if is_input_sam:
            cmd += ["-S"]
        return cmd

    cmd = ["samtools", "view", "-@", threads]
    if fmt == "CRAM":
        cmd += ["-O", "CRAM"]
    elif fmt == "BAM":
        cmd += ["-b"]
    if reference:
        # Also decodes CRAM input with it.
        cmd += ["-T", fspath(reference)]

    return cmd
