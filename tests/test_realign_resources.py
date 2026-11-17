"""Focused realign resources regressions."""

import gzip
import logging
import platform
import random
import re
from pathlib import Path

import pytest

from tests.disk_volumes import GIB, Volumes
from tests.htslib_helpers import needs_tools, sam_to_bam
from tests.prepared_data import sparse_truncate
from tests.test_realign import (
    _DISK as _DISK,
)
from tests.test_realign import (
    _args as _args,
)
from tests.test_realign import (
    _disk_needs as _disk_needs,
)
from tests.test_realign import (
    _Fakes as _Fakes,
)
from tests.test_realign import (
    _old_alignment as _old_alignment,
)
from tests.test_realign import (
    _preflight_gib as _preflight_gib,
)
from tests.test_realign import (
    _view as _view,
)
from tests.test_realign import (
    _write_alignment as _write_alignment,
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
from wgsextract_cli.api.align import realign as realign_api
from wgsextract_cli.commands import realign
from wgsextract_cli.commands.alignment import short_reads
from wgsextract_cli.commands.realignment import mapping as realign_mapping
from wgsextract_cli.commands.realignment import preparation as realign_preparation
from wgsextract_cli.commands.realignment import resources as realign_resources
from wgsextract_cli.commands.realignment import steps as realign_steps
from wgsextract_cli.core.alignment.memory import MappingMemory
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.utils import WGSExtractError, run_command


@needs_tools("bwa", "samtools")
def test_968_realign_auto_plan_survives_extraction_and_matches_explicit_threads(
    tmp_path, genome_and_ref, monkeypatch
):
    from tests.test_alignment_commands import _m1_core_levels

    _m1_core_levels(monkeypatch)
    monkeypatch.setitem(settings, "cpu_threads", "auto")
    genome, ref = genome_and_ref
    source, _ = _old_alignment(tmp_path, genome)
    records = []
    for threads, alignment, sort in ((None, 8, 4), (2, 2, 2)):
        result = realign_api(
            _args(
                source,
                threads=threads,
                ref=ref,
                outdir=tmp_path / str(threads),
                aligner="bwa",
                native_aligners="off",
            )
        )
        plan = result.provenance.params["thread_plan"]
        assert plan["alignment"] == alignment
        assert plan["sort"] == sort
        (output,) = (tmp_path / str(threads)).glob("*_aligned.bam")
        records.append(_view("view", str(output)))
    assert records[0] == records[1]


@needs_tools("bwa", "samtools", "samblaster")
@pytest.mark.parametrize("keep_fastq", [False, True], ids=["stream", "files"])
def test_997_codec_workers_do_not_expand_realign_collation_budget(
    tmp_path, genome_and_ref, monkeypatch, keep_fastq
):
    from tests.test_alignment_commands import _m1_core_levels

    _m1_core_levels(monkeypatch)
    monkeypatch.setitem(settings, "cpu_threads", "auto")
    genome, ref = genome_and_ref
    source, _ = _old_alignment(tmp_path, genome)
    lines = _view("view", "-H", source).splitlines()
    rng = random.Random(997)
    # Budget-sensitive buckets and coincident fragments expose reordered duplicates.
    padding = "".join(
        rng.choices(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=200_000
        )
    )
    for row in _view("view", source).splitlines():
        fields = row.split("\t")
        first = int(fields[1]) & 64
        fields[3], fields[7], fields[8] = (
            ("101", "301", "300") if first else ("301", "101", "-300")
        )
        fields[9] = genome[100:200] if first else genome[300:400]
        lines.append("\t".join([*fields, f"ZZ:Z:{padding}"]))
    source = sam_to_bam(lines, tmp_path / "old" / "padded.bam")
    run_command(["bwa", "index", ref])
    outputs, requirements, fastqs = [], [], []
    # Equal 8M sort budgets: auto is 4 x 2M, explicit is 8 x 1M.
    for name, threads, memory in (("auto", None, "2M"), ("explicit", 8, "1M")):
        out = tmp_path / name
        result = realign_api(
            _args(
                source,
                ref=ref,
                outdir=out,
                threads=threads,
                memory=memory,
                aligner="bwa",
                native_aligners="off",
                keep_fastq=keep_fastq,
            )
        )
        outputs.append(_view("view", str(out / "padded_aligned.bam")))
        if not keep_fastq:
            requirements.append(result.details["host_requirement"]["memory_bytes"])
        else:
            fastqs.append(
                tuple(
                    gzip.decompress(path.read_bytes())
                    for path in sorted(out.glob("*.fastq.gz"))
                )
            )
    assert outputs[0] == outputs[1]
    if keep_fastq:
        assert fastqs[0] and fastqs[0] == fastqs[1]
    else:
        assert requirements[0] == requirements[1]


@pytest.mark.parametrize(("extract", "needed"), [(True, 10), (False, 5)])
def test_preflight_needs_room_for_the_extraction_only_when_extracting(
    tmp_path, monkeypatch, source, extract, needed
):
    fakes = _Fakes(monkeypatch)
    estimates = {"Name": (10, 4), "Coord": (3, 2)}  # (temp GB, final GB)
    monkeypatch.setattr(
        realign_resources,
        "get_free_space_needed",
        lambda _size, sort_type, **_k: estimates[sort_type],
    )
    Volumes(monkeypatch).free[tmp_path] = 0
    reads = tmp_path / "reads.fq"
    reads.write_text("")
    args = _args(
        source,
        no_preflight=False,
        r1=None if extract else str(reads),
        keep_fastq=extract,
    )

    # Extraction's temp space is freed before alignment; its FASTQs are not:
    # max(10, 4 + 3 + 2) when extracting, 3 + 2 when not.
    with pytest.raises(
        WGSExtractError,
        match=f"~{needed} GiB disk on {re.escape(str(tmp_path))},.*--no-preflight",
    ):
        realign.run(args)
    assert fakes.extracted == [] and fakes.aligned == []


def test_stream_preflight_drops_only_the_fastqs_between_collate_and_sort(
    tmp_path,
    monkeypatch,
    source,
):
    estimates = {"Name": (10, 8), "Coord": (3, 2)}  # (temp GB, final GB)
    monkeypatch.setattr(
        realign_resources,
        "get_free_space_needed",
        lambda _size, sort_type, **_k: estimates[sort_type],
    )
    asked = [
        realign._disk_requirements(
            str(source), str(tmp_path), not streaming, 100, streaming=streaming
        )
        for streaming in (False, True)
    ]
    # Files: max(10, 8 + 3 + 2). Streaming: collate's spills drain as the
    # sorter's grow, so max(10, 3 + 2).
    assert asked == [[(str(tmp_path), 13 * GIB)], [(str(tmp_path), 10 * GIB)]]


@pytest.mark.parametrize("requested", ["bwa", "bwa-mem2", "auto"])
def test_stream_overlap_without_ram_uses_files_without_changing_aligner(
    tmp_path, monkeypatch, source, requested
):
    fakes = _Fakes(monkeypatch)
    ref = tmp_path / "ref.fa"
    ref.write_text(">c\nA\n")
    monkeypatch.setattr(realign.bwa_mem2, "check_index", lambda *_a: None)
    monkeypatch.setattr(realign.bwa_mem2, "has_index", lambda *_a: True)
    monkeypatch.setattr(
        realign.bwa_mem2,
        "can_map",
        lambda *_a, **kw: kw.get("additional_ram_bytes", 0) == 0,
    )
    monkeypatch.setattr(short_reads, "get_tool_path", lambda *_a: "tool")
    monkeypatch.setattr(short_reads, "available_memory", lambda: 8 * 1024**3)
    monkeypatch.setattr(
        short_reads,
        "estimate_mapping_memory",
        lambda *_a, **_kw: MappingMemory(8 * 1024**3 - 32 * 1024**2, 0, 0, 0, 0),
    )
    checked = []
    monkeypatch.setattr(
        realign_steps,
        "check_mapping_memory",
        lambda *_a, **kw: checked.append(kw["additional_ram_bytes"]),
    )
    chosen = []

    def align_files(args):
        fakes.align(args)
        chosen.append(
            realign.align_cmd.short_read_aligner(
                args.aligner, str(ref), reads=args.read_count
            )
        )

    monkeypatch.setattr(realign.align_cmd, "run", align_files)
    realign.run(_args(source, ref=str(ref), aligner=requested))
    assert len(fakes.extracted) == 1 and not fakes.streamed
    assert chosen == ["bwa" if requested == "bwa" else "bwa-mem2"]
    assert all(extra == 0 for extra in checked)


def test_stream_reserves_the_estimated_collate_bucket_for_reporting_and_mapping(
    tmp_path, monkeypatch, source, caplog
):
    fakes = _Fakes(monkeypatch)
    monkeypatch.setattr(realign_mapping, "indexed_read_count", lambda *_a: 1_000_000)
    monkeypatch.setattr(realign_steps, "indexed_read_count", lambda *_a: 1_000_000)
    estimates, disk = [], []
    for ram in (1024**4, 1):  # the bucket fits beside alignment, then doesn't
        monkeypatch.setattr(realign_steps, "available_memory", lambda ram=ram: ram)
        caplog.clear()
        with caplog.at_level(logging.INFO):
            realign.run(_args(source, aligner="bwa"))
        estimates.append(_preflight_gib(caplog, "memory"))
        disk.append(_preflight_gib(caplog, "disk"))
        (tmp_path / "x_aligned.bam").unlink()
    expected = realign.collate_working_memory(
        source, "2", "100M", records=1_000_000, max_record_bytes=400
    )
    assert len(fakes.streamed) == 1 and len(fakes.extracted) == 1
    assert fakes.streamed[0]["additional_ram_bytes"] == expected
    assert estimates[0] - estimates[1] == pytest.approx(expected / 1024**3, abs=0.01)
    assert disk[0] < disk[1]  # a stream writes no FASTQ files


@pytest.mark.parametrize("gib", [64, 2])
def test_whole_genome_input_streams_when_its_collate_bucket_fits_ram(
    tmp_path, monkeypatch, source, gib
):
    fakes = _Fakes(monkeypatch)
    sparse_truncate(source, 40 * 1024**3)
    monkeypatch.setattr(realign_mapping, "indexed_read_count", lambda *_a: 800_000_000)
    monkeypatch.setattr(realign_steps, "indexed_read_count", lambda *_a: 800_000_000)
    monkeypatch.setattr(realign, "calculate_bam_md5", lambda *_a: None)
    monkeypatch.setattr(realign_preparation, "calculate_bam_md5", lambda *_a: None)
    monkeypatch.setattr(realign, "resolve_reference", lambda *_a: None)
    monkeypatch.setattr(realign_preparation, "resolve_reference", lambda *_a: None)
    monkeypatch.setattr(short_reads, "available_memory", lambda: gib * 1024**3)
    monkeypatch.setattr(realign_steps, "available_memory", lambda: gib * 1024**3)
    realign.run(_args(source, aligner="bwa", threads=8, memory="2G"))
    if gib == 64:
        assert fakes.streamed and not fakes.extracted
    else:
        assert len(fakes.extracted) == 1 and not fakes.streamed
    assert len(fakes.aligned) == 1


def test_ample_disk_space_is_not_reported_as_a_warning(
    tmp_path, monkeypatch, source, roomy_disk, ram, caplog
):
    """Issue #549: every realign warned "~N GB free space required" though
    the disk had room; only a shortfall is worth a warning."""
    ram(128 * GIB)
    _Fakes(monkeypatch)
    monkeypatch.setattr(realign_mapping, "indexed_read_count", lambda *_a: 800_000_000)
    monkeypatch.setattr(realign_steps, "indexed_read_count", lambda *_a: 800_000_000)
    with caplog.at_level(logging.INFO):
        realign.run(_args(source, aligner="bwa", no_preflight=False))
    disk = [r for r in caplog.records if _DISK.search(r.getMessage())]
    assert disk and all(r.levelno < logging.WARNING for r in disk)


def test_late_stream_memory_change_retries_file_extraction(
    tmp_path, monkeypatch, source, roomy_disk, caplog
):
    caplog.set_level(logging.INFO)
    fakes = _Fakes(monkeypatch)
    monkeypatch.setattr(realign_mapping, "indexed_read_count", lambda *_a: 800_000_000)
    monkeypatch.setattr(realign_steps, "indexed_read_count", lambda *_a: 800_000_000)

    def unavailable(*_a, **_kw):
        raise realign.align_cmd.StreamingMemoryUnavailable("RAM changed")

    monkeypatch.setattr(realign.align_cmd, "align_bwa", unavailable)
    realign.run(_args(source, aligner="bwa", no_preflight=False))
    # Rechecked for FASTQ files kept until alignment, which streaming has none of.
    streamed, extracted = _disk_needs(caplog)
    assert extracted > streamed
    assert len(fakes.extracted) == 1 and len(fakes.aligned) == 1
    assert not list(tmp_path.glob(".collate_*"))


@pytest.mark.parametrize("provided_fastqs", [False, True])
def test_realign_uses_actual_input_count_before_extraction_for_memory_and_disk(
    tmp_path, monkeypatch, source, provided_fastqs, roomy_disk, ram, caplog
):
    caplog.set_level(logging.INFO)
    ram(128 * GIB)
    _Fakes(monkeypatch)
    ref = tmp_path / "ref.fa"
    ref.write_text(">c\nA\n")
    Path(str(ref) + ".fai").write_text("c\t1\t3\t1\t2\n")
    monkeypatch.setattr(realign.bwa_mem2, "check_index", lambda *_: None)

    def indexed_count(*_args):
        assert not provided_fastqs
        return 800_000_000

    monkeypatch.setattr(realign_mapping, "indexed_read_count", indexed_count)
    monkeypatch.setattr(realign_steps, "indexed_read_count", indexed_count)
    budgets = []
    monkeypatch.setattr(
        realign_steps,
        "check_mapping_memory",
        lambda *_a, **kwargs: budgets.append(("memory", kwargs["reads"])),
    )

    def align(args, **_kwargs):
        budgets.append(("align", args.read_count))
        _write_alignment(args)

    monkeypatch.setattr(realign.align_cmd, "run", align)
    monkeypatch.setattr(realign.align_cmd, "align_bwa", align)
    overrides = {}
    if provided_fastqs:
        for mate in ("r1", "r2"):
            fastq = tmp_path / f"{mate}.fq"
            fastq.write_text("@r\nA\n+\nI\n")
            overrides[mate] = str(fastq)
    realign.run(
        _args(source, ref=str(ref), aligner="bwa-mem2", no_preflight=False, **overrides)
    )
    expected = 2 if provided_fastqs else 800_000_000
    assert budgets == [("memory", expected), ("align", expected)]
    # 800 million reads need room on disk; two reads in a 4-byte input don't.
    (needed,) = _disk_needs(caplog)
    assert (needed > 0) == (not provided_fastqs)


@pytest.mark.parametrize("no_preflight", [False, True])
@pytest.mark.parametrize("reads", [800_000_000, None])
def test_ultima_realign_counts_index_once_and_passes_through_unknown_counts(
    tmp_path, monkeypatch, source, no_preflight, reads, roomy_disk, caplog
):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    calls = []

    def count(*_args):
        calls.append("idxstats")
        return reads

    monkeypatch.setattr(realign_mapping, "indexed_read_count", count)
    monkeypatch.setattr(realign_steps, "indexed_read_count", count)

    def align_ultima(args, *_a):
        calls.append(("align", args.read_count))
        Path(tmp_path, "x_aligned.bam").write_text("aligned")
        Path(tmp_path, "x_aligned.bam.bai").write_text("index")

    monkeypatch.setattr(realign.align_cmd, "align_ultima", align_ultima)
    realign.run(
        _args(source, platform="ultima", aligner="bwa", no_preflight=no_preflight)
    )
    assert calls == ["idxstats", ("align", reads)]
    # The count sizes the disk check; unknown, the 4-byte input's size does.
    # --no-preflight still prints the requirement.
    (needed,) = _disk_needs(caplog)
    assert (needed > 0) == (reads is not None)
