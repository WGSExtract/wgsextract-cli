"""The generated-reference conflict workflow uses real Git and generators."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from wgsextract_cli.cli.help import SITE_BLOCK_END, SITE_BLOCK_START

ROOT = Path(__file__).resolve().parents[1]
GENERATED = (
    "tests/fixtures/api_cli_snapshot.json",
    "docs/api.md",
    "docs/commands.md",
)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Regeneration test",
            "-c",
            "user.email=regen@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def regen_repo(tmp_path: Path) -> Path:
    for relative in (
        "scripts/regen.py",
        "scripts/gen_api_docs.py",
        "scripts/gen_command_docs.py",
        "tests/cli_snapshot.py",
        "site_src/cli.md",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    for relative in GENERATED:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("base\n", encoding="utf-8")
    # Stale generated cards, but preserve the handwritten source around them.
    site = tmp_path / "site_src/cli.md"
    text = site.read_text(encoding="utf-8")
    start = text.index(SITE_BLOCK_START)
    end = text.index(SITE_BLOCK_END)
    site.write_text(
        text[:start] + SITE_BLOCK_START + "\nstale\n" + text[end:],
        encoding="utf-8",
    )
    git(tmp_path, "init", "--initial-branch=base")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    return tmp_path


def conflict(repo: Path, paths: tuple[str, ...]) -> None:
    for branch in ("left", "right"):
        git(repo, "switch", "-c", branch, "base")
        for relative in paths:
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{branch}\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", branch)
    assert git(repo, "merge", "--no-commit", "left", check=False).returncode == 1


def resolve(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/regen.py", "--resolve"],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("source", ["src/problem.py", "site_src/cli.md"])
def test_regen_resolve_refuses_unresolved_sources(
    regen_repo: Path, source: str
) -> None:
    conflict(regen_repo, (source,))
    before = {name: (regen_repo / name).read_bytes() for name in GENERATED}

    result = resolve(regen_repo)

    assert result.returncode == 2
    assert source in result.stderr and "conflicts" in result.stderr
    assert {name: (regen_repo / name).read_bytes() for name in GENERATED} == before
    assert (
        git(regen_repo, "diff", "--name-only", "--diff-filter=U").stdout.strip()
        == source
    )


def test_regen_resolve_rebuilds_and_stages_all_generated_outputs(
    regen_repo: Path,
) -> None:
    conflict(regen_repo, GENERATED)

    result = resolve(regen_repo)

    assert result.returncode == 0, result.stderr
    assert not git(regen_repo, "diff", "--name-only", "--diff-filter=U").stdout
    staged = git(regen_repo, "diff", "--cached", "--name-only").stdout.splitlines()
    assert set(staged) == {*GENERATED, "site_src/cli.md"}
    assert not git(regen_repo, "diff", "--name-only").stdout
    snapshot = json.loads((regen_repo / GENERATED[0]).read_text(encoding="utf-8"))
    assert snapshot["commands"]["microarray"]["help"]
    assert "MicroarrayParams" in (regen_repo / "docs/api.md").read_text(
        encoding="utf-8"
    )
    for name in ("docs/commands.md", "site_src/cli.md"):
        assert "microarray" in (regen_repo / name).read_text(encoding="utf-8")
    for generator in ("gen_api_docs.py", "gen_command_docs.py"):
        checked = subprocess.run(
            [sys.executable, f"scripts/{generator}", "--check"],
            cwd=regen_repo,
            capture_output=True,
            text=True,
        )
        assert checked.returncode == 0, checked.stderr
