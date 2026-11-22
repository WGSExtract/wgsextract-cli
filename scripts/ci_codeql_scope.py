#!/usr/bin/env python3
"""Choose which CodeQL languages a pull request must analyze afresh (#1373).

A language whose files the PR leaves untouched reuses the base branch's latest
analysis instead, so the CodeQL check still compares every category and never
reports "configurations not found". Unknown inputs fail open to every language.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path, PurePosixPath

# File types each extractor reads, from CodeQL's supported-languages table.
EXTENSIONS = {
    "python": {".py", ".pyi"},
    "javascript-typescript": {
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".es",
        ".es6",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".htm",
        ".html",
        ".xhtm",
        ".xhtml",
        ".vue",
        ".hbs",
        ".ejs",
        ".njk",
        ".json",
        ".yaml",
        ".yml",
        ".raml",
        ".xml",
    },
    "rust": {".rs"},
    "actions": set(),
}
LANGUAGES = tuple(EXTENSIONS)
# The workflow, its scope rules and repository CI setup affect every language.
ALL_LANGUAGE_INPUTS = (".github/", "scripts/ci_codeql_scope.py")


def languages(paths: list[str], crate_roots: set[str]) -> list[str]:
    """Return the languages whose analysis these changed paths may alter."""
    selected: set[str] = set()
    for path in paths:
        if path.startswith(ALL_LANGUAGE_INPUTS):
            return list(LANGUAGES)
        pure = PurePosixPath(path)
        suffix = pure.suffix.lower()
        selected.update(
            language for language, suffixes in EXTENSIONS.items() if suffix in suffixes
        )
        if pure.name in {"action.yml", "action.yaml"}:
            selected.add("actions")
        # Rust extraction reads manifests and anything a crate may embed.
        if pure.name in {"Cargo.toml", "Cargo.lock"} or any(
            path.startswith(f"{root}/") for root in crate_roots
        ):
            selected.add("rust")
    return [language for language in LANGUAGES if language in selected]


def git_lines(*args: str) -> list[str]:
    output = subprocess.check_output(["git", *args])
    return output.decode().rstrip("\0").split("\0") if output else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Analyze every language.")
    group.add_argument("--base", help="Base commit of the pull request diff.")
    parser.add_argument("--head", help="Head commit of the pull request diff.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.all:
        selected = list(LANGUAGES)
    else:
        if not args.head:
            parser.error("--base requires --head")
        paths = git_lines(
            "diff",
            "-z",
            "--name-only",
            "--no-renames",
            f"{args.base}...{args.head}",
            "--",
        )
        crate_roots = {
            str(PurePosixPath(manifest).parent)
            for manifest in git_lines("ls-files", "-z", "--", "*Cargo.toml")
        }
        selected = languages(paths, crate_roots)
    output = f"analyze={json.dumps(selected)}\n"
    print(output, end="")
    if args.output:
        with args.output.open("a", encoding="utf-8") as stream:
            stream.write(output)


if __name__ == "__main__":
    main()
