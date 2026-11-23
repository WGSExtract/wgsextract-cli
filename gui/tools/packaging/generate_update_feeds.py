#!/usr/bin/env python3
"""Generate GitHub Release-backed update metadata for desktop packages."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import quote

UPDATER_TARGETS = {
    "tauri-windows": ("windows-x86_64", "*-setup.exe"),
    "tauri-linux": ("linux-x86_64", "*.AppImage"),
    "tauri-macos-arm64": ("darwin-aarch64", "*_aarch64.app.tar.gz"),
    "tauri-macos-x64": ("darwin-x86_64", "*_x64.app.tar.gz"),
}


def parse_expected_targets(value: str) -> list[str]:
    try:
        targets = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(
            "expected a JSON list of Tauri build targets"
        ) from exc
    if (
        not isinstance(targets, list)
        or not targets
        or not all(isinstance(target, str) for target in targets)
    ):
        raise argparse.ArgumentTypeError(
            "expected a non-empty JSON list of Tauri build targets"
        )
    unknown = sorted(set(targets) - UPDATER_TARGETS.keys())
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown Tauri build targets: {', '.join(unknown)}"
        )
    if len(targets) != len(set(targets)):
        raise argparse.ArgumentTypeError("duplicate Tauri build targets")
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", required=True, help="GitHub repository in owner/name form"
    )
    parser.add_argument("--tag", required=True, help="GitHub release tag")
    parser.add_argument("--version", required=True, help="SemVer app version")
    parser.add_argument("--artifacts-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--release-base-url", default="")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--expected-targets",
        type=parse_expected_targets,
        help="JSON list of build targets requiring signed artifacts",
    )
    mode.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="Allow partial or no updater feed for development builds",
    )
    args = parser.parse_args()

    try:
        artifacts = collect_artifacts(args.artifacts_root)
        base_url = (
            args.release_base_url
            or f"https://github.com/{args.repo}/releases/download/{args.tag}"
        )
        platforms = collect_updater_platforms(
            artifacts, base_url, args.expected_targets
        )
    except ValueError as exc:
        print(f"Cannot generate updater feed: {exc}", file=sys.stderr)
        return 1
    output_path = args.output_dir / "latest.json"
    if not platforms:
        if output_path.exists():
            print(
                f"No signed updater artifacts; refusing to leave a stale feed at {output_path}. "
                "Remove it or use a clean output directory.",
                file=sys.stderr,
            )
            return 1
        print(
            "No updater feed was generated; signed updater artifacts were not found.",
            file=sys.stderr,
        )
        return 0

    write_tauri_latest_json(output_path, platforms, args.version)
    return 0


def collect_artifacts(root: Path) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    duplicates: dict[str, list[Path]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not is_release_artifact_file(path.name):
            continue
        asset_name = release_asset_name(path.name)
        existing = artifacts.get(asset_name)
        if existing is not None:
            duplicates.setdefault(asset_name, [existing]).append(path)
            continue
        artifacts[asset_name] = path
    if duplicates:
        details = ", ".join(
            f"{name}: {', '.join(str(path.relative_to(root)) for path in paths)}"
            for name, paths in sorted(duplicates.items())
        )
        raise ValueError(f"Duplicate release artifact names are ambiguous: {details}")
    return artifacts


def is_release_artifact_file(name: str) -> bool:
    return name.endswith(
        (
            ".sig",
            ".app.tar.gz",
            ".dmg",
            ".AppImage",
            ".deb",
            ".rpm",
            ".pkg.tar.zst",
            "-setup.exe",
            "-quick-uninstall.ps1",
        )
    )


def collect_updater_platforms(
    artifacts: dict[str, Path],
    base_url: str,
    expected_targets: list[str] | None,
) -> dict[str, dict[str, str]]:
    platforms: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for target in UPDATER_TARGETS if expected_targets is None else expected_targets:
        platform, pattern = UPDATER_TARGETS[target]
        matches = [
            path for name, path in artifacts.items() if fnmatchcase(name, pattern)
        ]
        if len(matches) > 1:
            errors.append(
                f"{platform}: ambiguous updater artifacts: {', '.join(map(str, matches))}"
            )
            continue
        if not matches:
            if expected_targets is not None:
                errors.append(
                    f"{platform}: missing updater artifact matching {pattern}"
                )
            continue

        artifact = matches[0]
        signature_path = artifact.with_name(f"{artifact.name}.sig")
        if artifacts.get(release_asset_name(signature_path.name)) != signature_path:
            if expected_targets is not None:
                errors.append(f"{platform}: missing updater signature {signature_path}")
            continue
        try:
            signature = signature_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            errors.append(
                f"{platform}: cannot read updater signature {signature_path}: {exc}"
            )
            continue
        if not signature:
            if expected_targets is not None:
                errors.append(f"{platform}: empty updater signature {signature_path}")
            continue
        platforms[platform] = {
            "signature": signature,
            "url": f"{base_url}/{quote(release_asset_name(artifact.name))}",
        }
    if errors:
        raise ValueError("\n".join(errors))
    return platforms


def write_tauri_latest_json(
    output_path: Path,
    platforms: dict[str, dict[str, str]],
    version: str,
) -> None:
    payload = {
        "version": version,
        "notes": f"WGS Extract GUI {version}",
        "pub_date": datetime.now(timezone.utc).isoformat(),
        "platforms": platforms,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def release_asset_name(value: str) -> str:
    return value.replace(" ", ".")


if __name__ == "__main__":
    raise SystemExit(main())
