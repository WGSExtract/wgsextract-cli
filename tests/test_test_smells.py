"""scripts/test_smells.py flags change detectors only in newly added test lines."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "test_smells.py"

OLD = """from unittest.mock import patch

from wgsextract_cli.core import utils


def test_old(monkeypatch):
    monkeypatch.setattr(utils, "_old_private", None)
    monkeypatch.setattr(
        utils,
        "public_helper",
        None,
    )
"""

NEW = """
def test_new(monkeypatch, run, cmd):
    monkeypatch.setattr(utils, "_new_private", None)
    monkeypatch.setattr(utils, "public_helper", None)
    patch("wgsextract_cli.core.utils._dotted")
    assert cmd == ["samtools", "sort", "-o", "x.bam"]
    run.assert_called_once_with(["tabix", "-l", "x.vcf.gz"], check=True)
    assert cmd[:3] == ["samtools", "sort", "-m"]  # contract: -m is per-thread RAM
    assert cmd == ["samtools", "index", "x.bam"]
    # contract: -m sets the documented per-thread memory
    assert cmd[:3] == ["samtools", "sort", "-m"]
    assert subprocess.run(["samtools", "view", "x.bam"]).returncode == 0
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _smells(
    root: Path,
    encoding: str = "utf-8",
    *,
    base: str = "HEAD",
    flags: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, SCRIPT, "--base", base, "--root", root, *flags],
        capture_output=True,
        encoding=encoding,
        env={**os.environ, "PYTHONIOENCODING": encoding},
    )


def _repo(tmp_path: Path) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(OLD)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "diff.mnemonicPrefix", "true")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@t")
    _commit(tmp_path, "base")
    return tests


def _commit(root: Path, message: str) -> None:
    _git(root, "add", ".")
    _git(root, "commit", "-qm", message)


def test_1554_blocking_gate_only_checks_pr_test_changes(tmp_path: Path) -> None:
    tests = _repo(tmp_path)
    edited = tests / "test_edited.py"
    edited.write_text(OLD)
    _commit(tmp_path, "another legacy test")
    _git(tmp_path, "switch", "-qc", "upstream")
    (tests / "test_x.py").write_text(OLD.replace("_old_private", "public_helper"))
    _commit(tmp_path, "upstream fixes legacy test")
    _git(tmp_path, "switch", "-qc", "pr-head", "upstream^")
    edited.write_text(
        OLD
        + "\n\ndef test_detected_tool(tool):\n"
        + '    assert tool in ["samtools", "bcftools"]\n'
    )
    (tmp_path / "not_a_test.py").write_text(OLD)
    _commit(tmp_path, "clean PR changes")

    flags = ("--github", "--check")
    clean = _smells(tmp_path, base="upstream", flags=flags)
    assert clean.returncode == 0
    assert "TS001" not in clean.stdout

    edited.write_text(OLD.replace('"public_helper"', '"_introduced_private"'))
    (tests / "test_new.py").write_text(OLD)
    _commit(tmp_path, "PR introduces two findings")
    _git(tmp_path, "switch", "upstream")
    _git(tmp_path, "merge", "--no-ff", "--no-edit", "pr-head")

    blocked = _smells(tmp_path, base="HEAD^1", flags=flags)
    assert blocked.returncode == 1
    errors = [
        line for line in blocked.stdout.splitlines() if line.startswith("::error")
    ]
    assert len(errors) == 2
    assert "file=tests/test_edited.py,line=8,title=TS001::" in errors[0]
    assert "file=tests/test_new.py,line=7,title=TS001::" in errors[1]


def test_1554_removed_contract_exposes_only_its_surviving_assert(
    tmp_path: Path,
) -> None:
    tests = _repo(tmp_path)
    argv = (
        "def test_command(cmd):\n"
        "    # contract: indexing preserves the BAM path\n"
        '    assert cmd == ["samtools", "index", "x.bam"]\n'
        '    assert cmd == ["samtools", "view", "x.bam"]\n'
    )
    (tests / "test_argv.py").write_text(argv)
    (tests / "test_deleted.py").write_text(argv)
    _commit(tmp_path, "legacy argv assertions")
    lines = argv.splitlines(keepends=True)
    (tests / "test_argv.py").write_text("".join(lines[:1] + lines[2:]))
    (tests / "test_deleted.py").write_text("".join(lines[:1] + lines[3:]))

    blocked = _smells(tmp_path, flags=("--check",))

    assert blocked.returncode == 1
    findings = [line for line in blocked.stdout.splitlines() if ": TS002" in line]
    assert len(findings) == 1
    assert findings[0].startswith("tests/test_argv.py:2: TS002")


def test_reports_only_added_private_patches_and_argv_asserts(tmp_path: Path) -> None:
    tests = _repo(tmp_path)
    edited = OLD.replace('"public_helper"', '"_renamed_private"') + NEW
    (tests / "test_x.py").write_text(edited)

    result = _smells(tmp_path)

    assert result.returncode == 0
    findings = [line for line in result.stdout.splitlines() if ": TS" in line]
    assert findings == [
        "tests/test_x.py:8: TS001 patches private internal utils._renamed_private",
        "tests/test_x.py:15: TS001 patches private internal utils._new_private",
        "tests/test_x.py:17: TS001 patches private internal "
        "wgsextract_cli.core.utils._dotted",
        "tests/test_x.py:18: TS002 asserts a whole tool argv",
        "tests/test_x.py:19: TS002 asserts a whole tool argv",
        "tests/test_x.py:21: TS002 asserts a whole tool argv",
    ]


def test_new_untracked_files_are_checked_and_broken_ones_skipped(
    tmp_path: Path,
) -> None:
    tests = _repo(tmp_path)
    (tests / "test_new.py").write_text(OLD)
    (tests / "test_broken.py").write_text("def test_(:\n")

    result = _smells(tmp_path)

    assert result.returncode == 0
    assert "tests/test_new.py:7: TS001" in result.stdout
    assert "tests/test_x.py" not in result.stdout
    assert "skipped tests/test_broken.py" in result.stdout


def test_non_ascii_test_paths_keep_their_own_findings(tmp_path: Path) -> None:
    tests = _repo(tmp_path)
    (tests / "test_é.py").write_text(OLD)
    _git(tmp_path, "add", ".")
    _git(tmp_path, *("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "é"))
    (tests / "test_é.py").write_text(OLD + NEW)
    (tests / "test_ü_new.py").write_text(OLD)

    result = _smells(tmp_path)

    assert "tests/test_é.py:15: TS001" in result.stdout
    assert "tests/test_ü_new.py:7: TS001" in result.stdout
    assert "tests/test_x.py" not in result.stdout

    # A console that can't show the name still gets the finding.
    result = _smells(tmp_path, encoding="ascii")
    assert result.returncode == 0
    assert "tests/test_\\xe9.py:15: TS001" in result.stdout


@pytest.mark.skipif(
    sys.platform != "linux", reason="needs a filesystem that allows non-UTF-8 names"
)
def test_a_non_utf8_test_path_is_still_checked(tmp_path: Path) -> None:
    tests = _repo(tmp_path)
    (tests / os.fsdecode(b"test_\xff.py")).write_text(OLD)

    result = _smells(tmp_path)

    assert result.returncode == 0
    assert "tests/test_\\udcff.py:7: TS001" in result.stdout
