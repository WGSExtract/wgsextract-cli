"""Issue #1230: concurrent saves of config.toml keep every change."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import tomllib

from wgsextract_cli.core import config
from wgsextract_cli.core.file_lock import shared_file_lock
from wgsextract_cli.core.utils import WGSExtractError


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "get_config_path", lambda: config_path)
    saved = dict(config.settings)
    yield config_path
    config.settings.clear()
    config.settings.update(saved)


# Stands in for the app saving config.toml at the same moment: it reads the
# file under the shared lock, then writes its change after a pause.
_APP_SAVE = """
import sys, time
from pathlib import Path
from wgsextract_cli.core.file_lock import shared_file_lock
with shared_file_lock(Path(sys.argv[1])) as target:
    text = target.read_text()
    print("holding", flush=True)
    time.sleep(float(sys.argv[2]))
    target.write_text(text + 'app_setting = "kept"\\n')
"""


def _lock_record(pid: int) -> str:
    return json.dumps({"pid": pid, "host": socket.gethostname(), "token": "test"})


def _start_app_save(path: Path, seconds: float) -> subprocess.Popen[str]:
    child = subprocess.Popen(
        [sys.executable, "-c", _APP_SAVE, str(path), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None and child.stdout.readline() == "holding\n"
    return child


@pytest.mark.parametrize("alias", [False, True])
def test_1230_concurrent_saves_keep_both_changes(temp_config, tmp_path, alias):
    # Issue #1230: the app and a command saving config.toml at once each read
    # it, and the later write silently dropped the other's change.
    temp_config.write_text('output_directory = "/out"\n')
    path = temp_config
    if alias:
        link = tmp_path / "alias"
        try:
            link.symlink_to(tmp_path, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"symlinks unavailable: {error}")
        path = link / "config.toml"
    child = _start_app_save(path, 0.5)
    config.save_config({"reference_library": "/ref"})
    assert child.wait(10) == 0
    assert tomllib.loads(temp_config.read_text()) == {
        "output_directory": "/out",
        "reference_library": "/ref",
        "app_setting": "kept",
    }


def test_1230_a_crashed_savers_lock_does_not_block_saves(temp_config):
    # Issue #1230: a save interrupted by a crash leaves its lock behind.
    dead = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    lock = temp_config.with_name(".config.toml.lock")
    lock.write_text(_lock_record(int(dead.stdout)))
    config.save_config({"reference_library": "/ref"})
    assert tomllib.loads(temp_config.read_text()) == {"reference_library": "/ref"}
    assert not lock.exists()


@pytest.mark.parametrize(
    "left",
    [
        # By this process, when removing it failed (Windows refused).
        _lock_record(os.getpid()),
        # Empty: its save crashed before writing who owned it.
        "",
    ],
    ids=["release-failed", "never-written"],
)
def test_1230_an_abandoned_lock_does_not_block_saves(temp_config, left):
    # Issue #1230: such a lock named this live process, or none, so every
    # later save timed out until it was deleted by hand.
    lock = temp_config.with_name(".config.toml.lock")
    lock.write_text(left)
    if not left:
        minute_ago = time.time() - 60
        os.utime(lock, (minute_ago, minute_ago))
    with shared_file_lock(temp_config, timeout=5) as target:
        target.write_text("saved\n")
    assert temp_config.read_text() == "saved\n"
    assert not lock.exists()


def test_1230_a_lock_windows_would_not_let_its_maker_check_does_not_block(
    temp_config, monkeypatch
):
    # Issue #1230: a sharing error (an antivirus scan, say) on reading back a
    # new lock left it held by its maker, so that save and later ones timed out.
    monkeypatch.setattr(sys, "platform", "win32")
    lock = temp_config.with_name(".config.toml.lock")
    read_bytes = Path.read_bytes
    refused: list[Path] = []

    def refuse_first_read(self: Path) -> bytes:
        if self == lock and not refused:
            refused.append(self)
            raise PermissionError(13, "sharing violation", str(self))
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", refuse_first_read)
    with shared_file_lock(temp_config, timeout=5) as target:
        target.write_text("saved\n")
    assert refused
    assert temp_config.read_text() == "saved\n"
    assert not lock.exists()


def test_1230_a_lock_still_being_written_is_not_taken(temp_config):
    temp_config.with_name(".config.toml.lock").write_text("")
    with pytest.raises(WGSExtractError, match="Timed out waiting"):
        with shared_file_lock(temp_config, timeout=0.2):
            pytest.fail("took a lock its maker is still writing")


def test_1230_a_save_that_cannot_get_the_lock_fails_loudly(temp_config):
    # Issue #1230: a save must fail rather than overwrite a concurrent one.
    temp_config.write_text('output_directory = "/out"\n')
    child = _start_app_save(temp_config, 30)
    # However long the save has taken, its owner is alive: never overtake it.
    hour_ago = time.time() - 3600
    os.utime(temp_config.with_name(".config.toml.lock"), (hour_ago, hour_ago))
    try:
        with pytest.raises(WGSExtractError, match="Timed out waiting"):
            with shared_file_lock(temp_config, timeout=0.2):
                pytest.fail("took a lock another save holds")
    finally:
        child.kill()
        child.wait()
    assert temp_config.read_text() == 'output_directory = "/out"\n'
