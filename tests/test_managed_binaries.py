"""Managed release installs: one per tool at a time, and cleanup after the
swap or the uninstall rename never undoes it."""

import json
import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.test_deps_manage import _runnable_dorado
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.file_lock import build_lock
from wgsextract_cli.core.managed_binaries import managed_binary
from wgsextract_cli.main import main


@pytest.fixture
def dorado(tmp_path, monkeypatch):
    """The host's Dorado release, a config whose managed tools folder is under
    ``tmp_path``, and the download boundary writing a runnable release."""
    release = managed_binary("dorado")
    if release is None:
        pytest.skip("no Dorado release for this host")
    jar_dir = tmp_path / "jartools"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'jar_directory = "{jar_dir.as_posix()}"\n', encoding="utf-8")
    downloads: list[Path] = []

    def fake_download(url, dest, **_kwargs):  # the network boundary
        downloads.append(Path(dest))
        _runnable_dorado(release, dest)
        return True

    monkeypatch.setattr(
        "wgsextract_cli.core.ref_library.downloads.download_file", fake_download
    )
    yield release, jar_dir, cfg, downloads
    clear_tool_caches()


def _run(capsys, cfg: Path, *argv: str) -> tuple[int, dict]:
    capsys.readouterr()
    try:
        main(["--json", "--config", str(cfg), "deps", *argv])
        code = 0
    except SystemExit as exit_:
        code = int(exit_.code or 0)
    return code, json.loads(capsys.readouterr().out)


def _pin(folder: Path) -> None:
    """Make ``folder`` impossible to delete but still possible to rename: a
    read-only file on Windows, a read-only directory elsewhere."""
    if sys.platform == "win32":
        os.chmod(next(p for p in folder.rglob("*") if p.is_file()), stat.S_IREAD)
        return
    if os.geteuid() == 0:
        pytest.skip("root can delete from a read-only directory")
    os.chmod(
        next(p for p in folder.rglob("*") if p.is_dir()), stat.S_IRUSR | stat.S_IXUSR
    )


def _unpin(folder: Path) -> None:
    for path in folder.rglob("*"):
        if not path.is_symlink():
            os.chmod(path, stat.S_IRWXU)


def test_an_install_waits_for_another_of_the_same_tool(dorado, capsys):
    """An install doesn't start while another holds the tool's lock; each
    install unpacks in its own folder, and leftovers of an interrupted one
    are swept."""
    release, jar_dir, cfg, downloads = dorado
    folder = jar_dir / "dorado"
    (folder / ".unpacking-interrupted").mkdir(parents=True)
    (folder / f".old-{release.installed}-interrupted").mkdir()
    held, released = threading.Event(), []

    def other_install() -> None:  # another process installing dorado
        with build_lock(str(jar_dir / ".dorado.lock")):
            held.set()
            time.sleep(1.5)
            released.append(time.monotonic())

    holder = threading.Thread(target=other_install)
    holder.start()
    assert held.wait(timeout=30)
    code, document = _run(capsys, cfg, "install", "dorado", "--accept-license")
    started = time.monotonic()
    holder.join(timeout=30)

    assert code == 0, document
    assert released and released[0] <= started
    assert _run(capsys, cfg, "install", "dorado", "--accept-license")[0] == 0
    first, second = (path.parent for path in downloads)
    assert first != second and first.name.startswith(".unpacking-")
    assert sorted(p.name for p in folder.iterdir()) == [release.installed]


def _left_warning(document: dict) -> str:
    (warning,) = (
        w for w in document["warnings"] if w["code"] == "managed_release_left"
    )
    return warning["message"]


def test_a_reinstall_keeps_the_new_release_when_the_old_cant_be_deleted(dorado, capsys):
    """The swap commits a reinstall: an old release that can't be deleted is
    left with a warning naming it, and the new release is used."""
    release, jar_dir, cfg, _ = dorado
    folder = jar_dir / "dorado"
    assert _run(capsys, cfg, "install", "dorado", "--accept-license")[0] == 0
    _pin(folder / release.installed)
    try:
        code, document = _run(capsys, cfg, "install", "dorado", "--accept-license")
        (left,) = folder.glob(f".old-{release.installed}-*")
        assert code == 0, document
        assert Path(document["details"]["path"]) == folder / release.executable
        assert str(left) in _left_warning(document)
    finally:
        _unpin(folder)


def test_an_uninstall_succeeds_when_its_files_cant_be_deleted(dorado, capsys):
    """The rename commits an uninstall: the tool is gone even when its files
    can't be deleted, with a warning naming where they were left."""
    release, jar_dir, cfg, _ = dorado
    folder = jar_dir / "dorado"
    assert _run(capsys, cfg, "install", "dorado", "--accept-license")[0] == 0
    _pin(folder / release.installed)
    try:
        code, document = _run(capsys, cfg, "uninstall", "dorado")
        (left,) = folder.glob(f".old-{release.installed}-*")
        assert code == 0, document
        assert not (folder / release.installed).exists()
        assert str(left) in _left_warning(document)
        tools = _run(capsys, cfg, "list")[1]["details"]["tools"]
        row = next(row for row in tools if row["name"] == "dorado")
        assert not row["can_uninstall"]
    finally:
        _unpin(folder)
