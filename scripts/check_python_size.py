"""Bound new Python modules and ratchet explicit existing outlier budgets down."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path, PurePosixPath
from typing import NamedTuple, NoReturn

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 800
DIRECTORIES = ("src", "tests", "scripts")
IGNORED = {
    ".git",
    ".pixi",
    "__pycache__",
    "node_modules",
    ".env",
    ".env.local",
    ".venv",
    ".tox",
    ".nox",
    "env",
    "venv",
    "ENV",
    "env.bak",
    "venv.bak",
}
BASELINE = "scripts/python_size_baseline.json"


class Finding(NamedTuple):
    path: str
    code: str
    lines: int
    limit: int


def walk_error(error: OSError) -> NoReturn:
    raise error


def python_sizes(root: Path) -> dict[str, int]:
    sizes = {}
    for folder in DIRECTORIES:
        top = root / folder
        try:
            top.stat()
        except FileNotFoundError:
            continue
        if top.is_symlink():
            continue
        for directory, children, names in os.walk(
            top, followlinks=False, onerror=walk_error
        ):
            children[:] = [
                name
                for name in children
                if name not in IGNORED and not Path(directory, name).is_symlink()
            ]
            for name in names:
                file = Path(directory, name)
                if file.suffix != ".py" or file.is_symlink():
                    continue
                data = file.read_bytes()
                sizes[file.relative_to(root).as_posix()] = len(data.splitlines())
    return sizes


def read_baseline(path: Path) -> dict[str, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"version", "files"}:
        raise ValueError("Expected version/files baseline object")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported baseline version")
    files = data["files"]
    if not isinstance(files, dict):
        raise ValueError("Expected baseline files object")
    for name, ceiling in files.items():
        if not isinstance(name, str):
            raise ValueError("Baseline paths must be strings")
        file = PurePosixPath(name)
        if (
            file.is_absolute()
            or "\\" in name
            or ".." in file.parts
            or not file.parts
            or file.parts[0] not in DIRECTORIES
            or file.suffix != ".py"
            or file.as_posix() != name
            or type(ceiling) is not int
            or ceiling <= LIMIT
        ):
            raise ValueError(f"Invalid baseline entry: {name}")
    return files


def findings(sizes: dict[str, int], ceilings: dict[str, int]) -> list[Finding]:
    result = [
        Finding(name, "python_size_exceeded", count, ceilings.get(name, LIMIT))
        for name, count in sorted(sizes.items())
        if count > ceilings.get(name, LIMIT)
    ]
    result.extend(
        Finding(name, "python_size_stale_baseline", sizes.get(name, 0), ceiling)
        for name, ceiling in sorted(ceilings.items())
        if name not in sizes
    )
    return result


def refresh_baseline(
    path: Path, sizes: dict[str, int], ceilings: dict[str, int]
) -> None:
    lowered = {
        name: min(ceiling, sizes[name])
        for name, ceiling in sorted(ceilings.items())
        if sizes.get(name, 0) > LIMIT
    }
    staged = path.with_name(f".{path.name}.{os.getpid()}.new")
    created = False
    try:
        with staged.open("x", encoding="utf-8") as output:
            created = True
            output.write(json.dumps({"version": 1, "files": lowered}, indent=2) + "\n")
        staged.replace(path)
    finally:
        if created:
            staged.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path, default=Path(BASELINE))
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Lower/remove existing ceilings; never add or increase an exemption",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    baseline = args.baseline
    if not baseline.is_absolute():
        baseline = root / baseline
    try:
        if not root.is_dir():
            raise ValueError(f"Repository root does not exist: {root}")
        ceilings = read_baseline(baseline)
        sizes = python_sizes(root)
        issues = findings(sizes, ceilings)
        if args.refresh and not any(
            issue.code == "python_size_exceeded" for issue in issues
        ):
            refresh_baseline(baseline, sizes, ceilings)
            issues = findings(sizes, read_baseline(baseline))
    except (OSError, ValueError) as error:
        print(f"python_size_invalid_baseline: {error}", file=sys.stderr)
        return 2
    for issue in issues:
        if issue.code == "python_size_stale_baseline":
            detail = (
                "remove only this obsolete entry; global --refresh also "
                "lowers/removes unrelated ceilings"
            )
        else:
            detail = "split the module; --refresh cannot raise its ceiling"
        print(
            f"{issue.path}: {issue.code}: {issue.lines} lines, "
            f"ceiling {issue.limit}; {detail}"
        )
    if issues:
        return 1
    print(f"Python size check passed ({len(sizes)} files; new-file ceiling {LIMIT}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
