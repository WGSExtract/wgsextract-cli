import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from tests.htslib_helpers import needs_tools, write_fasta
from tests.test_alignment_commands import _samtools, _short_reads
from wgsextract_cli import api
from wgsextract_cli.commands.align import read_group
from wgsextract_cli.core import native_aligners, results
from wgsextract_cli.core.alignment_memory import bwa_gpu_memory, estimate_mapping_memory
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.resource_estimates import estimate_resources
from wgsextract_cli.core.staged_alignment import run_staged_alignment, workspace_path
from wgsextract_cli.core.utils import WGSExtractError

GIB = 1024**3


def _align(reads, ref, out, *, native="off", prefer=None):
    result = api.align.align(
        api.AlignParams(
            r1=Path(reads.r1),
            r2=Path(reads.r2),
            ref=Path(ref),
            outdir=out,
            threads=2,
            native_aligners=native,
            prefer_native_gpu=prefer,
        )
    )
    return out / "reads_aligned.bam", result


@pytest.mark.parametrize("fmt", ["BAM", "CRAM"])
def test_issue872_compressed_parts_preserve_duplicate_flags_and_rg_order(tmp_path, fmt):
    needs_tools("bwa", "samtools", "samblaster")
    reads = _short_reads(tmp_path)
    ref = str(
        write_fasta(
            tmp_path / "ref/duplicated.fa", {"chr1": reads.genome, "chr2": reads.genome}
        )
    )
    ordinary, _ = _align(reads, ref, tmp_path / "ordinary")
    expected = _samtools("view", ordinary)
    assert "XA:Z:" in expected
    output = str(tmp_path / ("parts.bam" if fmt == "BAM" else "parts.cram"))
    result = results.CommandResult(command="align")
    with results.collecting(result):
        workspace = run_staged_alignment(
            [
                [
                    "bwa",
                    "mem",
                    "-K",
                    "100000000",
                    "-t",
                    "2",
                    "-R",
                    read_group("reads"),
                    ref,
                    reads.r1,
                    reads.r2,
                ]
            ],
            output,
            output,
            memory="16M",
            fmt=fmt,
            ref=ref,
            chunk_records=7,
        )
    actual = (
        _samtools("view", output)
        if fmt == "BAM"
        else _samtools("view", "--reference", ref, output)
    )
    if fmt == "BAM":
        assert actual == expected
    else:
        assert [line.split("\t")[:11] for line in actual.splitlines()] == [
            line.split("\t")[:11] for line in expected.splitlines()
        ]
    assert "\tzI:" not in actual
    assert Path(output + (".bai" if fmt == "BAM" else ".crai")).is_file()
    checkpoint = json.loads((workspace / "parts.json").read_text())
    assert checkpoint["complete"]
    assert checkpoint["records"] == len(expected.splitlines())
    assert result.metrics["alignment_mode"] == "staged_metal"


def test_issue872_failed_encoding_resumes_validated_parts_without_remapping(tmp_path):
    needs_tools("bwa", "samtools", "samblaster")
    reads = _short_reads(tmp_path)
    _align(reads, reads.ref, tmp_path / "baseline")
    command = [
        [
            "bwa",
            "mem",
            "-K",
            "100000000",
            "-t",
            "2",
            "-R",
            read_group("reads"),
            reads.ref,
            reads.r1,
            reads.r2,
        ]
    ]
    output = str(tmp_path / "resumed.bam")
    with pytest.raises(WGSExtractError):
        run_staged_alignment(
            command,
            output,
            str(tmp_path),
            memory="16M",
            fmt="BAM",
            ref=reads.ref,
            chunk_records=7,
        )
    workspace = workspace_path(output)
    checkpoint = (workspace / "parts.json").read_bytes()
    assert not (workspace / "unsorted.bam").exists()
    invalid = json.loads(checkpoint)
    invalid["parts"][0]["name"] = "../outside.bam"
    (workspace / "parts.json").write_text(json.dumps(invalid))
    with pytest.raises(WGSExtractError, match="checkpoint path"):
        run_staged_alignment(
            command,
            output,
            output,
            memory="16M",
            fmt="BAM",
            ref=reads.ref,
            chunk_records=7,
        )
    (workspace / "parts.json").write_bytes(checkpoint)
    run_staged_alignment(
        command,
        output,
        output,
        memory="16M",
        fmt="BAM",
        ref=reads.ref,
        chunk_records=7,
    )
    assert (workspace / "parts.json").read_bytes() == checkpoint
    assert _samtools("view", output) == _samtools(
        "view", tmp_path / "baseline/reads_aligned.bam"
    )


def test_issue872_real_metal_physical_gate_and_speed_preference(tmp_path, monkeypatch):
    needs_tools("bwa", "samtools", "samblaster")
    native = native_aligners.find_native_aligner("bwa", "auto", quiet=True)
    if native is None or not native.metal_available or native.tool != "bwa":
        pytest.skip("Genuine wgse-bwa Metal hardware/build required")
    reads = _short_reads(tmp_path, comment=100_000)
    ordinary, _ = _align(reads, reads.ref, tmp_path / "ordinary")
    monkeypatch.setitem(settings, "memory_limit", None)
    gpu = bwa_gpu_memory(reads.ref, 280)
    needed = estimate_mapping_memory(
        reads.ref,
        "bwa",
        threads="2",
        memory="2G",
        samblaster=True,
        reads=280,
        read_length=100,
        gpu_bytes=gpu,
        staged=True,
    ).total
    real_memory = psutil.virtual_memory()

    def memory(budget):
        monkeypatch.setattr(
            psutil,
            "virtual_memory",
            lambda: real_memory._replace(
                total=budget + 2 * GIB, available=budget + 2 * GIB
            ),
        )

    memory(needed + GIB // 32)
    staged_path, staged = _align(reads, reads.ref, tmp_path / "staged", native="auto")
    assert (
        staged.metrics["native_aligner_backend_reason"] == "memory_budget_metal_staged"
    )
    assert _samtools("view", staged_path) == _samtools("view", ordinary)
    realigned = api.align.realign(
        api.RealignParams(
            input=ordinary,
            r1=Path(reads.r1),
            r2=Path(reads.r2),
            ref=Path(reads.ref),
            outdir=tmp_path / "realigned",
            threads=2,
        )
    )
    assert realigned.metrics["alignment_mode"] == "staged_metal"
    assert Path(reads.r1).is_file() and Path(reads.r2).is_file()
    memory(needed - GIB // 32)
    _, cpu = _align(reads, reads.ref, tmp_path / "cpu", native="auto")
    assert cpu.metrics["native_aligner_backend_reason"] == "memory_budget_cpu"
    _, preferred = _align(
        reads, reads.ref, tmp_path / "preferred", native="auto", prefer=True
    )
    assert preferred.metrics["native_aligner_backend_reason"] == "prefer_gpu"
    assert "alignment_mode" not in preferred.metrics


def test_issue872_staged_disk_estimate_does_not_double_count_phase_storage(tmp_path):
    needs_tools("bwa", "samtools", "samblaster")
    reads = _short_reads(tmp_path)
    _align(reads, reads.ref, tmp_path / "baseline")
    estimate = estimate_resources(
        "realign",
        input_paths=[reads.r1, reads.r2],
        output_dir=str(tmp_path),
        reference=reads.ref,
        reads=835260415,
        read_length=151,
        threads="3",
        memory="2G",
        gpu_bytes=GIB,
        staged=True,
        extract_fastq=True,
    )
    disks = [row for row in estimate.resources if row.resource == "disk"]
    assert len(disks) == 1
    assert disks[0].estimated_bytes is not None
    # contract: the measured 35x staged envelope fits about 200 GiB, not 430 GiB.
    assert disks[0].estimated_bytes < 200 * GIB
    assert "temporary" not in disks[0].components


def test_issue872_owned_fastqs_retire_only_after_validated_alignment(tmp_path):
    needs_tools("bwa", "samtools", "samblaster")
    reads = _short_reads(tmp_path)
    baseline, _ = _align(reads, reads.ref, tmp_path / "baseline")
    expected = _samtools("view", baseline)
    owned = (reads.r1, reads.r2)
    with pytest.raises(WGSExtractError):
        run_staged_alignment(
            [["bwa", "mem", str(tmp_path / "missing-reference"), *owned]],
            str(tmp_path / "failed.bam"),
            str(tmp_path / "failed.bam"),
            memory="16M",
            fmt="BAM",
            ref=reads.ref,
            retire_fastqs=owned,
        )
    assert all(Path(path).is_file() for path in owned)
    output = str(tmp_path / "owned.bam")
    run_staged_alignment(
        [
            [
                "bwa",
                "mem",
                "-K",
                "100000000",
                "-t",
                "2",
                "-R",
                read_group("reads"),
                reads.ref,
                *owned,
            ]
        ],
        output,
        output,
        memory="16M",
        fmt="BAM",
        ref=reads.ref,
        retire_fastqs=owned,
        chunk_records=7,
    )
    assert not any(Path(path).exists() for path in owned)
    assert _samtools("view", output) == expected


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cancellation")
def test_issue872_cancelled_staged_worker_reaps_its_sort_child(tmp_path):
    needs_tools("samtools")
    samtools = get_tool_path("samtools")
    assert samtools is not None
    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "wgsextract_cli.core.staged_alignment",
            "mark",
            "--workspace",
            str(tmp_path),
            "--samtools",
            samtools,
            "--memory",
            "16M",
            "--chunk-records",
            "1000",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    child_pid = None
    try:
        assert worker.stdin is not None
        worker.stdin.write(
            b"@HD\tVN:1.6\n@SQ\tSN:chr1\tLN:1000\n@RG\tID:r\tSM:r\n"
            b"read\t4\t*\t0\t0\t*\t*\t0\t0\tA\tF\tRG:Z:r\n"
        )
        worker.stdin.flush()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            children = psutil.Process(worker.pid).children()
            if children:
                child_pid = children[0].pid
                break
            if worker.poll() is not None:
                break
            time.sleep(0.01)
        assert child_pid is not None
        os.killpg(worker.pid, signal.SIGTERM)
        worker.wait(timeout=10)
        assert not psutil.pid_exists(child_pid)
    finally:
        if worker.poll() is None:
            os.killpg(worker.pid, signal.SIGKILL)
            worker.wait()
        if child_pid and psutil.pid_exists(child_pid):
            psutil.Process(child_pid).kill()
