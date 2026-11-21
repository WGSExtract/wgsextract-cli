import json
import os
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, sam_to_bam
from tests.prepared_data import sparse_truncate
from tests.test_alignment_commands import _samtools, _short_reads
from tests.test_staged_alignment import _align, _stage_reads
from wgsextract_cli.commands._extract_helpers import paired_fastq_stream
from wgsextract_cli.commands.align import read_group, run_streamed_alignment
from wgsextract_cli.commands.alignment.short_reads import bwa_index_suffixes
from wgsextract_cli.core import results
from wgsextract_cli.core.staged_alignment import run_staged_alignment, workspace_path
from wgsextract_cli.core.utils import WGSExtractError, run_command


@needs_tools("bwa", "samtools", "samblaster")
def test_issue1206_bwa_resume_ignores_changed_mem2_sidecar(tmp_path):
    reads = _short_reads(tmp_path)
    baseline, _ = _align(reads, reads.ref, tmp_path / "baseline")
    unused = Path(reads.ref + ".0123")
    unused.write_bytes(b"mem2 index")
    with pytest.raises(WGSExtractError):
        _stage_reads(tmp_path, reads, staged_output=str(tmp_path))
    workspace = workspace_path(str(tmp_path / "staged.bam"))
    checkpoint = (workspace / "parts.json").read_bytes()

    unused.write_bytes(b"changed mem2 index")
    _stage_reads(tmp_path, reads)

    assert (workspace / "parts.json").read_bytes() == checkpoint
    assert _samtools("view", tmp_path / "staged.bam") == _samtools("view", baseline)


@pytest.mark.parametrize("index_suffix", [".0123", ".bwt.2bit.64"])
@needs_tools("bwa", "bwa-mem2", "samtools", "samblaster")
def test_issue1206_resume_rejects_same_size_edit_outside_sampled_index_blocks(
    tmp_path, index_suffix
):
    reads = _short_reads(tmp_path)
    run_command(["bwa-mem2", "index", reads.ref])
    index = Path(reads.ref + index_suffix)
    # Extend the real index, leaving the part the mapper loads unchanged.
    sparse_truncate(index, 128 * 1024**2)
    with pytest.raises(WGSExtractError):
        _stage_reads(tmp_path, reads, aligner="bwa-mem2", staged_output=str(tmp_path))
    workspace = workspace_path(str(tmp_path / "staged.bam"))
    assert json.loads((workspace / "parts.json").read_text())["complete"]
    before = index.stat()

    with index.open("r+b") as stream:
        stream.seek(32 * 1024**2)  # In the gap between the first and middle blocks.
        stream.write(b"\xff")
    os.utime(index, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
    assert index.stat().st_size == before.st_size
    with pytest.raises(WGSExtractError, match="checkpoint inputs/settings changed"):
        _stage_reads(tmp_path, reads, aligner="bwa-mem2")


@needs_tools("bwa", "samtools", "samblaster")
def test_issue1206_stream_resume_still_warns_about_discarded_reads(tmp_path):
    reads = _short_reads(tmp_path)
    baseline, _ = _align(reads, reads.ref, tmp_path / "baseline")
    bam = sam_to_bam(
        [
            *_samtools("view", "-h", baseline).splitlines(),
            "extra\t4\t*\t0\t0\t*\t*\t0\t0\tACGT\tIIII",
        ],
        tmp_path / "mixed.bam",
    )
    output = str(tmp_path / "recovered.bam")
    aligner = ["bwa", "mem", "-p", "-R", read_group("reads"), Path(reads.ref), "-"]

    def run(staged_output):
        with paired_fastq_stream(
            bam, threads="1", memory="16M", temp_parent=tmp_path
        ) as extracted:
            run_staged_alignment(
                [*extracted.stages, aligner],
                output,
                staged_output,
                memory="16M",
                fmt="BAM",
                ref=reads.ref,
                index_suffixes=bwa_index_suffixes("bwa"),
                discarded_fastq=extracted.discarded_fastq,
                expected_reads=281,
            )

    initial = results.CommandResult(command="realign")
    with results.collecting(initial), pytest.raises(WGSExtractError):
        run(str(tmp_path))  # Encoding cannot write BAM to a directory.
    assert not list(tmp_path.glob(".collate_*"))
    workspace = workspace_path(output)
    checkpoint = (workspace / "parts.json").read_bytes()

    result = results.CommandResult(command="realign")
    with results.collecting(result):
        run(output)

    assert (workspace / "parts.json").read_bytes() == checkpoint
    warnings = [w for w in result.warnings if w.code == "paired_fastq_discarded_reads"]
    assert len(warnings) == 1 and warnings[0].details["reads"] == 1
    assert any(w.code == warnings[0].code for w in initial.warnings)
    assert len(_samtools("view", output).splitlines()) == 280

    streamed = results.CommandResult(command="realign")
    with (
        results.collecting(streamed),
        paired_fastq_stream(
            bam, threads="1", memory="16M", temp_parent=tmp_path
        ) as extracted,
    ):
        run_streamed_alignment(
            aligner,
            str(tmp_path / "ordinary.bam"),
            threads="1",
            memory="16M",
            fmt="BAM",
            ref=reads.ref,
            duplicates="samblaster",
            upstream=extracted.stages,
            discarded_fastq=extracted.discarded_fastq,
        )
    assert [
        w.details["reads"] for w in streamed.warnings if w.code == warnings[0].code
    ] == [1]
