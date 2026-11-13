"""Read-only static snapshots, using exactly the Pages preview publisher."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_mockup_previews as previews  # noqa: E402

__all__ = ["ReviewRepo", "previews"]


@lru_cache(maxsize=64)
def page_paths(root: Path) -> dict[str, str]:
    pages = {}
    for path in root.rglob("*.html"):
        match = re.search(
            r'data-page-id=["\']([a-z0-9-]+)["\']', path.read_text("utf-8")
        )
        if match:
            pages[match.group(1)] = path.relative_to(root).as_posix()
    return pages


class ReviewRepo:
    def __init__(self, repo: Path, cache: Path, interval: float) -> None:
        self.repo, self.cache, self.interval = repo, cache, interval
        self.roots: dict[str, Path] = {}
        self.branches: dict[str, str] = {}
        self.stop = threading.Event()
        cache.mkdir(parents=True, exist_ok=True)
        self.refresh()
        self.thread = threading.Thread(
            target=self.run, name="preview-refresh", daemon=True
        )
        self.thread.start()

    def snapshot(self, preview: previews.Preview) -> Path:
        dest = self.cache / (preview.sha + "-" + preview.name)
        if not dest.exists():
            staging = self.cache / (preview.sha + "-" + preview.name + ".partial")
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            previews.copy_tree(preview, staging, repo=self.repo)
            if preview.branch == "main":
                (staging / "shared/version.js").write_text(
                    "window.WGSM_VERSION = "
                    + json.dumps(
                        {"branch": "main", "sha": preview.sha, "source": "hosted"}
                    )
                    + ";\n",
                    encoding="utf-8",
                )
            staging.rename(dest)
        return dest

    def refresh(self) -> None:
        previews.git(
            "fetch",
            "--quiet",
            "--no-tags",
            "--depth=1",
            "origin",
            "+refs/heads/main:refs/review/main",
            repo=self.repo,
        )
        sha = (
            previews.git("rev-parse", "refs/review/main", repo=self.repo)
            .decode()
            .strip()
        )
        roots = {"main": self.snapshot(previews.Preview("main", "main", sha, ""))}
        branches = previews.fetch("origin", repo=self.repo)
        published = []
        for preview in branches:
            try:
                roots[preview.branch] = self.snapshot(preview)
                published.append(preview)
            except (ValueError, subprocess.CalledProcessError):
                logging.exception("skipping invalid preview %s", preview.branch)
        index = self.cache / "preview-index.html"
        index.write_text(
            previews.index_page(published).replace(
                "Notes made\n    on a preview stay in your browser: export them from the Notes panel.",
                'Notes are shared on this server. <a href="/notes.html">All notes</a>.',
            ),
            encoding="utf-8",
        )
        self.roots = roots
        self.branches = {p.name: p.branch for p in published}
        # Keep the active and previous snapshots: in-flight requests may still
        # be reading the old tree. Older commits are no longer reachable URLs.
        active = set(roots.values()) | getattr(self, "previous", set())
        self.previous = set(roots.values())
        for path in self.cache.iterdir():
            if path not in active and path.is_dir():
                shutil.rmtree(path)

    def run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                self.refresh()
            except Exception:
                logging.exception(
                    "preview refresh failed; retaining last good snapshots"
                )
