"""Regression tests for invalidation and cancellation of chunked realignment (#1208)."""

import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools
from tests.test_chunked_realign import GROUP_SORT, MAPPING, _view
from tests.test_chunked_realign import ont as ont
from wgsextract_cli.api import CancelToken
from wgsextract_cli.core import execution_context
from wgsextract_cli.core.chunked_alignment import ChunkedAlignment, Region
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.tool_commands import _tool_command_parts
from wgsextract_cli.core.utils import WGSExtractError, run_command


@pytest.fixture
def chunked_run(tmp_path, ont):
    return ChunkedAlignment(
        input=ont["cram"],
        output=str(tmp_path / "realigned.bam"),
        chunks=[[Region("chr1", 1, 100_000)]],
        reference=ont["ref"],
        format="BAM",
        threads="1",
        memory="256M",
        mapper=["minimap2", "-ax", "map-ont", "-t", "1", "-y"],
        indexer=["minimap2", "-x", "map-ont", "-t", "1"],
        reads=["samtools", "fastq", "-n", "-T", "MM,ML", "-"],
        decode=["-T", ont["ref"]],
        resume=True,
    )


@needs_tools("samtools", "minimap2")
@pytest.mark.parametrize("setting", ["decode", "reads"])
def test_1208_resume_rebuilds_chunks_after_extraction_options_change(
    tmp_path, chunked_run, setting, caplog
):
    with caplog.at_level("INFO"):
        chunked_run.run()
        caplog.clear()
        chunked_run.run()
        assert "Resuming the chunked alignment" in caplog.text

        if setting == "decode":
            decoder_reference = shutil.copyfile(
                chunked_run.reference, tmp_path / "decode.fa"
            )
            changed = replace(chunked_run, decode=["-T", str(decoder_reference)])
        else:
            changed = replace(chunked_run, reads=["samtools", "fastq", "-n", "-"])
        caplog.clear()
        changed.run()

    assert "Resuming the chunked alignment" not in caplog.text
    assert "Aligning chunk" in caplog.text
    records = _view("view", changed.output)
    assert records
    assert ("ML:B:" in records) == (setting == "decode")


@needs_tools("samtools", "minimap2")
def test_1208_resume_detects_a_changed_version_behind_a_mapper_wrapper(
    tmp_path, chunked_run, caplog
):
    minimap2 = get_tool_path("minimap2")
    assert minimap2 is not None
    version = tmp_path / "version"
    version.write_text(
        run_command(["minimap2", "--version"], capture_output=True).stdout
    )
    wrapper = tmp_path / "mapper wrapper.py"
    wrapper.write_text(
        "import subprocess, sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print(Path(__file__).with_name('version').read_text().strip())\n"
        "else:\n"
        f"    sys.exit(subprocess.call({_tool_command_parts(minimap2)!r} + sys.argv[1:]))\n"
    )
    quote = subprocess.list2cmdline if os.name == "nt" else shlex.join
    wrapped = replace(
        chunked_run,
        mapper=[quote([sys.executable, str(wrapper)]), *chunked_run.mapper[1:]],
    )
    with caplog.at_level("INFO"):
        wrapped.run()
        version.write_text("3.0-r1\n")
        caplog.clear()
        wrapped.run()
    assert "Resuming the chunked alignment" not in caplog.text
    assert "Aligning chunk" in caplog.text
    _view("quickcheck", wrapped.output)


@pytest.mark.skipif(platform.system() == "Windows", reason="bash PATH shims")
@needs_tools("samtools", "minimap2")
@pytest.mark.parametrize(
    ("tool", "active_stage", "description"),
    [
        ("minimap2", MAPPING, "Alignment of chunk"),
        ("samtools", GROUP_SORT, "Merge of output group"),
    ],
    ids=["align", "assemble"],
)
def test_1208_cancel_token_interrupts_active_chunk_pipeline(
    tmp_path, chunked_run, monkeypatch, tool, active_stage, description
):
    real_tool = get_tool_path(tool)
    assert real_tool is not None
    started = tmp_path / "stage-started"
    finished = tmp_path / "stage-finished"
    slow_stage = (
        "from pathlib import Path; import time; "
        f"Path({str(started)!r}).touch(); time.sleep(5); "
        f"Path({str(finished)!r}).touch()"
    )
    shim = tmp_path / tool
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"stage={shlex.quote(active_stage)}\n"
        f'if [[ "{tool} $*" =~ $stage ]]; then\n'
        f"  exec {shlex.join([sys.executable, '-c', slow_stage])}\n"
        "fi\n"
        f'exec {shlex.join(_tool_command_parts(real_tool))} "$@"\n'
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    token = CancelToken()

    def cancel_active_pipeline() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if started.exists():
                token.cancel()
                return
            time.sleep(0.01)

    thread = execution_context.context_thread(cancel_active_pipeline, daemon=True)
    thread.start()
    try:
        with (
            execution_context.bound(cancel=token),
            pytest.raises(WGSExtractError, match=rf"{description} \d+.* cancelled"),
        ):
            chunked_run.run()
    finally:
        thread.join()
    assert not finished.exists()
    assert not Path(chunked_run.output).exists()
