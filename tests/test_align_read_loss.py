"""Regression for unequal mates silently losing reads during alignment."""

import gzip
import os
import random
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, write_fasta, write_read_pairs
from wgsextract_cli import api
from wgsextract_cli.commands.align import run_streamed_alignment
from wgsextract_cli.core.atomic_output import atomic_output_mode
from wgsextract_cli.core.utils import WGSExtractError, run_command

pytestmark = needs_tools("bwa", "samtools")


@pytest.fixture
def reads(tmp_path):
    rng = random.Random(848)
    seq = "".join(rng.choice("ACGT") for _ in range(8000))
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": seq})
    r1, r2 = tmp_path / "reads_R1.fq", tmp_path / "reads_R2.fq"
    write_read_pairs(seq, r1, r2, [100, 500, 1000])
    return ref, r1, r2, seq


def _align(tmp_path, ref, r1, r2=None):
    return api.align.align(
        api.AlignParams(
            r1=r1, r2=r2, ref=ref, outdir=tmp_path / "aligned",
            aligner="bwa", native_aligners="off", threads=1, memory="16M",
        )
    )  # fmt: skip


@pytest.mark.parametrize(
    "damage", ["short_r1", "short_r2", "names", "fastq", "gzip", "unequal_bgz"]
)
def test_unequal_mates_and_truncated_reads_preserve_previous_alignment(
    tmp_path, reads, damage
):
    ref, r1, r2, _ = reads
    result = _align(tmp_path, ref, r1, r2)
    bam = tmp_path / "aligned/reads_aligned.bam"
    original = bam.read_bytes()
    index = bam.with_suffix(".bam.bai")
    original_index = index.read_bytes()
    if damage.startswith("short") or damage == "unequal_bgz":
        mate = r1 if damage == "short_r1" else r2
        mate.write_text("\n".join(mate.read_text().splitlines()[:-4]) + "\n")
        if damage == "unequal_bgz":
            compressed = r1.with_suffix(".data")
            compressed.write_bytes(gzip.compress(r1.read_bytes()))
            r1 = compressed
            compressed = r2.with_suffix(".fastq.bgz")
            compressed.write_bytes(gzip.compress(r2.read_bytes()))
            r2 = compressed
        message = "read count mismatch"
    elif damage == "names":
        r2.write_text(r2.read_text().replace("@p2", "@other"))
        message = "read name mismatch"
    elif damage == "fastq":
        r2.write_text(r2.read_text()[:-20])
        message = "truncated FASTQ"
    else:
        compressed = r2.with_suffix(".fq.gz")
        compressed.write_bytes(gzip.compress(r2.read_bytes())[:-6])
        r2 = compressed
        message = "complete FASTQ"
    with pytest.raises(api.CommandFailed, match=message):
        _align(tmp_path, ref, r1, r2)
    assert bam.read_bytes() == original
    assert index.read_bytes() == original_index
    assert result.metrics["input_reads"] == result.metrics["primary_records"] == 6


@pytest.mark.parametrize("paired", [True, False])
@pytest.mark.parametrize("wrapped", [False, True])
def test_matched_and_single_end_reads_have_exact_primary_accounting(
    tmp_path, reads, paired, wrapped
):
    ref, r1, r2, seq = reads
    # A chimeric read produces supplementary records; they are not extra reads.
    chimera = seq[2000:2400] + seq[6000:6400]
    with r1.open("a") as out:
        out.write(f"@chimera/1\n{chimera}\n+\n{'I' * len(chimera)}\n")
    if paired:
        with r2.open("a") as out:
            out.write(f"@chimera/2\n{seq[2500:2600]}\n+\n{'I' * 100}\n")
    if wrapped:
        for path in (r1, r2):
            lines = path.read_text().splitlines()
            path.write_text(
                "\n".join(
                    "\n".join((line[:40], line[40:])) if index % 4 in (1, 3) else line
                    for index, line in enumerate(lines)
                )
                + "\n"
            )
    result = _align(tmp_path, ref, r1, r2 if paired else None)
    bam = next(output.path for output in result.outputs if output.role == "alignment")
    primary = int(
        run_command(
            ["samtools", "view", "-c", "-F", "0x900", bam], capture_output=True
        ).stdout
    )
    supplementary = int(
        run_command(
            ["samtools", "view", "-c", "-f", "0x800", bam], capture_output=True
        ).stdout
    )
    assert primary == (8 if paired else 4)
    assert supplementary > 0
    assert result.metrics["input_reads"] == result.metrics["primary_records"] == primary


def test_primary_count_gate_ignores_secondary_records_and_preserves_output(
    tmp_path, reads
):
    """A successful process is not enough: validate its exact primary count."""
    ref, _, _, seq = reads
    sam = tmp_path / "mapper.sam"
    sam.write_text(
        "@HD\tVN:1.6\tSO:unsorted\n@SQ\tSN:chr1\tLN:8000\n"
        + "".join(
            f"p0\t{flag}\tchr1\t101\t60\t100M\t*\t0\t0\t{seq[100:200]}\t{'I' * 100}\n"
            for flag in (0, 256, 2048)
        )
    )
    bam = tmp_path / "mapped.bam"
    with atomic_output_mode(True):
        run_streamed_alignment(
            ["samtools", "view", "-h", str(sam)], str(bam),
            threads="1", memory="16M", fmt="BAM", ref=str(ref),
            duplicates=None, expected_reads=999, exact_reads=1,
        )  # fmt: skip
    previous = bam.read_bytes()
    assert (
        int(
            run_command(
                ["samtools", "view", "-c", str(bam)], capture_output=True
            ).stdout
        )
        == 3
    )
    with (
        atomic_output_mode(True),
        pytest.raises(WGSExtractError, match="2 input reads"),
    ):
        run_streamed_alignment(
            ["samtools", "view", "-h", str(sam)], str(bam),
            threads="1", memory="16M", fmt="BAM", ref=str(ref),
            duplicates=None, expected_reads=2, exact_reads=2,
        )  # fmt: skip
    assert bam.read_bytes() == previous


@pytest.mark.parametrize("tilde_mate", ["r1", "r2", "both"])
def test_home_directory_read_paths_survive_validation(
    tmp_path, reads, monkeypatch, tilde_mate
):
    ref, r1, r2, _ = reads
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    if tilde_mate in {"r1", "both"}:
        r1 = Path("~/reads_R1.fq")
    if tilde_mate in {"r2", "both"}:
        r2 = Path("~/reads_R2.fq")
    result = _align(tmp_path, ref, r1, r2)
    assert result.metrics["input_reads"] == result.metrics["primary_records"] == 6


def test_missing_reference_is_reported_before_malformed_fastq(tmp_path, reads):
    _, r1, r2, _ = reads
    r1.write_text("invalid FASTQ")
    with pytest.raises(api.CommandFailed) as error:
        _align(tmp_path, tmp_path / "missing.fa", r1, r2)
    assert "FASTQ" not in str(error.value)
    assert not list((tmp_path / "aligned").glob("*aligned.bam"))


def test_cancelling_read_validation_preserves_previous_alignment(tmp_path, reads):
    ref, r1, r2, seq = reads
    _align(tmp_path, ref, r1, r2)
    bam = tmp_path / "aligned/reads_aligned.bam"
    previous = bam.read_bytes()
    write_read_pairs(seq, r1, r2, [100] * 4096)
    token = api.CancelToken()

    def cancel_in_validation(event):
        if event.stage == "reads_validation":
            token.cancel()

    result = api.align.align(
        api.AlignParams(
            r1=r1, r2=r2, ref=ref, outdir=tmp_path / "aligned",
            aligner="bwa", native_aligners="off", threads=1, memory="16M",
        ),
        cancel=token, progress=cancel_in_validation, check=False,
    )  # fmt: skip
    assert result.status == "cancelled"
    assert result.exit_code == 130
    assert bam.read_bytes() == previous


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX read streams")
def test_named_fifo_reads_are_staged_without_losing_input(tmp_path, reads):
    ref, r1, r2, _ = reads
    fifo1, fifo2 = tmp_path / "pipe_R1.fq", tmp_path / "pipe_R2.fq"
    os.mkfifo(fifo1)
    os.mkfifo(fifo2)
    writer = subprocess.Popen(
        [
            sys.executable, "-c",
            "import sys; from pathlib import Path\n"
            "for source, target in zip(sys.argv[1:3], sys.argv[3:]):\n"
            " with open(target, 'wb') as out: out.write(Path(source).read_bytes())\n",
            str(r1), str(r2), str(fifo1), str(fifo2),
        ]
    )  # fmt: skip
    try:
        result = _align(tmp_path, ref, fifo1, fifo2)
        assert result.metrics["input_reads"] == result.metrics["primary_records"] == 6
        assert (tmp_path / "aligned/pipe_aligned.bam").is_file()
        assert not list((tmp_path / "aligned").glob(".wgse_reads_*"))
    finally:
        if writer.poll() is None:
            writer.terminate()
        writer.wait(timeout=10)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX standard input")
def test_piped_standard_input_is_validated_and_mapped_once(tmp_path, reads):
    ref, r1, _, _ = reads
    out = tmp_path / "piped"
    done = subprocess.run(
        [
            sys.executable, "-m", "wgsextract_cli.main", "align",
            "--r1", "/dev/stdin", "--platform", "illumina", "--ref", str(ref),
            "--aligner", "bwa", "--native-aligners", "off", "--threads", "1",
            "--memory", "16M", "--outdir", str(out),
        ],
        input=r1.read_bytes(), capture_output=True, timeout=60,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()
    count = run_command(
        ["samtools", "view", "-c", "-F", "0x900", str(out / "stdin_aligned.bam")],
        capture_output=True,
    )
    assert int(count.stdout) == 3
    assert not list(out.glob(".wgse_reads_*"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX named pipes")
def test_cancellation_interrupts_waiting_for_a_fifo_producer(tmp_path, reads):
    ref, _, _, _ = reads
    fifo = tmp_path / "waiting.fq"
    os.mkfifo(fifo)
    token = api.CancelToken()
    timer = threading.Timer(0.1, token.cancel)

    def cancel_when_staging(event):
        if event.stage == "reads_staging":
            timer.start()

    try:
        result = api.align.align(
            api.AlignParams(
                r1=fifo, ref=ref, outdir=tmp_path / "aligned", platform="illumina",
                aligner="bwa", native_aligners="off", threads=1, memory="16M",
            ),
            cancel=token, progress=cancel_when_staging, check=False,
        )  # fmt: skip
    finally:
        timer.cancel()
        if timer.ident is not None:
            timer.join(timeout=5)
    assert result.status == "cancelled"
    assert not list((tmp_path / "aligned").glob("*aligned.bam"))
    assert not list((tmp_path / "aligned").glob(".wgse_reads_*"))
