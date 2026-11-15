"""Pass a validated PR selection to test-ci without shell interpolation."""

from __future__ import annotations

import argparse
import fnmatch
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_test_cache import (  # noqa: E402
    Fingerprint,
    Manifest,
    fingerprint,
    skip_summary,
)
from scripts.ci_test_tiers import SINGLE_OS_MODULES  # noqa: E402

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def test_targets(selection: str) -> list[str] | None:
    if selection == "all":
        return None
    targets = json.loads(selection)
    if not isinstance(targets, list) or not all(
        isinstance(target, str)
        and target.startswith("tests/")
        and target.endswith(".py")
        and ".." not in Path(target).parts
        and Path(target).is_file()
        for target in targets
    ):
        raise ValueError("Selection must contain existing repository test files")
    manifest = Path(__file__).resolve().parents[1] / "pixi.toml"
    task = tomllib.loads(manifest.read_text(encoding="utf-8"))["feature"]["dev"][
        "tasks"
    ]["test-ci"]
    words = shlex.split(task if isinstance(task, str) else task["cmd"])
    ignored: set[str] = set()
    patterns: set[str] = set()
    for index, word in enumerate(words):
        option, _, value = word.partition("=")
        if option not in {"--ignore", "--ignore-glob"}:
            continue
        value = value or words[index + 1]
        (ignored if option == "--ignore" else patterns).add(value.rstrip("/"))
    return [
        target
        for target in targets
        if not any(target == path or target.startswith(path + "/") for path in ignored)
        and not any(fnmatch.fnmatchcase(target, pattern) for pattern in patterns)
    ]


def apply_cache(
    targets: list[str] | None,
    job: str,
    sources: dict[str, Path],
    summary: Path | None,
) -> tuple[list[str] | None, Fingerprint, list[str]]:
    """Drop test files whose exact dependency hash already passed (#915)."""
    current = fingerprint(job)
    hashes = current.hashes
    candidates = (
        targets if targets is not None else test_targets(json.dumps(sorted(hashes)))
    )
    assert candidates is not None
    manifests = {label: Manifest.load(path) for label, path in sources.items()}
    skipped = {}
    for test in candidates:
        found = [
            label
            for label, manifest in manifests.items()
            if test in hashes and manifest.passed_with(hashes[test], current.names)
        ]
        if found:
            skipped[test] = ", ".join(found)
    report = skip_summary(candidates, skipped, hashes)
    print(report)
    if summary is not None:
        with summary.open("a", encoding="utf-8") as stream:
            stream.write(report)
    if targets is None and not skipped:
        return None, current, []
    remaining = [test for test in candidates if test not in skipped]
    return remaining, current, sorted(skipped)


def record_passes(manifest_path: Path, tests: list[str], current: Fingerprint) -> None:
    """Add passing hashes; write only when new, so unchanged caches aren't resaved."""
    manifest = Manifest.load(manifest_path)
    hashes = {current.hashes[test] for test in tests if test in current.hashes}
    if manifest.record(hashes, current.names, datetime.now(timezone.utc)):
        manifest.save(manifest_path)
        # Outside the cached directory: tells the workflow to save a new entry.
        directory = manifest_path.parent
        directory.with_name(directory.name + ".updated").touch()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", default="all")
    parser.add_argument("--generic-only", action="store_true")
    parser.add_argument("--platform-only", action="store_true")
    parser.add_argument(
        "--cache-job",
        help="Partition identity (OS, tier, shard, flags) for dependency hashes.",
    )
    parser.add_argument(
        "--cache-read",
        action="append",
        default=[],
        metavar="LABEL=MANIFEST",
        help="Skip test files whose exact hash is in this passed-hash manifest.",
    )
    parser.add_argument(
        "--cache-record",
        type=Path,
        help="After a passing run, add its test hashes to this manifest; "
        "DIR.updated marks a manifest DIR that learned new hashes.",
    )
    parser.add_argument("--summary", type=Path, help="Append the cache report here.")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if (args.cache_read or args.cache_record) and not args.cache_job:
        parser.error("--cache-read and --cache-record need --cache-job")
    targets = test_targets(args.selection)
    if args.generic_only:
        targets = test_targets(
            json.dumps(
                [
                    target
                    for target in (
                        targets
                        if targets is not None
                        else sorted(
                            path.as_posix() for path in Path("tests").glob("test_*.py")
                        )
                    )
                    if Path(target).name in SINGLE_OS_MODULES
                ]
            )
        )
    current: Fingerprint | None = None
    skipped: list[str] = []
    if args.cache_job and targets != []:
        sources = dict(source.split("=", 1) for source in args.cache_read)
        targets, current, skipped = apply_cache(
            targets,
            args.cache_job,
            {label: Path(path) for label, path in sources.items()},
            args.summary,
        )
        if targets == []:
            print("Every selected test file already passed with these inputs.")
            return
    if targets == []:
        print("No affected tests; skipping pytest.")
        return
    options = args.pytest_args
    if options[:1] == ["--"]:
        options = options[1:]
    if args.platform_only:
        options.append("--ci-skip-independent")
    result = subprocess.run(
        ["pixi", "run", "--no-install", "test-ci", *options, *(targets or [])]
    )
    # A sparse selection may contain no tests in this tier/hash shard.
    passed = result.returncode == 0 or result.returncode == 5 and targets is not None
    if passed and args.cache_record and current is not None:
        ran = targets
        if ran is None:
            ran = test_targets(json.dumps(sorted(current.hashes))) or []
        record_passes(args.cache_record, [*ran, *skipped], current)
    if result.returncode == 5 and targets is not None:
        print("No affected tests in this CI partition.")
        return
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
