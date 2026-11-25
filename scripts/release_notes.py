#!/usr/bin/env python3
"""Check and assemble release-note fragments.

Each change adds its own file to docs/release-notes/unreleased/ or
gui/docs/changelog/unreleased/, so PRs never edit a shared notes file. A
fragment is named NUMBER-SLUG.md after its issue or PR and holds one or more
sections, each a `## Section` heading followed by a `- ` list of entries.

  python scripts/release_notes.py check [--base REF]
  python scripts/release_notes.py assemble X.Y.Z

`assemble` writes docs/release-notes/vX.Y.Z.md and adds a dated release to
gui/docs/CHANGELOG.md, then deletes the fragments. The release PR adds a
summary by hand (see docs/releasing.md). With --base, `check` also fails when
the change since REF is a release but fragments remain.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

NOTES = Path(__file__).resolve().parents[1] / "docs" / "release-notes"
GUI_CHANGELOG = NOTES.parents[1] / "gui" / "docs" / "CHANGELOG.md"
SECTIONS = ("Changed", "New", "Fixes", "Deprecated", "Removed")
GUI_SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
NAME = re.compile(r"(?P<number>[1-9]\d*)-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
HEADING = re.compile(r"##\s+(?P<section>.+?)\s*")
VERSION = re.compile(r"v?(?P<version>\d+\.\d+\.\d+)")
REPOSITORY_URL = "https://github.com/WGSExtract/wgsextract-cli"
COMPARE = f"{REPOSITORY_URL}/compare"
README = "README.md"


@dataclass(frozen=True)
class Fragment:
    path: Path
    number: int
    sections: dict[str, str]


def parse(path: Path, allowed: tuple[str, ...]) -> tuple[Fragment | None, list[str]]:
    name = f"{path.parent.name}/{path.name}"
    match = NAME.fullmatch(path.name)
    if match is None:
        return None, [f"{name}: name it NUMBER-slug.md, e.g. 1155-missing-ystrs.md"]
    errors: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        heading = HEADING.fullmatch(line)
        if heading:
            section = heading["section"]
            if section == "Desktop app" and allowed == SECTIONS:
                errors.append(
                    f"{name}:{number}: put desktop app entries in "
                    "gui/docs/changelog/unreleased/"
                )
            elif section not in allowed:
                errors.append(
                    f"{name}:{number}: unknown section {section!r}; use one of "
                    + ", ".join(allowed)
                )
            elif section in sections:
                errors.append(f"{name}:{number}: duplicate section {section!r}")
            current = sections.setdefault(section, [])
        elif line.startswith("# "):
            errors.append(f"{name}:{number}: the release adds the title")
        elif current is not None:
            current.append(line)
        elif line.strip():
            errors.append(f"{name}:{number}: start with a ## section heading")
    bodies = {section: "\n".join(lines).strip() for section, lines in sections.items()}
    # Entries wrap onto indented lines; anything else would leak into the notes.
    errors += [
        f"{name}: section {section!r} must be a list of `- ` entries"
        for section, body in bodies.items()
        if not body.startswith("- ")
        or any(
            line.strip() and not line.startswith(("- ", " ", "\t"))
            for line in body.splitlines()
        )
    ]
    if not sections and not errors:
        errors.append(f"{name}: no ## section heading")
    if errors:
        return None, errors
    return Fragment(path, int(match["number"]), bodies), []


def collect(
    notes: Path, allowed: tuple[str, ...] = SECTIONS
) -> tuple[list[Fragment], list[str]]:
    fragments: list[Fragment] = []
    errors: list[str] = []
    if (notes / "unreleased.md").exists():
        errors.append(
            "unreleased.md: move its entries into unreleased/NUMBER-slug.md fragments"
        )
    unreleased = notes / "unreleased"
    if not (unreleased / README).is_file():
        # Without it a deleted or misplaced folder would pass as "no fragments".
        return [], [*errors, f"unreleased/{README} is missing"]
    for path in sorted(unreleased.iterdir()):
        # Finder and editors leave dotfiles beside the fragments.
        if path.name == README or path.name.startswith("."):
            continue
        if not path.is_file():
            errors.append(f"unreleased/{path.name}: expected a Markdown file")
            continue
        fragment, problems = parse(path, allowed)
        errors += problems
        if fragment:
            fragments.append(fragment)
    fragments.sort(key=lambda fragment: (fragment.number, fragment.path.name))
    return fragments, errors


def git(notes: Path, *args: str) -> list[str] | None:
    """Output lines from the repository that holds docs/release-notes, if any."""
    repository = notes.parents[1]
    if not (repository / ".git").exists():
        return None
    command = ["git", "-C", str(repository), *args]
    return subprocess.run(
        command, capture_output=True, text=True, check=True
    ).stdout.splitlines()


def project_version(notes: Path, revision: str) -> str | None:
    text = "\n".join(git(notes, "show", f"{revision}:pyproject.toml") or [])
    version = tomllib.loads(text).get("project", {}).get("version")
    return version if isinstance(version, str) else None


def released(notes: Path, base: str) -> list[tuple[str, str]]:
    """What since base makes this a release: added notes files, a version bump."""
    added = git(
        notes,
        "diff",
        "--name-only",
        "--no-renames",
        "--diff-filter=A",
        f"{base}...HEAD",
        "--",
        "docs/release-notes/",
    )
    if added is None:
        raise ValueError(f"{notes.parents[1]} is not a git checkout")
    releases = [
        (match["version"], f"{Path(path).name} is added here")
        for path in added
        if Path(path).parent == Path("docs/release-notes")
        and Path(path).stem.startswith("v")
        and (match := VERSION.fullmatch(Path(path).stem))
    ]
    version = project_version(notes, "HEAD")
    if version is not None and version != project_version(notes, base):
        releases.append((version, f"the version becomes {version} here"))
    return releases


def previous(notes: Path, version: tuple[int, ...]) -> str | None:
    """The newest older release tag; notes files only without tags.

    A notes file can exist for a release whose workflow never tagged it.
    """
    names = git(notes, "tag", "--list", "v*") or [
        path.stem for path in notes.glob("v*.md")
    ]
    releases = [
        tuple(int(part) for part in match["version"].split("."))
        for name in names
        if name.startswith("v") and (match := VERSION.fullmatch(name))
    ]
    older = [release for release in releases if release < version]
    return ".".join(map(str, max(older))) if older else None


def grouped(fragments: list[Fragment], sections: tuple[str, ...]) -> dict[str, str]:
    return {
        section: "\n".join(bodies)
        for section in sections
        if (
            bodies := [
                fragment.sections[section]
                for fragment in fragments
                if section in fragment.sections
            ]
        )
    }


def render(
    version: str,
    fragments: list[Fragment],
    before: str | None,
    desktop: dict[str, str],
) -> str:
    parts = [f"# WGS Extract {version}"]
    sections = grouped(fragments, SECTIONS)
    if desktop:
        sections["Desktop app"] = "\n\n".join(
            f"### {section}\n\n{body}" for section, body in desktop.items()
        )
    parts.extend(f"## {section}\n\n{body}" for section, body in sections.items())
    if before:
        parts.append(f"**Full changelog:** <{COMPARE}/v{before}...v{version}>")
    return "\n\n".join(parts) + "\n"


def collect_gui(changelog: Path | None) -> tuple[str, list[Fragment], list[str]]:
    if changelog is None:
        return "", [], []
    directory = changelog.parent / "changelog"
    fragments, errors = collect(directory, GUI_SECTIONS)
    try:
        history = changelog.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        history = ""
        errors.append(f"cannot read {changelog}: {error}")
    if re.search(r"^##\s+\[?Unreleased\b", history, re.MULTILINE | re.IGNORECASE):
        errors.append("move the shared Unreleased section to individual fragments")
    return history, fragments, [f"desktop changelog: {error}" for error in errors]


def has_gui_release(history: str, version: str) -> bool:
    return bool(
        re.search(
            rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
            history,
            re.MULTILINE,
        )
    )


def render_gui(history: str, version: str, desktop: dict[str, str]) -> str:
    if re.search(rf"^## \[{re.escape(version)}\](?:\s|$)", history, re.MULTILINE):
        raise ValueError(f"the desktop changelog already has release {version}")
    changed = (
        f"- Bump the bundled WGSExtract CLI installer target to `v{version}` "
        f"([release notes]({REPOSITORY_URL}/releases/tag/v{version}))."
    )
    if "Changed" in desktop:
        changed += "\n" + desktop["Changed"]
    sections = {**desktop, "Changed": changed}
    today = datetime.now(timezone.utc).date().isoformat()
    release = [f"## [{version}] - {today}"]
    release += [
        f"### {section}\n{sections[section]}"
        for section in GUI_SECTIONS
        if section in sections
    ]
    index = re.search(r"^## ", history, re.MULTILINE)
    start = index.start() if index else len(history)
    return (
        history[:start].rstrip()
        + "\n\n"
        + "\n\n".join(release)
        + "\n\n"
        + history[start:]
    )


def write_release(
    output: Path, notes: str, changelog: Path | None, history: str
) -> None:
    changes = {output: notes}
    if changelog is not None:
        changes[changelog] = history
    staged: dict[Path, Path] = {}
    try:
        for path, text in changes.items():
            stage = path.with_suffix(".md.assemble")
            with stage.open("x", encoding="utf-8") as file:
                staged[path] = stage
                file.write(text)
        staged[output].replace(output)
        if changelog is not None:
            try:
                staged[changelog].replace(changelog)
            except OSError:
                output.unlink()
                raise
    finally:
        for stage in staged.values():
            stage.unlink(missing_ok=True)


def assemble(notes: Path, requested: str, changelog: Path | None) -> Path:
    match = VERSION.fullmatch(requested)
    if match is None:
        raise ValueError(f"{requested!r} is not a X.Y.Z version")
    version = match["version"]
    output = notes / f"v{version}.md"
    if output.exists():
        raise ValueError(f"{output.name} already exists")
    fragments, errors = collect(notes)
    history, app_fragments, app_errors = collect_gui(changelog)
    errors += app_errors
    if errors:
        raise ValueError("\n".join(errors))
    if not fragments and changelog is None:
        raise ValueError("no release-note fragments in unreleased/")
    before = previous(notes, tuple(int(part) for part in version.split(".")))
    desktop = grouped(app_fragments, GUI_SECTIONS)
    if changelog is not None:
        history = render_gui(history, version, desktop)
    write_release(
        output, render(version, fragments, before, desktop), changelog, history
    )
    for fragment in fragments + app_fragments:
        fragment.path.unlink()
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--notes-dir", type=Path, default=NOTES, help=argparse.SUPPRESS)
    parser.add_argument("--gui-changelog", type=Path, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Validate fragment names and sections.")
    check.add_argument(
        "--base",
        help="Also reject fragments left over by a release notes file added "
        "since this git ref (PR and merge-queue checks).",
    )
    build = commands.add_parser("assemble", help="Write vX.Y.Z.md from fragments.")
    build.add_argument("version", help="Release version, X.Y.Z")
    args = parser.parse_args(argv)
    changelog = args.gui_changelog
    if changelog is None and args.notes_dir == NOTES:
        changelog = GUI_CHANGELOG
    if args.command == "check":
        fragments, errors = collect(args.notes_dir)
        history, app_fragments, app_errors = collect_gui(changelog)
        errors += app_errors
        if args.base and (changelog is not None or fragments):
            try:
                added = released(args.notes_dir, args.base)
            except (ValueError, subprocess.CalledProcessError) as error:
                print(f"Cannot compare with {args.base}: {error}", file=sys.stderr)
                return 1
            errors += [
                f"{reason}, but {len(fragments)} CLI and {len(app_fragments)} GUI "
                "release-note fragment(s) remain: the release must assemble them "
                "(move late entries into the release notes and, for GUI fragments, "
                "the desktop changelog, then delete them)"
                for _, reason in added
                if fragments or app_fragments
            ]
            if changelog is not None:
                errors += [
                    f"desktop changelog has no dated entry for {version}; run assemble"
                    for version in dict.fromkeys(version for version, _ in added)
                    if not has_gui_release(history, version)
                ]
        if errors:
            print("Release-note fragments are invalid:", file=sys.stderr)
            print("\n".join(f"  - {error}" for error in errors), file=sys.stderr)
            return 1
        print(f"{len(fragments)} release-note fragment(s) are valid.")
        if changelog is not None:
            print(f"{len(app_fragments)} desktop changelog fragment(s) are valid.")
        return 0
    try:
        output = assemble(args.notes_dir, args.version, changelog)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    if changelog is not None:
        print(f"Updated {changelog}.")
    print(f"Wrote {output}; add the release summary.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
