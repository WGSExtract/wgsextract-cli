import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from wgsextract_cli.api._runtime import ProgressEvent, _context_of
from wgsextract_cli.core import utils
from wgsextract_cli.core.progress import Heartbeat
from wgsextract_cli.core.results import CommandResult, collecting


def _wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll until condition() is true; the heartbeat thread's timing is not guaranteed."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)


def test_pipeline_heartbeat_preserves_binary_data_and_counts_stderr(
    tmp_path, caplog, monkeypatch
):
    original = utils.Heartbeat
    monkeypatch.setattr(
        utils, "Heartbeat", lambda *a, **k: original(*a, interval=0.01, **k)
    )
    stages = [
        [
            sys.executable,
            "-c",
            "import sys,time;sys.stderr.write('Processed 2 reads in 1s\\n');"
            "sys.stderr.flush();sys.stdout.buffer.write(b'\\x00data\\xff');time.sleep(.06)",
            "bwa",
        ]
    ]
    caplog.set_level(logging.INFO)
    for enabled in (False, True):
        with (tmp_path / str(enabled)).open("wb") as stream:
            utils.run_pipeline(
                stages, stdout=stream, heartbeat=enabled, expected_reads=4
            )
    assert (
        (tmp_path / "False").read_bytes()
        == (tmp_path / "True").read_bytes()
        == b"\x00data\xff"
    )
    assert "2/4 reads (50.0%)" in caplog.text
    assert "estimated" in caplog.text


def test_heartbeat_reports_while_no_shards_complete_and_stops(caplog):
    caplog.set_level(logging.INFO)
    with Heartbeat("GATK scatter", 2, "shards", interval=0.01) as progress:
        # The entry report plus at least one report from the background thread.
        _wait_until(lambda: caplog.text.count("0/2 shards") >= 2)
        progress.advance()
        progress.report()
    assert caplog.text.count("0/2 shards") >= 2
    assert "1/2 shards" in caplog.text
    assert not progress._thread.is_alive()


def test_progress_scan_error_cannot_fail_the_data_pipeline(caplog):
    def unavailable():
        raise OSError("monitor unavailable")

    caplog.set_level(logging.INFO)
    with Heartbeat("Extraction", 10, scan=unavailable, interval=0.01):
        # Exit also scans, so wait for the background thread to hit the error first.
        _wait_until(lambda: "Progress scan unavailable" in caplog.text)
        background_scan_failed = "Progress scan unavailable" in caplog.text
    assert background_scan_failed


@pytest.mark.parametrize("with_aligner", [False, True])
def test_pipeline_counts_aligner_only_or_fastq_as_fallback(
    tmp_path, caplog, with_aligner
):
    caplog.set_level(logging.INFO)
    stages = [
        [
            sys.executable,
            "-c",
            "import sys;sys.stderr.write('[M::bam2fq_mainloop] processed 2 reads\\n');"
            "sys.stdout.buffer.write(b'paired-data')",
            "samtools",
            "fastq",
        ]
    ]
    if with_aligner:
        stages.append(
            [
                sys.executable,
                "-c",
                "import sys;sys.stderr.write('Processed 2 reads in 1s\\n');"
                "sys.stdout.buffer.write(sys.stdin.buffer.read())",
                "bwa",
            ]
        )
    with (tmp_path / "data").open("wb") as output:
        utils.run_pipeline(stages, stdout=output, heartbeat=True, expected_reads=2)
    assert (tmp_path / "data").read_bytes() == b"paired-data"
    assert "2/2 reads (100.0%)" in caplog.text
    assert "4/2 reads" not in caplog.text


def test_heartbeat_sends_its_count_and_fraction_to_api_callers():
    events: list[ProgressEvent] = []
    with _context_of(events.append, None):
        with Heartbeat("Alignment", 4, interval=60) as heartbeat:
            heartbeat.advance(3)
    assert events == [
        ProgressEvent("heartbeat", "0/4 reads", 0.0),
        ProgressEvent("heartbeat", "3/4 reads", 0.75),
    ]


@pytest.mark.parametrize(
    ("work", "state"),
    [
        ("t=time.time()\nwhile time.time()-t<1.5: pass", "busy"),
        ("time.sleep(1.5)", "stalled"),
    ],
)
def test_quiet_aligner_heartbeat_tells_slow_batch_from_stall(
    tmp_path, caplog, monkeypatch, work, state
):
    """#431: minimap2 spent hours on a batch of chr4 satellite ONT reads while
    the heartbeat repeated the same read count, which looked like a hang."""
    original = utils.Heartbeat
    monkeypatch.setattr(
        utils,
        "Heartbeat",
        lambda *a, **k: original(*a, interval=0.1, stall_after=0.5, **k),
    )
    stage = [
        sys.executable,
        "-c",
        "import sys,time\nsys.stderr.write('[M::worker_pipeline] mapped 2 sequences\\n')\n"
        f"sys.stderr.flush()\n{work}",
        "minimap2",
    ]
    caplog.set_level(logging.INFO)
    with collecting(result := CommandResult(command="align")):
        with (tmp_path / "out").open("wb") as output:
            utils.run_pipeline([stage], stdout=output, heartbeat=True)
    assert f"{Path(sys.executable).stem} {state}: no progress for" in caplog.text
    stalls = [w.code for w in result.warnings if w.code == "aligner_stalled"]
    assert stalls == (["aligner_stalled"] if state == "stalled" else [])


def test_aligner_waiting_on_busy_upstream_stage_is_not_stalled(tmp_path, monkeypatch):
    """PR #576 review: samtools collate reads a whole 30x input before bwa gets
    a read; bwa idling on its empty stdin meanwhile is no stall."""
    original = utils.Heartbeat
    monkeypatch.setattr(
        utils,
        "Heartbeat",
        lambda *a, **k: original(*a, interval=0.1, stall_after=0.5, **k),
    )
    collate = [
        sys.executable,
        "-c",
        "import time\nt=time.time()\nwhile time.time()-t<1.5: pass\nprint('r')",
    ]
    aligner = [
        sys.executable,
        "-c",
        "import sys\nsys.stdin.read()\n"
        "sys.stderr.write('[M::process] Processed 1 reads\\n')",
        "bwa",
    ]
    with collecting(result := CommandResult(command="realign")):
        with (tmp_path / "out").open("wb") as output:
            utils.run_pipeline([collate, aligner], stdout=output, heartbeat=True)
    assert [w.code for w in result.warnings] == []
