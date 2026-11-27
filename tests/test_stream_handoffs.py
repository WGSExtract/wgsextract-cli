"""Real process and output checks for the streamed hand-offs in #1125."""

import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.htslib_helpers import needs_tools, sam_to_bam, vcf_calls
from tests.test_clair3 import clair3_gpu as clair3_gpu
from tests.test_clair3 import gpu_call, make_model
from tests.test_clair3 import linux_x86 as linux_x86
from wgsextract_cli.commands.alignment.output import run_streamed_alignment
from wgsextract_cli.core.atomic_output import atomic_output_mode
from wgsextract_cli.core.utils import WGSExtractError, run_command, run_pipeline


@needs_tools("samtools")
@pytest.mark.skipif(sys.platform != "linux", reason="observes Linux pipe capacity")
def test_1125_paused_consumer_blocks_the_alignment_producer(tmp_path: Path) -> None:
    samtools = shutil.which("samtools")
    if not samtools:
        pytest.skip("needs samtools on PATH to observe its direct pipe")
    header = "@SQ\tSN:chr1\tLN:1000"
    n = 24_000
    record = f"\t0\tchr1\t1\t60\t512M\t*\t0\t0\t{'ACGT' * 128}\t{'I' * 512}"
    bam = sam_to_bam(
        [header, *(f"r{i}{record}" for i in range(n))], tmp_path / "reads.bam"
    )
    blocked = tmp_path / "producer.blocked"
    producer = """
import os, select, subprocess, sys, time
from pathlib import Path
pipe = os.fstat(1)
poll = select.poll()
poll.register(1, select.POLLOUT)
with subprocess.Popen(sys.argv[2:]) as samtools:
    deadline = time.monotonic() + 10
    while samtools.poll() is None and poll.poll(0):
        if time.monotonic() >= deadline:
            samtools.kill()
            sys.exit("samtools did not reach a blocked pipe")
        time.sleep(0.01)
    if samtools.poll() is not None:
        sys.exit("samtools completed while the consumer was paused")
    Path(sys.argv[1]).write_text(f"{pipe.st_dev} {pipe.st_ino}")
    try:
        sys.exit(samtools.wait(timeout=15))
    except subprocess.TimeoutExpired:
        samtools.kill()
        sys.exit("samtools did not finish after the consumer resumed")
"""
    # Simulate a device consumer that cannot accept any more input until a
    # kernel finishes. The producer must still be writing when its pipe fills.
    consumer = """
import fcntl, os, sys, time
from pathlib import Path
blocked, samtools, bound = sys.argv[1:]
pipe = os.fstat(0)
capacity = fcntl.fcntl(0, fcntl.F_GETPIPE_SZ)
if capacity > int(bound):
    sys.exit(f"Pipe can buffer too much of the stream: {capacity} bytes")
deadline = time.monotonic() + 10
while True:
    try:
        device, inode = map(int, Path(blocked).read_text().split())
        if (device, inode) != (pipe.st_dev, pipe.st_ino):
            sys.exit("A relay instead of the producer owns the consumer's pipe")
        break
    except (FileNotFoundError, ValueError):
        pass
    if time.monotonic() >= deadline:
        sys.exit("samtools did not block writing to the paused consumer")
    time.sleep(0.01)
os.execv(samtools, [samtools, "view", "-c", "-"])
"""
    count = tmp_path / "consumed.txt"
    with count.open("wb") as sink:
        run_pipeline(
            [
                [sys.executable, "-c", producer, blocked, samtools, "view", "-h", bam],
                [
                    sys.executable,
                    "-c",
                    consumer,
                    blocked,
                    samtools,
                    str(n * len(record) // 16),
                ],
            ],
            stdout=sink,
            stderr_dir=tmp_path,
            description="Alignment hand-off to paused consumer",
        )
    assert int(count.read_text()) == n


@needs_tools("samtools")
@pytest.mark.parametrize("existing", [False, True], ids=["fresh", "replace"])
@pytest.mark.parametrize("failed_stage", ["view", "sort"], ids=["producer", "consumer"])
def test_1125_stream_failure_keeps_previous_indexed_alignment(
    tmp_path: Path, existing: bool, failed_stage: str
) -> None:
    sam = tmp_path / "reads.sam"
    sam.write_text(
        "@SQ\tSN:chr1\tLN:1000\nread\t0\tchr1\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\n"
    )
    ref = tmp_path / "ref.fa"
    ref.write_text(">chr1\n" + "ACGT" * 250 + "\n")
    run_command(["samtools", "faidx", ref])
    out = tmp_path / "out"
    out.mkdir()
    alignment = out / "aligned.cram"

    def align(reference: Path) -> None:
        run_streamed_alignment(
            ["samtools", "view", "-h", sam],
            str(alignment),
            threads="1",
            memory="64M",
            fmt="CRAM",
            ref=str(reference),
            duplicates=None,
        )

    with atomic_output_mode(True):
        if existing:
            align(ref)
        before = {path.name: path.read_bytes() for path in out.iterdir()}
        if failed_stage == "view":
            # A valid prefix reaches sort before the producer reports corruption.
            with sam.open("a") as stream:
                stream.write("truncated\t0\tchr1\t2\t60\t4M\n")
            expectation = r"samtools view \(exit \d+\):[\s\S]*Parse error"
        else:
            ref = tmp_path / "missing.fa"
            expectation = r"samtools sort \(exit \d+\):[\s\S]*missing\.fa"
        with pytest.raises(WGSExtractError, match=expectation):
            align(ref)

    assert {path.name: path.read_bytes() for path in out.iterdir()} == before


@needs_tools("samtools", "bcftools", "tabix")
@pytest.mark.parametrize("existing", [False, True], ids=["fresh", "replace"])
@pytest.mark.parametrize("failure", ["cuda-error", "missing-evidence"])
def test_1125_failed_gpu_call_preserves_the_entire_callset(
    tmp_path: Path, clair3_gpu: SimpleNamespace, existing: bool, failure: str
) -> None:
    model = make_model(tmp_path, "my_r1041_model")
    region = "chr1:900-1100"
    options = ("--reference-evidence", "gvcf", "--threads", "1")
    names = (
        "clair3.vcf.gz", "clair3.vcf.gz.tbi",
        "clair3.g.vcf.gz", "clair3.g.vcf.gz.tbi",
    )  # fmt: skip
    paths = [clair3_gpu.out / name for name in names]
    with atomic_output_mode(True):
        if existing:
            gpu_call(clair3_gpu, model, *options)
        before = {path: path.read_bytes() for path in paths if path.is_file()}
        entries = set(clair3_gpu.out.iterdir()) if clair3_gpu.out.exists() else set()
        if failure == "cuda-error":
            expected = "CUDA error: out of memory"
            clair3_gpu.clair3.fail(f"[ERROR] {expected}", after_vcf=True)
        else:
            expected = r"merge_output\.gvcf\.gz"
            clair3_gpu.clair3.lose_gvcf()
        with pytest.raises(WGSExtractError, match=expected):
            gpu_call(clair3_gpu, model, *options)

    assert {path: path.read_bytes() for path in paths if path.is_file()} == before
    assert set(clair3_gpu.out.iterdir()) == entries
    if existing:
        assert vcf_calls(paths[0], region) == clair3_gpu.reads.calls[:1]
        assert vcf_calls(paths[2], region)


@needs_tools("samtools", "bcftools", "tabix")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_1125_callset_replacement_does_not_expose_new_private_indexes(
    tmp_path: Path, clair3_gpu: SimpleNamespace
) -> None:
    model = make_model(tmp_path, "my_r1041_model")
    options = ("--reference-evidence", "gvcf", "--threads", "1")
    gpu_call(clair3_gpu, model, *options)
    for name in ("clair3.vcf.gz", "clair3.g.vcf.gz"):
        path = clair3_gpu.out / name
        path.chmod(0o600)
        path.with_name(name + ".tbi").unlink()
    gpu_call(clair3_gpu, model, *options)
    for name in ("clair3.vcf.gz", "clair3.g.vcf.gz"):
        assert (clair3_gpu.out / (name + ".tbi")).stat().st_mode & 0o077 == 0
