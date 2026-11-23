"""Test launchers must match the real host, not the platform being simulated."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fake_tool_launchers import (
    expose_tool_fakes,
    python_tool,
    require_bare_name_fakes,
)


@pytest.mark.skipif(os.name != "nt", reason="requires a native Windows host")
def test_1406_linux_simulation_still_skips_bare_name_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(pytest.skip.Exception):
        require_bare_name_fakes()


def test_1406_fake_tool_is_runnable_after_platform_simulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as platform_patch:
        platform_patch.setattr(sys, "platform", "linux" if os.name == "nt" else "win32")
        python_tool(tmp_path, "answer", "print(42)")
    monkeypatch.setenv("PATHEXT", ".EXE")
    expose_tool_fakes(monkeypatch, tmp_path, "answer")
    launcher = shutil.which("answer")
    assert launcher is not None
    assert subprocess.check_output([launcher], text=True).strip() == "42"
