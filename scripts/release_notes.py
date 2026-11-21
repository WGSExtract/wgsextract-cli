#!/usr/bin/env python3
"""Check and assemble release-note fragments.

Each change adds its own file to docs/release-notes/unreleased/, so PRs never
edit a shared notes file (#1281). A fragment is named NUMBER-SLUG.md after its
issue or PR (for example 1155-missing-ystrs.md) and holds one or more of the
sections below, each a `## Section` heading followed by a `- ` list of entries.

  python scripts/release_notes.py check [--base REF]
  python scripts/release_notes.py assemble X.Y.Z

`assemble` writes docs/release-notes/vX.Y.Z.md from every fragment, grouped by
section in the order below and by number within a section, then deletes the
fragments; the release PR adds the summary and the app's changes by hand (see
docs/releasing.md). With --base, `check` also fails when the change since REF is
a release (it adds a vX.Y.Z.md or bumps pyproject.toml's version) while
fragments remain, so none is left out of that release or announced twice.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NOTES = Path(__file__).resolve().parents[1] / "docs" / "release-notes"
SECTIONS = ("Changed", "New", "Fixes", "Deprecated", "Removed", "Desktop app")
NAME = re.compile(r"(?P<number>[1-9]\d*)-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
HEADING = re.compile(r"##\s+(?P<section>.+?)\s*")
VERSION = re.compile(r"v?(?P<version>\d+\.\d+\.\d+)")
COMPARE = "https://github.com/WGSExtract/wgsextract-cli/compare"
README = "README.md"


@dataclass(frozen=True)
class Fragment:
    path: Path
    number: int
    sections: dict[str, str]


def parse(path: Path) -> tuple[Fragment | None, list[str]]:
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
            if section not in SECTIONS:
                errors.append(
                    f"{name}:{number}: unknown section {section!r}; use one of "
                    + ", ".join(SECTIONS)
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
    errors += [
        f"{name}: section {section!r} must be a list of `- ` entries"
        for section, body in bodies.items()
        if not body.startswith("- ")
    ]
    if not sections and not errors:
        errors.append(f"{name}: no ## section heading")
    if errors:
        return None, errors
    return Fragment(path, int(match["number"]), bodies), []


def collect(notes: Path) -> tuple[list[Fragment], list[str]]:
    fragments: list[Fragment] = []
    errors: list[str] = []
    if (notes / "unreleased.md").exists():
        errors.append(
            "unreleased.md: move its entries into unreleased/NUMBER-slug.md fragments"
        )
    unreleased = notes / "unreleased"
    paths = sorted(unreleased.iterdir()) if unreleased.is_dir() else []
    for path in paths:
        # Finder and editors leave dotfiles beside the fragments.
        if path.name == README or path.name.startswith("."):
            continue
        if not path.is_file():
            errors.append(f"unreleased/{path.name}: expected a Markdown file")
            continue
        fragment, problems = parse(path)
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
    text = git(notes, "show", f"{revision}:pyproject.toml") or []
    return next(
        (m[1] for line in text if (m := re.fullmatch(r'version = "([^"]+)"', line))),
        None,
    )


def released(notes: Path, base: str) -> list[str]:
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
        f"{Path(path).name} is added here"
        for path in added
        if Path(path).parent == Path("docs/release-notes")
        and Path(path).stem.startswith("v")
        and VERSION.fullmatch(Path(path).stem)
    ]
    version = project_version(notes, "HEAD")
    if version != project_version(notes, base):
        releases.append(f"the version becomes {version} here")
    return releases


def previous(notes: Path, version: tuple[int, ...]) -> str | None:
    """The newest older release: a tag, or a notes file (when not checked out)."""
    names = [path.stem for path in notes.glob("v*.md")]
    names += git(notes, "tag", "--list", "v*") or []
    releases = [
        tuple(int(part) for part in match["version"].split("."))
        for name in names
        if name.startswith("v") and (match := VERSION.fullmatch(name))
    ]
    older = [release for release in releases if release < version]
    return ".".join(map(str, max(older))) if older else None


def render(version: str, fragments: list[Fragment], before: str | None) -> str:
    parts = [f"# WGS Extract {version}"]
    for section in SECTIONS:
        bodies = [f.sections[section] for f in fragments if section in f.sections]
        if bodies:
            parts.append(f"## {section}\n\n" + "\n".join(bodies))
    if before:
        parts.append(f"**Full changelog:** <{COMPARE}/v{before}...v{version}>")
    return "\n\n".join(parts) + "\n"


def assemble(notes: Path, requested: str) -> Path:
    match = VERSION.fullmatch(requested)
    if match is None:
        raise ValueError(f"{requested!r} is not a X.Y.Z version")
    version = match["version"]
    output = notes / f"v{version}.md"
    if output.exists():
        raise ValueError(f"{output.name} already exists")
    fragments, errors = collect(notes)
    if errors:
        raise ValueError("\n".join(errors))
    if not fragments:
        raise ValueError("no release-note fragments in unreleased/")
    before = previous(notes, tuple(int(part) for part in version.split(".")))
    output.write_text(render(version, fragments, before), encoding="utf-8")
    for fragment in fragments:
        fragment.path.unlink()
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--notes-dir", type=Path, default=NOTES, help=argparse.SUPPRESS)
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
    if args.command == "check":
        fragments, errors = collect(args.notes_dir)
        if args.base and fragments:
            try:
                added = released(args.notes_dir, args.base)
            except (ValueError, subprocess.CalledProcessError) as error:
                print(f"Cannot compare with {args.base}: {error}", file=sys.stderr)
                return 1
            errors += [
                f"{reason}, but {len(fragments)} fragment(s) remain in "
                "unreleased/: the release must assemble them (move late ones' "
                "entries into its notes file and delete them)"
                for reason in added
            ]
        if errors:
            print("Release-note fragments are invalid:", file=sys.stderr)
            print("\n".join(f"  - {error}" for error in errors), file=sys.stderr)
            return 1
        print(f"{len(fragments)} release-note fragment(s) are valid.")
        return 0
    try:
        output = assemble(args.notes_dir, args.version)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Wrote {output}; add the summary and the app's changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
