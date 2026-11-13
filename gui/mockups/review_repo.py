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
        self.restore()
        self.previous: set[Path] = set(self.roots.values())
        self.thread = threading.Thread(
            target=self.run, name="preview-refresh", daemon=True
        )
        self.thread.start()

    def restore(self) -> None:
        """Recover the last published mapping without any network access."""
        try:
            manifest = json.loads((self.cache / "current.json").read_text("utf-8"))
            if not isinstance(manifest.get("roots"), dict) or not isinstance(
                manifest.get("branches"), dict
            ):
                raise ValueError("invalid snapshot mapping")
            roots = {
                idea: self.cache / folder for idea, folder in manifest["roots"].items()
            }
            if any(
                path.parent != self.cache or not path.is_dir()
                for path in roots.values()
            ):
                raise ValueError("invalid or missing snapshot directory")
            self.roots = roots
            self.branches = manifest["branches"]
            return
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            logging.exception(
                "cannot read snapshot manifest; scanning completed snapshots"
            )
        # Also recover completed snapshots when the first manifest has not yet
        # been written, or an interrupted write left it unavailable.
        candidates = sorted(
            (
                path
                for path in self.cache.iterdir()
                if path.is_dir() and not path.is_symlink()
            ),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for path in candidates:
            match = re.fullmatch(r"([0-9a-f]{40,64})-([a-z0-9-]+)", path.name)
            if not match:
                continue
            try:
                text = (path / "shared/version.js").read_text("utf-8")
                version = json.loads(
                    text.removeprefix("window.WGSM_VERSION = ")
                    .strip()
                    .removesuffix(";")
                )
                branch = version["branch"]
                if version["sha"] != match[1] or not isinstance(branch, str):
                    continue
                if branch != "main" and not branch.startswith("mockups/"):
                    continue
                if branch not in self.roots:
                    self.roots[branch] = path
                    if branch != "main":
                        self.branches.setdefault(match[2], branch)
            except (OSError, ValueError, KeyError, TypeError):
                logging.exception("cannot recover cached snapshot %s", path.name)

    def snapshot(self, preview: previews.Preview) -> Path:
        dest: Path = self.cache / (preview.sha + "-" + preview.name)
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
        names = {p.name: p.branch for p in published}
        manifest = self.cache / "current.json.partial"
        manifest.write_text(
            json.dumps(
                {
                    "roots": {idea: path.name for idea, path in roots.items()},
                    "branches": names,
                }
            ),
            encoding="utf-8",
        )
        manifest.replace(self.cache / "current.json")
        self.roots, self.branches = roots, names
        # Keep the active and previous snapshots: in-flight requests may still
        # be reading the old tree. Older commits are no longer reachable URLs.
        active = set(roots.values()) | getattr(self, "previous", set())
        self.previous = set(roots.values())
        for path in self.cache.iterdir():
            if path not in active and path.is_dir():
                shutil.rmtree(path)

    def run(self) -> None:
        delay = 0.0
        while not self.stop.wait(delay):
            try:
                self.refresh()
                delay = self.interval
            except Exception:
                logging.exception(
                    "preview refresh failed; retaining last good snapshots"
                )
                delay = min(self.interval, 15)
