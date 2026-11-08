import logging
import subprocess
import sys
import time
from collections.abc import Callable

import pytest

from wgsextract_cli.api._runtime import ProgressEvent, _context_of
from wgsextract_cli.core import utils
from wgsextract_cli.core.progress import Heartbeat


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


def test_extraction_heartbeat_moves_before_samtools_reports_its_count(
    tmp_path, caplog, monkeypatch
):
    # Issue #549: a 35x FASTQ extraction showed "0/835M reads (0.0%)" for 23
    # minutes, because samtools fastq prints its count only when it finishes.
    original = utils.Heartbeat
    monkeypatch.setattr(
        utils, "Heartbeat", lambda *a, **k: original(*a, interval=0.01, **k)
    )
    state = tmp_path / "state"
    stage = [
        sys.executable,
        "-c",
        "import pathlib,sys,time;state=pathlib.Path(sys.argv[1])\n"
        "for shares in ('0.5 0','1 0','1 0.5'):\n"
        "    state.write_text(shares);time.sleep(.2)\n"
        "sys.stderr.write('[M::bam2fq_mainloop] processed 4 reads\\n')",
        str(state),
        "samtools",
        "fastq",
    ]

    def estimate() -> tuple[float, float]:
        read, written = (state.read_text() or "0 0").split()
        return float(read), float(written)

    state.write_text("0 0")
    caplog.set_level(logging.INFO)
    utils.run_pipeline(
        [stage], stdout=subprocess.DEVNULL, heartbeat=True, expected_reads=4,
        estimate=estimate,
    )  # fmt: skip
    assert "50% of the input read" in caplog.text
    assert "100% of the input read, collating pairs" in caplog.text
    assert "~2/4 reads written (50.0%)" in caplog.text
    assert "4/4 reads (100.0%)" in caplog.text


def test_write_time_left_comes_from_progress_since_writing_was_seen(
    caplog, monkeypatch
):
    # #564 review: a first sample with output already written claimed "about
    # 0s left to write" while half the output was still to come.
    clock = iter([0.0, 60.0, 60.0, 120.0, 120.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    caplog.set_level(logging.INFO)
    heartbeat = Heartbeat("Extraction", 100)
    heartbeat.estimate(1, 0.4)
    heartbeat.report()
    assert "(40.0%), elapsed" in caplog.text
    assert "left to write" not in caplog.text
    heartbeat.estimate(1, 0.7)
    heartbeat.report()
    assert "(70.0%), about 60s left to write" in caplog.text


def test_failing_estimate_keeps_the_tools_own_count(tmp_path, caplog, monkeypatch):
    # #564 review: an estimator error disabled the whole scan, so samtools's
    # final count was never read.
    original = utils.Heartbeat
    monkeypatch.setattr(
        utils, "Heartbeat", lambda *a, **k: original(*a, interval=0.01, **k)
    )
    stage = [
        sys.executable,
        "-c",
        "import sys,time;time.sleep(.1)\n"
        "sys.stderr.write('[M::bam2fq_mainloop] processed 4 reads\\n')",
        "samtools",
        "fastq",
    ]

    def estimate() -> tuple[float, float]:
        raise OSError("bucket vanished")

    caplog.set_level(logging.INFO)
    utils.run_pipeline(
        [stage], stdout=subprocess.DEVNULL, heartbeat=True, expected_reads=4,
        estimate=estimate,
    )  # fmt: skip
    assert "4/4 reads (100.0%)" in caplog.text
    assert "Progress scan unavailable" not in caplog.text
