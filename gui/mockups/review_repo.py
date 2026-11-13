"""Read-only static snapshots, using exactly the Pages preview publisher."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import uuid
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
            r'data-page-id=["\']([a-z0-9-]+)["\']',
            path.read_text("utf-8", errors="replace"),
        )
        if match:
            pages[match.group(1)] = path.relative_to(root).as_posix()
    return pages


class ReviewRepo:
    def __init__(
        self,
        repo: Path,
        cache: Path,
        interval: float,
        local_repo: Path | None = None,
    ) -> None:
        self.repo, self.cache, self.interval = repo, cache, interval
        self.local_repo = local_repo
        self.trigger = cache.parent / "refresh.trigger"
        self.wake = threading.Event()
        self.local_wake = threading.Event()
        self.publish_lock = threading.Lock()
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
        if local_repo:
            threading.Thread(
                target=self.run_local, name="local-preview-refresh", daemon=True
            ).start()

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
                if branch != "main" and not branch.startswith(("mockups/", "local/")):
                    continue
                if branch not in self.roots:
                    self.roots[branch] = path
                    if branch != "main":
                        name = version.get("name", match[2])
                        self.branches.setdefault(
                            ("local/" if branch.startswith("local/") else "") + name,
                            branch,
                        )
            except (OSError, ValueError, KeyError, TypeError):
                logging.exception("cannot recover cached snapshot %s", path.name)

    @staticmethod
    def snapshot_name(branch: str, sha: str, name: str) -> str:
        return sha + "-" + hashlib.sha256(branch.encode()).hexdigest()[:12] + "-" + name

    def snapshot(self, preview: previews.Preview, repo: Path | None = None) -> Path:
        key = self.snapshot_name(preview.branch, preview.sha, preview.name)
        dest: Path = self.cache / key
        if not dest.exists():
            staging = self.cache / (key + "." + uuid.uuid4().hex + ".partial")
            staging.mkdir()
            try:
                previews.copy_tree(preview, staging, repo=repo or self.repo)
                (staging / "shared/version.js").write_text(
                    "window.WGSM_VERSION = "
                    + json.dumps(
                        {
                            "branch": preview.branch,
                            "sha": preview.sha,
                            "date": preview.date,
                            "source": "hosted"
                            if preview.branch == "main"
                            else "local"
                            if preview.branch.startswith("local/")
                            else "preview",
                            "name": preview.name,
                            "local_push": preview.branch.startswith("local/"),
                        }
                    )
                    + ";\n",
                    encoding="utf-8",
                )
                (staging / ".wgsm-snapshot.json").write_text(
                    json.dumps(
                        {
                            "schema": 1,
                            "branch": preview.branch,
                            "sha": preview.sha,
                            "name": preview.name,
                        }
                    ),
                    encoding="utf-8",
                )
                staging.rename(dest)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
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
        branches = previews.fetch("origin", repo=self.repo, limit=None)
        published = []
        for preview in branches:
            try:
                roots[preview.branch] = self.snapshot(preview)
                published.append(preview)
            except (ValueError, subprocess.CalledProcessError):
                logging.exception("skipping invalid preview %s", preview.branch)
        self.publish(roots, {p.name: p.branch for p in published}, local=False)

    def publish(
        self, updated: dict[str, Path], names: dict[str, str], local: bool
    ) -> None:
        with self.publish_lock:
            roots = {
                idea: path
                for idea, path in self.roots.items()
                if idea.startswith("local/") != local
            }
            roots.update(updated)
            branches = {
                name: idea
                for name, idea in self.branches.items()
                if idea.startswith("local/") != local
            }
            branches.update(names)
            rows = []
            for name, idea in branches.items():
                folder = roots[idea]
                rows.append(
                    previews.Preview(
                        idea,
                        "/" + name if idea.startswith("local/") else "/preview/" + name,
                        folder.name.split("-")[0],
                        "local push, not on GitHub"
                        if idea.startswith("local/")
                        else "",
                    )
                )
            (self.cache / "preview-index.html").write_text(
                previews.index_page(rows).replace(
                    "Notes made\n    on a preview stay in your browser: export them from the Notes panel.",
                    'Notes are shared on this server. <a href="/notes.html">All notes</a>.',
                ),
                encoding="utf-8",
            )
            manifest = self.cache / "current.json.partial"
            manifest.write_text(
                json.dumps(
                    {
                        "roots": {idea: path.name for idea, path in roots.items()},
                        "branches": branches,
                    }
                ),
                encoding="utf-8",
            )
            manifest.replace(self.cache / "current.json")
            self.roots, self.branches = roots, branches
            # Keep the active and previous snapshots: in-flight requests may still
            # be reading the old tree. Older commits are no longer reachable URLs.
            active = set(roots.values()) | self.previous
            self.previous = set(roots.values())
            for path in self.cache.iterdir():
                if (
                    path in active
                    or not path.is_dir()
                    or path.is_symlink()
                    or path.name.endswith(".partial")
                ):
                    continue
                marker = path / ".wgsm-snapshot.json"
                if not marker.is_file():
                    continue
                try:
                    owned = json.loads(marker.read_text("utf-8"))
                    if (
                        not isinstance(owned, dict)
                        or owned.get("schema") != 1
                        or not all(
                            isinstance(owned.get(field), str)
                            for field in ("branch", "sha", "name")
                        )
                    ):
                        continue
                    branch = owned["branch"]
                    if (
                        self.snapshot_name(branch, owned["sha"], owned["name"])
                        == path.name
                        and branch.startswith("local/") == local
                    ):
                        shutil.rmtree(path)
                except (OSError, ValueError, KeyError, TypeError):
                    logging.exception("cannot prune snapshot %s", path)

    def request_refresh(self) -> None:
        self.wake.set()
        self.local_wake.set()

    def refresh_local(self) -> None:
        assert self.local_repo is not None
        roots, names = {}, {}
        for preview in previews.discover(
            "refs/heads/", repo=self.local_repo, limit=None
        ):
            preview.branch = "local/" + preview.branch.removeprefix(previews.PREFIX)
            try:
                roots[preview.branch] = self.snapshot(preview, repo=self.local_repo)
                names["local/" + preview.name] = preview.branch
            except (ValueError, subprocess.CalledProcessError):
                logging.exception("skipping invalid local preview %s", preview.branch)
        self.publish(roots, names, local=True)

    def run_local(self) -> None:
        stamp = None
        first = True
        while not self.stop.is_set():
            try:
                current = (
                    self.trigger.stat().st_mtime_ns if self.trigger.exists() else None
                )
                if first or self.local_wake.is_set() or current != stamp:
                    self.local_wake.clear()
                    self.refresh_local()
                    stamp, first = current, False
            except Exception:
                logging.exception("local preview refresh failed")
            self.local_wake.wait(1)

    def run(self) -> None:
        delay = 0.0
        while not self.stop.is_set():
            self.wake.wait(delay)
            self.wake.clear()
            if self.stop.is_set():
                break
            try:
                self.refresh()
                delay = self.interval
            except Exception:
                logging.exception(
                    "preview refresh failed; retaining last good snapshots"
                )
                delay = min(self.interval, 15)
