"""Command-line options for the standalone review server."""

from __future__ import annotations

import argparse
from pathlib import Path


def make_parser(description: str, feedback: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--port", type=int, help="port (default: first free from 8765; 0 picks any)"
    )
    parser.add_argument("--host", default="127.0.0.1", help="listening address")
    parser.add_argument(
        "--open", action="store_true", help="open the index in the browser"
    )
    parser.add_argument(
        "--check", action="store_true", help="validate and tidy feedback, then exit"
    )
    parser.add_argument("--db", type=Path, help="shared SQLite notes database")
    parser.add_argument(
        "--repo", type=Path, help="read-only bare clone for main and previews"
    )
    parser.add_argument(
        "--local-repo", type=Path, help="bare repository for SSH-pushed previews"
    )
    parser.add_argument(
        "--cache", type=Path, help="static snapshots (default: beside --db)"
    )
    parser.add_argument("--refresh-minutes", type=float, default=5)
    parser.add_argument(
        "--allow-host", action="append", default=[], help="allowed host:port"
    )
    parser.add_argument("--feedback-dir", type=Path, default=feedback)
    parser.add_argument(
        "--import-feedback",
        type=Path,
        help="import per-page files into main, then exit",
    )
    parser.add_argument(
        "--export-feedback",
        type=Path,
        help="write per-idea repo-format files, then exit",
    )
    return parser
