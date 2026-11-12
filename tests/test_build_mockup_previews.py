"""scripts/build_mockup_previews.py publishes only the static mockups of
mockups/* branches, one folder per branch, for the GitHub Pages previews."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_mockup_previews.py"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def branch(
    repo: Path, name: str, files: dict[str, str], symlink: str | None = None
) -> None:
    git(repo, "checkout", "--quiet", "--orphan", name)
    git(repo, "rm", "-rf", "--quiet", "--ignore-unmatch", ".")
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    if symlink:
        os.symlink("/etc/passwd", repo / symlink)
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", name)


def main_only(tmp_path: Path) -> Path:
    repo = tmp_path / "remote"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    branch(repo, "main", {"README.md": "main"})
    return repo


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    repo = main_only(tmp_path)
    branch(
        repo,
        "mockups/Randy's_Idea",
        {
            "gui/mockups/index.html": "<h1>idea</h1>",
            "gui/mockups/serve.py": "print('x')",
            "gui/mockups/shared/version.js": "evil()",
            "gui/mockups/feedback/c-home.json": "{}",
            "gui/mockups/__pycache__/x.js": "x",
            "src/other.txt": "not published",
        },
        symlink="gui/mockups/leak.txt",
    )
    branch(repo, "mockups/randy-s-idea", {"gui/mockups/index.html": "<h1>twin</h1>"})
    branch(repo, "mockups/no-mockups", {"README.md": "nothing here"})
    branch(repo, "feature/other", {"gui/mockups/index.html": "not a preview"})
    return repo


def build(tmp_path: Path, remote: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "--quiet")
    out = tmp_path / "site" / "preview"
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out",
            str(out),
            "--remote",
            f"file://{remote}",
        ],
        cwd=work,
        check=True,
        capture_output=True,
    )
    return out


def test_publishes_only_static_mockups_of_mockups_branches(tmp_path, remote):
    out = build(tmp_path, remote)
    folders = sorted(p.name for p in out.iterdir() if p.is_dir())
    # Names are sanitized and made unique; other branches are ignored.
    assert folders == ["randy-s-idea", "randy-s-idea-2"]
    idea = next(
        out / f
        for f in folders
        if (out / f / "index.html").read_text() == "<h1>idea</h1>"
    )
    published = sorted(
        p.relative_to(idea).as_posix() for p in idea.rglob("*") if p.is_file()
    )
    assert published == [
        "feedback/_pages.json",
        "feedback/c-home.json",
        "index.html",
        "shared/version.js",
    ]
    version = (idea / "shared" / "version.js").read_text()
    assert "evil" not in version
    stamp = json.loads(version.removeprefix("window.WGSM_VERSION = ").rstrip(";\n"))
    assert stamp["source"] == "preview"
    assert stamp["branch"] == "mockups/Randy's_Idea"
    assert json.loads((idea / "feedback" / "_pages.json").read_text()) == ["c-home"]


def test_index_lists_previews_and_escapes_branch_names(tmp_path, remote):
    index = (build(tmp_path, remote) / "index.html").read_text()
    assert "mockups/Randy&#x27;s_Idea" in index
    assert 'href="randy-s-idea/index.html"' in index
    assert "no-mockups" not in index


def test_no_preview_branches_still_builds_an_empty_index(tmp_path):
    # The Pages deploy failed once the last mockups/* branch was deleted:
    # a shallow fetch that matches no branch exits non-zero.
    out = build(tmp_path, main_only(tmp_path))
    assert [p.name for p in out.iterdir()] == ["index.html"]
    assert (
        "No <code>mockups/*</code> branches right now."
        in (out / "index.html").read_text()
    )
