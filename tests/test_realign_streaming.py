"""Focused realign streaming regressions."""

import logging
import os
import platform
import sys
from pathlib import Path

import pytest

from tests.fake_tools import FakeParabricks
from tests.htslib_helpers import needs_tools, sam_to_bam, write_fasta
from tests.test_realign import (
    _args as _args,
)
from tests.test_realign import (
    _Fakes as _Fakes,
)
from tests.test_realign import (
    _old_alignment as _old_alignment,
)
from tests.test_realign import (
    _view as _view,
)
from tests.test_realign import (
    _visible as _visible,
)
from tests.test_realign import (
    genome_and_ref as genome_and_ref,
)
from tests.test_realign import (
    roomy_disk as roomy_disk,
)
from tests.test_realign import (
    source as source,
)
from wgsextract_cli.commands import realign
from wgsextract_cli.commands.alignment import common, preparation, short_reads
from wgsextract_cli.commands.realignment import steps as realign_steps
from wgsextract_cli.core import parabricks
from wgsextract_cli.core.alignment import memory as alignment_memory
from wgsextract_cli.core.atomic_output import atomic_output_mode
from wgsextract_cli.core.utils import WGSExtractError, run_command


def test_existing_fastqs_are_reused_and_kept(tmp_path, monkeypatch, source):
    fakes = _Fakes(monkeypatch)
    pair = (str(tmp_path / "x_R1.fq.gz"), str(tmp_path / "x_R2.fq.gz"))
    for path in pair:
        Path(path).write_text("reads")

    realign.run(_args(source))

    assert fakes.extracted == [] and fakes.aligned == [pair]
    assert all(Path(p).read_text() == "reads" for p in pair)


@needs_tools("samtools")
def test_mem2_realign_prepares_missing_fai_before_index_preflight(
    tmp_path, monkeypatch, source
):
    fakes = _Fakes(monkeypatch)
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 8 * 1024**3)
    ref = tmp_path / "ref.fa"
    ref.write_text(">chr1\nACGT\n")
    pair = (str(tmp_path / "r1.fq"), str(tmp_path / "r2.fq"))
    for path in pair:
        Path(path).write_text("@r\nA\n+\nI\n")
    assert not Path(str(ref) + ".fai").exists()
    assert not Path(str(ref) + ".ann").exists()

    realign.run(_args(source, ref=str(ref), aligner="bwa-mem2", r1=pair[0], r2=pair[1]))

    assert Path(str(ref) + ".fai").read_text().split("\t")[:2] == ["chr1", "4"]
    assert fakes.aligned == [pair]


def test_force_extract_streams_without_touching_existing_fastqs(
    tmp_path, monkeypatch, source
):
    fakes = _Fakes(monkeypatch)
    for name in ("x_R1.fastq.gz", "x_R2.fastq.gz"):
        (tmp_path / name).write_text("stale")
    outdir = tmp_path / "out"

    realign.run(_args(source, outdir=str(outdir), force_extract=True))

    pair = (str(outdir / "x_R1.fastq.gz"), str(outdir / "x_R2.fastq.gz"))
    assert fakes.extracted == [] and fakes.aligned == [pair]
    assert fakes.streamed[0]["read_paths"] == [str(source)]
    assert not any(Path(p).exists() for p in pair)
    # The pair beside the input is not this run's to delete.
    assert (tmp_path / "x_R1.fastq.gz").read_text() == "stale"


def test_reads_passed_in_are_aligned_and_never_deleted(tmp_path, monkeypatch, source):
    fakes = _Fakes(monkeypatch)
    r1 = tmp_path / "reads.fq"
    r1.write_text("reads")

    realign.run(_args(source, r1=str(r1)))

    assert fakes.extracted == [] and fakes.aligned == [(str(r1), None)]
    assert r1.exists()
    with pytest.raises(WGSExtractError, match="--r2"):
        realign.run(_args(source, r1=str(r1), r2=str(tmp_path / "gone.fq")))


@pytest.mark.parametrize("error", [WGSExtractError("boom"), KeyboardInterrupt()])
def test_failed_extraction_leaves_no_truncated_fastqs(
    tmp_path, monkeypatch, source, error
):
    fakes = _Fakes(monkeypatch)

    def truncated(_input, r1, r2, **_kwargs):
        Path(r1).write_text("@r\nAC")
        raise error

    monkeypatch.setattr(realign_steps, "alignments_to_fastq", truncated)
    with pytest.raises(type(error)):
        realign.run(_args(source, keep_fastq=True))
    assert list(tmp_path.glob("*.fastq.gz")) == []
    assert fakes.aligned == []


def test_failed_alignment_keeps_fastqs_for_the_rerun(tmp_path, monkeypatch, source):
    fakes = _Fakes(monkeypatch)
    fakes.align_error = WGSExtractError("bwa alignment failed")
    with pytest.raises(WGSExtractError, match="bwa alignment failed"):
        realign.run(_args(source, keep_fastq=True))
    pair = (str(tmp_path / "x_R1.fastq.gz"), str(tmp_path / "x_R2.fastq.gz"))
    assert all(Path(p).is_file() for p in pair)

    fakes.align_error = None
    realign.run(_args(source))
    assert fakes.extracted == [pair] and fakes.aligned == [pair, pair]
    # Reused, not extracted by this run: left in place.
    assert all(Path(p).is_file() for p in pair)


@pytest.mark.parametrize("error", [WGSExtractError("boom"), KeyboardInterrupt()])
def test_stream_failure_leaves_no_fastqs_or_collate_spills(monkeypatch, source, error):
    fakes = _Fakes(monkeypatch)
    fakes.align_error = error
    with pytest.raises(type(error)):
        realign.run(_args(source))
    assert fakes.extracted == [] and len(fakes.streamed) == 1
    assert list(source.parent.glob("*.fastq.gz")) == []
    assert list(source.parent.glob(".collate_*")) == []


@needs_tools("samtools")
@pytest.mark.parametrize("cram", [False, True], ids=["bam", "cram"])
@pytest.mark.parametrize("markdup", [False, True], ids=["auto-marker", "markdup"])
@pytest.mark.parametrize("aligner", ["bwa", "bwa-mem2", "minimap2"])
def test_paired_stream_body_matches_files_including_discarded_reads(
    tmp_path, genome_and_ref, monkeypatch, cram, markdup, aligner
):
    if realign.align_cmd.get_tool_path(aligner) is None:
        pytest.skip(f"{aligner} unavailable")
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 8 * 1024**3)
    for module in (realign.align_cmd, preparation, short_reads):
        monkeypatch.setattr(module, "streaming_mapping_fits", lambda *_a, **_kw: True)
    genome, ref = genome_and_ref
    source, pairs = _old_alignment(tmp_path, genome)
    records = _view("view", source).splitlines()
    extra = []
    for flag in (0x100, 0x800):
        fields = records[0].split("\t")
        fields[1] = str(int(fields[1]) | flag)
        extra.append("\t".join(fields))
    extra.append(
        f"singleton\t73\told1\t1\t60\t100M\t*\t0\t0\t{genome[:100]}\t{'I' * 100}"
    )
    tie = records[0].split("\t")
    tie[10] = "!" * len(tie[10])
    extra.append("\t".join(tie))
    source = sam_to_bam(
        [*_view("view", "-H", source).splitlines(), *records, *extra],
        tmp_path / "old" / "filtered.bam",
    )
    input_ref = None
    if cram:
        input_ref = str(write_fasta(tmp_path / "old" / "encode.fa", {"old1": genome}))
        encoded = tmp_path / "old" / "filtered.cram"
        run_command(["samtools", "view", "-C", "-T", input_ref, "-o", encoded, source])
        run_command(["samtools", "index", encoded])
        source = str(encoded)
    if markdup:
        original = realign.align_cmd.get_tool_path
        for module in (realign.align_cmd, common, preparation, short_reads):
            monkeypatch.setattr(
                module,
                "get_tool_path",
                lambda tool: None if tool == "samblaster" else original(tool),
            )
    for mode in ("files", "stream"):
        realign.run(
            _args(
                source,
                ref=ref,
                input_ref=input_ref,
                aligner=aligner,
                outdir=str(tmp_path / mode),
                keep_fastq=mode == "files",
                **({"native_aligners": "off"} if aligner == "minimap2" else {}),
            )
        )
    files = tmp_path / "files" / "filtered_aligned.bam"
    stream = tmp_path / "stream" / "filtered_aligned.bam"
    body = _view("view", stream)
    assert body == _view("view", files)
    assert len(body.splitlines()) == pairs * 2
    assert "singleton" not in body
    header = _view("view", "-H", stream)
    if aligner == "minimap2":
        # #1125: the default run never extracted a seekable mate pair.
        assert ".fastq.gz" not in header
    else:
        assert "-p " in header
    assert ".fastq.gz" in _view("view", "-H", files)
    assert not list((tmp_path / "stream").glob("*.fastq.gz"))
    assert not list((tmp_path / "stream").glob(".collate_*"))


@needs_tools("minimap2", "samtools")
def test_1125_minimap2_memory_fallback_keeps_extraction_step(
    tmp_path, genome_and_ref, monkeypatch, caplog
):
    genome, ref = genome_and_ref
    source, pairs = _old_alignment(tmp_path, genome)
    monkeypatch.setattr(realign_steps, "available_memory", lambda: 256 * 1024**2)
    with caplog.at_level(logging.INFO):
        realign.run(
            _args(
                source,
                ref=ref,
                aligner="minimap2",
                outdir=tmp_path / "fallback",
                native_aligners="off",
            )
        )
    alignment = tmp_path / "fallback" / "x_aligned.bam"
    assert len(_view("view", alignment).splitlines()) == pairs * 2
    assert ".fastq.gz" in _view("view", "-H", alignment)
    assert "does not fit available RAM" in caplog.text


@needs_tools("minimap2", "samtools")
def test_1125_forced_minimap2_extract_replaces_cached_mates(tmp_path, genome_and_ref):
    genome, ref = genome_and_ref
    source, pairs = _old_alignment(tmp_path, genome)
    outdir = tmp_path / "out"
    options = {
        "ref": ref,
        "aligner": "minimap2",
        "outdir": outdir,
        "native_aligners": "off",
    }
    realign.run(_args(source, **options, keep_fastq=True))
    for path in outdir.glob("*.fastq.gz"):
        path.write_bytes(b"stale FASTQ")
    realign.run(_args(source, **options, force_steps=("extract",)))
    assert not list(outdir.glob("*.fastq.gz"))
    assert len(_view("view", outdir / "x_aligned.bam").splitlines()) == pairs * 2


@needs_tools("minimap2", "samtools")
def test_1125_failed_late_memory_fallback_keeps_previous_alignment(
    tmp_path, genome_and_ref, monkeypatch, caplog
):
    genome, ref = genome_and_ref
    source, _ = _old_alignment(tmp_path, genome)
    outdir = tmp_path / "out"
    output = outdir / "x_aligned.bam"
    options = {
        "ref": ref,
        "aligner": "minimap2",
        "outdir": outdir,
        "native_aligners": "off",
    }
    with atomic_output_mode(True):
        realign.run(_args(source, **options))
        original = output.read_bytes()
        monkeypatch.setattr(realign_steps, "available_memory", lambda: 8 * 1024**3)
        monkeypatch.setattr(short_reads, "available_memory", lambda: 256 * 1024**2)
        replace_file = os.replace

        def refuse_publish(src, dst, **kwargs):
            if Path(dst) == output:
                raise PermissionError("could not replace previous BAM")
            return replace_file(src, dst, **kwargs)

        monkeypatch.setattr(os, "replace", refuse_publish)
        with (
            caplog.at_level(logging.INFO),
            pytest.raises(WGSExtractError, match="could not replace previous BAM"),
        ):
            realign.run(_args(source, **options, sample="new-sample"))
    assert "Available RAM changed before streaming started" in caplog.text
    assert output.read_bytes() == original
    _view("quickcheck", output)


@needs_tools("bwa", "samtools")
@pytest.mark.parametrize("keep_fastq", [False, True], ids=["stream", "files"])
def test_single_end_reads_are_refused_before_extraction(
    tmp_path, genome_and_ref, monkeypatch, keep_fastq
):
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 8 * 1024**3)
    for module in (realign.align_cmd, preparation, short_reads):
        monkeypatch.setattr(module, "streaming_mapping_fits", lambda *_a, **_kw: True)
    genome, ref = genome_and_ref
    lines = ["@HD\tVN:1.6\tSO:unsorted", f"@SQ\tSN:old1\tLN:{len(genome)}"]
    for i, flag in enumerate((0, 4, 0)):
        seq = genome[i * 100 : i * 100 + 100] if flag == 0 else "A" * 100
        pos = (i * 100 + 1, "100M") if flag == 0 else (0, "*")
        rname = "old1" if flag == 0 else "*"
        lines.append(
            f"se{i}\t{flag}\t{rname}\t{pos[0]}\t60\t{pos[1]}\t*\t0\t0\t{seq}\t{'I' * 100}"
        )
    source = sam_to_bam(lines, tmp_path / "old" / "single.bam")
    outdir = tmp_path / "out"
    with pytest.raises(WGSExtractError, match=r"3 of 3 sampled .* \(e\.g\. se0\)"):
        realign.run(_args(source, ref=ref, outdir=str(outdir), keep_fastq=keep_fastq))
    assert _visible(outdir) == []


@needs_tools("bwa", "samtools")
def test_streamed_cram_output_matches_file_path(tmp_path, genome_and_ref, monkeypatch):
    for module in (realign.align_cmd, preparation, short_reads):
        monkeypatch.setattr(module, "streaming_mapping_fits", lambda *_a, **_kw: True)
    genome, ref = genome_and_ref
    source, _ = _old_alignment(tmp_path, genome)
    for mode in ("files", "stream"):
        realign.run(
            _args(
                source,
                ref=ref,
                aligner="bwa",
                format="CRAM",
                outdir=str(tmp_path / mode),
                keep_fastq=mode == "files",
            )
        )
    outputs = [tmp_path / mode / "x_aligned.cram" for mode in ("files", "stream")]
    assert _view("view", "-T", ref, str(outputs[0])) == _view(
        "view",
        "-T",
        ref,
        str(outputs[1]),
    )
    assert Path(str(outputs[1]) + ".crai").is_file()


@needs_tools("bwa", "samtools")
def test_native_stream_failure_removes_partial_output_and_collation(
    tmp_path,
    genome_and_ref,
    monkeypatch,
):
    for module in (realign.align_cmd, preparation, short_reads):
        monkeypatch.setattr(module, "streaming_mapping_fits", lambda *_a, **_kw: True)
    genome, ref = genome_and_ref
    source, _ = _old_alignment(tmp_path, genome)
    monkeypatch.setattr(preparation, "ensure_bwa_index", lambda _ref: None)
    outdir = tmp_path / "failure"
    with pytest.raises(WGSExtractError, match="bwa alignment failed"):
        realign.run(_args(source, ref=ref, outdir=str(outdir), aligner="bwa"))
    assert _visible(outdir) == []


@pytest.mark.skipif(
    sys.platform == "win32", reason="the fake GPU host's tools are shell scripts"
)
def test_cleanup_problem_does_not_fail_a_finished_realign(
    tmp_path, monkeypatch, source, caplog, ram
):
    # A GPU realign always goes through FASTQs; run it on a fake GPU host.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    ram(128 * 10**9)
    host = FakeParabricks(tmp_path / "gpu", parabricks.DEFAULT_IMAGE)
    monkeypatch.setenv("PATH", f"{host.bin}{os.pathsep}{os.environ['PATH']}")
    fakes = _Fakes(monkeypatch)

    def align(args):
        fakes.align(args)
        Path(args.r1).unlink()

    monkeypatch.setattr(realign.align_cmd, "run", align)
    with caplog.at_level(logging.WARNING):
        realign.run(_args(source, gpu=True))
    assert "Could not remove" in caplog.text
    assert fakes.extracted and not list(tmp_path.glob("*.fastq.gz"))
