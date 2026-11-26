"""The CLI's output is UTF-8 even when the platform's default isn't."""

import os
import subprocess
import sys


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
