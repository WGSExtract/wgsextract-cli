"""scripts/release_notes.py: per-change fragments replace the shared notes file (#1281)."""

from pathlib import Path

import pytest

from scripts.release_notes import main


def notes_dir(tmp_path: Path, fragments: dict[str, str]) -> Path:
    unreleased = tmp_path / "unreleased"
    unreleased.mkdir()
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


def test_1281_assemble_without_fragments_writes_nothing(tmp_path: Path) -> None:
    notes = notes_dir(tmp_path, {})

    assert run(notes, "assemble", "0.9.0") == 1
    assert not (notes / "v0.9.0.md").exists()
