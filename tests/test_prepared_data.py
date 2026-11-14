import ctypes
import os
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests import htslib_helpers
from tests.htslib_helpers import needs_htslib, sam_to_bam, write_fasta
from tests.prepared_data import prepared_files, sparse_truncate
from wgsextract_cli.core.file_lock import build_lock
from wgsextract_cli.core.utils import run_command


@needs_htslib
def test_761_cached_genomes_and_indexes_are_private_writable_copies(tmp_path):
    first = write_fasta(tmp_path / "first.fa", {"chr1": "ACGT"})
    second = write_fasta(tmp_path / "second.fa", {"chr1": "ACGT"})
    first.write_text("changed")
    Path(str(first) + ".fai").unlink()
    assert second.read_text() == ">chr1\nACGT\n"
    assert (
        run_command(
            ["samtools", "faidx", str(second), "chr1:2-3"], capture_output=True
        ).stdout
        == ">chr1:2-3\nCG\n"
    )
    changed = write_fasta(tmp_path / "changed.fa", {"chr1": "TGCA"})
    assert (
        run_command(
            ["samtools", "faidx", str(changed), "chr1:2-3"], capture_output=True
        ).stdout
        == ">chr1:2-3\nGC\n"
    )

    header = ["@SQ\tSN:chr1\tLN:4"]
    row = "read\t0\tchr1\t1\t60\t4M\t*\t0\t0\tACGT\tIIII"
    original = sam_to_bam([*header, row], tmp_path / "first.bam")
    copied = sam_to_bam([*header, row], tmp_path / "second.bam")
    Path(original).write_bytes(b"changed")
    Path(original + ".bai").unlink()
    assert (
        run_command(
            ["samtools", "view", copied, "chr1"], capture_output=True
        ).stdout.rstrip()
        == row
    )
    empty = sam_to_bam(header, tmp_path / "empty.bam")
    assert (
        run_command(
            ["samtools", "view", "-c", empty], capture_output=True
        ).stdout.strip()
        == "0"
    )


def test_761_large_metadata_files_do_not_allocate_gigabytes_on_windows(tmp_path):
    path = tmp_path / "large.bam"
    path.write_bytes(b"header")
    sparse_truncate(path, 8 * 1024**3)
    assert path.stat().st_size == 8 * 1024**3
    with path.open("rb") as stream:
        assert stream.read(6) == b"header"
        stream.seek(-4, os.SEEK_END)
        assert stream.read() == b"\0" * 4
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        size = kernel.GetCompressedFileSizeW
        size.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
        size.restype = wintypes.DWORD
        high = wintypes.DWORD()
        low = size(str(path), ctypes.byref(high))
        assert low != 0xFFFFFFFF or ctypes.get_last_error() == 0
        allocated = low + (high.value << 32)
    else:
        allocated = path.stat().st_blocks * 512
    assert allocated < 1024**2


def test_761_interrupted_fixture_repair_never_publishes_partial_inputs(tmp_path):
    interrupted = False

    def build(root: Path) -> None:
        (root / "left").write_bytes(b"partial" if interrupted else b"complete")
        if interrupted:
            raise OSError("builder interrupted")
        (root / "right").write_bytes(b"complete")

    args = (str(tmp_path).encode(), sys.executable, ("left", "right"), build)
    root = prepared_files(*args)
    (root / "left").unlink()
    interrupted = True
    with pytest.raises(OSError, match="interrupted"):
        prepared_files(*args)
    interrupted = False
    recovered = prepared_files(*args)
    assert (recovered / "left").read_bytes() == b"complete"
    assert (recovered / "right").read_bytes() == b"complete"


def test_761_complete_fixtures_remain_readable_while_a_builder_holds_the_lock(
    tmp_path,
):
    def build(root: Path) -> None:
        (root / "input").write_bytes(b"complete")

    args = (str(tmp_path).encode(), sys.executable, ("input",), build)
    root = prepared_files(*args)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with build_lock(str(root) + ".lock"):
            result = pool.submit(prepared_files, *args).result(timeout=5)
            assert (result / "input").read_bytes() == b"complete"


@needs_htslib
def test_761_fixture_tools_accept_pixi_wrapper_commands(tmp_path, monkeypatch):
    pixi = shutil.which("pixi")
    if pixi is None:
        pytest.skip("Pixi needed to exercise a real tool launcher")
    args = [
        pixi,
        "run",
        "--no-install",
        "--manifest-path",
        str(Path(__file__).resolve().parents[1] / "pixi.toml"),
        "-e",
        "dev",
        "samtools",
    ]
    command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
    # OS boundary: dependency discovery can return a launch command, not a file.
    monkeypatch.setattr(htslib_helpers, "get_tool_path", lambda _name: command)
    fasta = write_fasta(tmp_path / "wrapped.fa", {"chr1": "ACGT"})
    assert (
        run_command(
            [command, "faidx", str(fasta), "chr1:2-3"], capture_output=True
        ).stdout
        == ">chr1:2-3\nCG\n"
    )
    row = "read\t0\tchr1\t1\t60\t4M\t*\t0\t0\tACGT\tIIII"
    bam = sam_to_bam(["@SQ\tSN:chr1\tLN:4", row], tmp_path / "wrapped.bam")
    assert (
        run_command([command, "view", bam, "chr1"], capture_output=True).stdout.rstrip()
        == row
    )
