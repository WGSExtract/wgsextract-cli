"""scripts/release_notes.py: per-change fragments replace the shared notes file (#1281)."""

import subprocess
from pathlib import Path

import pytest

from scripts.release_notes import main


def notes_dir(tmp_path: Path, fragments: dict[str, str]) -> Path:
    unreleased = tmp_path / "unreleased"
    unreleased.mkdir(parents=True)
    (unreleased / "README.md").write_text("# How to add notes\n")
    for version in ("0.8.8", "0.8.9", "1.0.0"):
        (tmp_path / f"v{version}.md").write_text(f"# WGS Extract {version}\n")
    for name, text in fragments.items():
        (unreleased / name).write_text(text)
    return tmp_path


def run(notes: Path, *args: str) -> int:
    return main(["--notes-dir", str(notes), *args])


def test_1281_assemble_groups_sections_and_orders_by_number(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = notes_dir(
        tmp_path,
        {
            "10-later-fix.md": "## Fixes\n\n- Later fix (#10).\n",
            "9-earlier-fix.md": "## Fixes\n\n- Earlier fix\n  wraps (#9).\n",
            "12-feature.md": "## New\n\n- Feature (#12).\n\n"
            "## Deprecated\n\n- `old` is deprecated (#12).\n",
            "11-behaviour.md": "## Changed\n\n- Changed default (#11).\n",
        },
    )

    assert run(notes, "assemble", "v0.9.0") == 0

    assert (notes / "v0.9.0.md").read_text() == (
        "# WGS Extract 0.9.0\n\n"
        "## Changed\n\n- Changed default (#11).\n\n"
        "## New\n\n- Feature (#12).\n\n"
        "## Fixes\n\n- Earlier fix\n  wraps (#9).\n- Later fix (#10).\n\n"
        "## Deprecated\n\n- `old` is deprecated (#12).\n\n"
        "**Full changelog:** <https://github.com/WGSExtract/wgsextract-cli/"
        "compare/v0.8.9...v0.9.0>\n"
    )
    assert sorted(p.name for p in (notes / "unreleased").iterdir()) == ["README.md"]
    capsys.readouterr()
    # The next release starts empty and valid.
    assert run(notes, "check") == 0
    assert "0 release-note fragment(s)" in capsys.readouterr().out


def test_1281_check_accepts_fragments_without_changing_them(tmp_path: Path) -> None:
    fragment = "## Fixes\n\n- A fix (#1155).\n"
    notes = notes_dir(tmp_path, {"1155-missing-ystrs.md": fragment, ".DS_Store": ""})

    assert run(notes, "check") == 0
    assert run(notes, "check") == 0
    assert (notes / "unreleased" / "1155-missing-ystrs.md").read_text() == fragment


@pytest.mark.parametrize(
    ("name", "text", "problem"),
    [
        ("missing-ystrs.md", "## Fixes\n\n- Fix.\n", "NUMBER-slug.md"),
        ("1155_missing.md", "## Fixes\n\n- Fix.\n", "NUMBER-slug.md"),
        ("1155-Missing.md", "## Fixes\n\n- Fix.\n", "NUMBER-slug.md"),
        ("1155-missing.txt", "## Fixes\n\n- Fix.\n", "NUMBER-slug.md"),
        ("1155-missing.md", "## Bugfixes\n\n- Fix.\n", "unknown section"),
        ("1155-missing.md", "- Fix.\n", "start with a ## section"),
        ("1155-missing.md", "## Fixes\n\n## New\n\n- Feature.\n", "list of `- `"),
        ("1155-missing.md", "## Fixes\n\n- A.\n\n## Fixes\n\n- B.\n", "duplicate"),
        ("1155-missing.md", "# WGS Extract\n\n## Fixes\n\n- Fix.\n", "title"),
    ],
)
def test_1281_check_rejects_bad_fragments(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    text: str,
    problem: str,
) -> None:
    notes = notes_dir(tmp_path, {name: text})

    assert run(notes, "check") == 1
    assert problem in capsys.readouterr().err


def test_1281_shared_unreleased_file_is_rejected_and_nothing_assembled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = notes_dir(tmp_path, {"7-fix.md": "## Fixes\n\n- Fix.\n"})
    (notes / "unreleased.md").write_text("## Fixes\n\n- Old shared entry.\n")

    assert run(notes, "check") == 1
    assert run(notes, "assemble", "0.9.0") == 1
    assert "unreleased.md" in capsys.readouterr().err
    assert not (notes / "v0.9.0.md").exists()
    assert (notes / "unreleased" / "7-fix.md").exists()


@pytest.mark.parametrize("version", ["0.8.9", "0.9", "next"])
def test_1281_assemble_refuses_existing_or_invalid_versions(
    tmp_path: Path, version: str
) -> None:
    notes = notes_dir(tmp_path, {"7-fix.md": "## Fixes\n\n- Fix.\n"})

    assert run(notes, "assemble", version) == 1
    assert (notes / "v0.8.9.md").read_text() == "# WGS Extract 0.8.9\n"
    assert (notes / "unreleased" / "7-fix.md").exists()


def test_1281_check_fails_without_the_unreleased_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = notes_dir(tmp_path, {})
    (notes / "unreleased" / "README.md").unlink()
    (notes / "unreleased").rmdir()

    assert run(notes, "check") == 1
    assert "unreleased/README.md is missing" in capsys.readouterr().err


def test_1281_assemble_without_fragments_writes_nothing(tmp_path: Path) -> None:
    notes = notes_dir(tmp_path, {})

    assert run(notes, "assemble", "0.9.0") == 1
    assert not (notes / "v0.9.0.md").exists()


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), "-c", "user.name=t", "-c", "user.email=t@t"]
        + list(args),
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A checkout whose notes live in docs/release-notes, as in this repository."""
    root = tmp_path / "repo"
    notes = notes_dir(root / "docs" / "release-notes", {})
    (notes / "unreleased" / "7-fix.md").write_text("## Fixes\n\n- Fix.\n")
    (root / "pyproject.toml").write_text('[project]\nversion = "0.8.9"\n')
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return root


def test_1281_previous_release_comes_from_tags_not_notes_files(
    repository: Path,
) -> None:
    # v0.8.10 was released as a draft with generated notes, so it has no
    # notes file; v0.8.11.md exists, but its release never got tagged.
    git(repository, "tag", "v0.8.9")
    git(repository, "tag", "v0.8.10")
    notes = repository / "docs" / "release-notes"
    (notes / "v0.8.11.md").write_text("# WGS Extract 0.8.11\n")

    assert run(notes, "assemble", "0.9.0") == 0
    assert "compare/v0.8.10...v0.9.0>" in (notes / "v0.9.0.md").read_text()


def test_1281_fragment_landing_after_assembly_fails_the_release_check(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = repository / "docs" / "release-notes"
    git(repository, "checkout", "-qb", "release-0.9.0")
    assert run(notes, "assemble", "0.9.0") == 0
    git(repository, "add", "-A")
    git(repository, "commit", "-qm", "release")
    assert run(notes, "check", "--base", "main") == 0

    # Another PR's fragment merges cleanly into the release PR's queue commit.
    git(repository, "checkout", "-qb", "queue")
    (notes / "unreleased" / "8-late.md").write_text("## New\n\n- Late.\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-qm", "late fragment")
    capsys.readouterr()
    assert run(notes, "check", "--base", "main") == 1
    assert "v0.9.0.md is added here" in capsys.readouterr().err

    # Once the release lands, PRs add fragments for the next one.
    git(repository, "checkout", "-q", "main")
    git(repository, "merge", "-q", "--ff-only", "release-0.9.0")
    git(repository, "checkout", "-qb", "next")
    (notes / "unreleased" / "9-next.md").write_text("## New\n\n- Next.\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-qm", "next fragment")
    assert run(notes, "check", "--base", "main") == 0


def test_1281_version_bump_without_assembled_notes_fails_the_check(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # release.yml can publish a draft without a notes file; its fragments
    # would otherwise be announced again by the next release.
    notes = repository / "docs" / "release-notes"
    git(repository, "checkout", "-qb", "release-0.9.0")
    (repository / "pyproject.toml").write_text('[project]\nversion = "0.9.0"\n')
    git(repository, "commit", "-qam", "bump")

    assert run(notes, "check", "--base", "main") == 1
    assert "version becomes 0.9.0" in capsys.readouterr().err


def test_1281_version_bump_is_read_from_toml_not_one_line_layout(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = repository / "docs" / "release-notes"
    layout = '[tool.other]\nversion = "1.0"\n\n[project]\nversion = "{}"  # bump\n'
    (repository / "pyproject.toml").write_text(layout.format("0.8.9"))
    git(repository, "commit", "-qam", "reformat")
    git(repository, "checkout", "-qb", "release-0.9.0")
    (repository / "pyproject.toml").write_text(layout.format("0.9.0"))
    git(repository, "commit", "-qam", "bump")

    assert run(notes, "check", "--base", "main") == 1
    assert "version becomes 0.9.0" in capsys.readouterr().err
