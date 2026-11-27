"""The CLI's output is UTF-8 even when the platform's default isn't."""

import os
import subprocess
import sys
from pathlib import Path


def test_1506_version_ignores_a_closed_output_pipe(tmp_path: Path) -> None:
    config = tmp_path / "empty.toml"
    config.touch()
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        run = subprocess.run(
            [
                sys.executable,
                "-u",
                "-m",
                "wgsextract_cli",
                "--config",
                str(config),
                "--version",
            ],
            stdout=write_fd,
            stderr=subprocess.PIPE,
            timeout=8,
        )
    finally:
        os.close(write_fd)
    assert run.returncode == 0
    assert run.stderr == b""


def test_piped_output_becomes_utf8() -> None:
    # Piped stdout on Windows uses the ANSI code page, which has no emoji.
    script = r"""
from wgsextract_cli.cli.stdio import configure_stdio_encoding

configure_stdio_encoding()
print("\u2139\ufe0f: done")
"""
    run = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, PYTHONIOENCODING="cp1252"),
        capture_output=True,
        check=True,
    )
    assert run.stdout == f"ℹ️: done{os.linesep}".encode()
