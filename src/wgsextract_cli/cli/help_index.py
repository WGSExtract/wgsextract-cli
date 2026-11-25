"""Build and load the compact discovery index without writing to the package."""

from __future__ import annotations

import argparse
import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

from wgsextract_cli import __version__
from wgsextract_cli.core.paths import windows_local_app_dir

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_INDEX = Path("assets/cli/help-index.json")
PACKAGE_KEY = PACKAGE_INDEX.with_suffix(".key")


@cache
def registry_key(root: Path = PACKAGE_ROOT) -> str:
    """Include parser/registry source in the key even in editable installs."""
    digest = hashlib.sha256()
    files = [
        path
        for folder in ("api", "cli", "commands/_help_catalog", "commands/_messages")
        for path in (root / folder).rglob("*.py")
    ]
    files.extend((root / "commands").glob("*.py"))
    files.extend(
        root / path
        for path in ("core/config.py", "core/messages.py", "core/settings_schema.py")
    )
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return f"{__version__}-{digest.hexdigest()}"


def build_index() -> dict[str, Any]:
    from wgsextract_cli.cli.help import command_index, global_options_json, index_json
    from wgsextract_cli.cli.parser import build_parser
    from wgsextract_cli.core.config import settings_suspended

    with settings_suspended():
        parser, _ = build_parser()
        document: dict[str, Any] = json.loads(
            index_json(command_index(parser), global_options_json(parser), compact=True)
        )
    return document


def index_bytes(key: str, document: dict[str, Any]) -> bytes:
    return (
        json.dumps({"key": key, "index": document}, separators=(",", ":")) + "\n"
    ).encode()


def read_index(path: Path, key: str) -> dict[str, Any] | None:
    from wgsextract_cli.cli.help import HELP_INDEX_SCHEMA

    try:
        record = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("key") != key:
        return None
    document = record.get("index")
    if (
        not isinstance(document, dict)
        or document.get("schema") != HELP_INDEX_SCHEMA
        or not all(
            isinstance(document.get(field), str) for field in ("details", "full")
        )
        or not isinstance(global_options := document.get("global_options"), list)
        or not global_options
        or not all(
            isinstance(option, dict) and isinstance(option.get("flags"), list)
            for option in global_options
        )
        or not isinstance(commands := document.get("commands"), list)
        or not commands
        or not all(
            isinstance(item, dict)
            and all(
                isinstance(item.get(field), str)
                for field in ("path", "summary", "tier")
            )
            and isinstance(flags := item.get("key_flags"), list)
            and all(isinstance(flag, str) for flag in flags)
            for item in commands
        )
        or not isinstance(topics := document.get("topics"), list)
        or not all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("summary"), str)
            for item in topics
        )
    ):
        return None
    return document


def cache_path(key: str) -> Path:
    root = PACKAGE_ROOT.parent.parent
    if (root / "tmp" / ".managed-help-cache").is_file():
        cache_root = root / "tmp" / "cache"
    elif (windows_dir := windows_local_app_dir("wgsextract", "wgsextract")) is not None:
        cache_root = windows_dir / "Cache"
    else:
        from platformdirs import user_cache_dir

        cache_root = Path(user_cache_dir("wgsextract"))
    return cache_root / "cli" / key / "help-index.json"


def load_index() -> dict[str, Any]:
    try:
        bundled_key = (PACKAGE_ROOT / PACKAGE_KEY).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        bundled_key = ""
    # A wheel's metadata and its code are installed together. An editable
    # install from an unpacked sdist must still detect later source edits.
    root = PACKAGE_ROOT.parent.parent
    editable = (root / "pyproject.toml").is_file()
    if not editable and bundled_key.startswith(f"{__version__}-"):
        bundled = read_index(PACKAGE_ROOT / PACKAGE_INDEX, bundled_key)
        if bundled is not None:
            return bundled
    key = registry_key()
    destination = cache_path(key)
    cached = read_index(destination, key)
    if cached is not None:
        return cached
    document = build_index()
    try:
        from wgsextract_cli.core.atomic_output import atomic_write

        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(destination, index_bytes(key, document))
    except OSError:
        pass  # Discovery still works on a host without a writable cache.
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write a build artifact at PATH")
    args = parser.parse_args()
    if args.output is None:
        load_index()
    else:
        from wgsextract_cli.core.atomic_output import atomic_write

        args.output.parent.mkdir(parents=True, exist_ok=True)
        key = registry_key()
        atomic_write(args.output, index_bytes(key, build_index()), new_mode=0o644)
        atomic_write(
            args.output.with_suffix(".key"), (key + "\n").encode(), new_mode=0o644
        )


if __name__ == "__main__":
    main()
