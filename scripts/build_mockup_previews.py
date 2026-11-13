"""Build GitHub Pages previews of the UI mockups on `mockups/<name>` branches.

Run by the Pages workflow on main (never by a branch's own code):

    python3 scripts/build_mockup_previews.py --out site/app/mockups/preview

It fetches every `mockups/*` branch of the remote (this repository only, so
only people with write access can publish a preview), and copies only the
static files under gui/mockups/ of each branch to <out>/<name>/. Nothing from
a branch is executed: files are read from `git archive` output, symlinks and
anything that isn't a plain static file are skipped. Each preview gets a
version.js naming its branch and commit, and <out>/index.html lists them.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PREFIX = "mockups/"
NAMESPACE = "refs/mockup-previews/"
SOURCE = "gui/mockups"
STATIC = {".html", ".css", ".js", ".json", ".svg", ".png", ".webp", ".md", ".txt"}
MAX_PREVIEWS = 20
MAX_FILES = 600
MAX_BYTES = 25 * 1024 * 1024


@dataclass
class Preview:
    branch: str
    name: str
    sha: str
    date: str
    files: int = 0


def git(*args: str, repo: Path | None = None) -> bytes:
    command = ["git"]
    if repo is not None:
        command.extend(["--git-dir", str(repo.resolve())])
    return subprocess.run(
        [*command, *args], check=True, capture_output=True, timeout=120
    ).stdout


def slug(branch: str) -> str:
    """The URL folder for a branch: mockups/Randy's idea -> randy-s-idea."""
    name = re.sub(r"[^a-z0-9-]+", "-", branch.removeprefix(PREFIX).lower())
    return re.sub(r"-{2,}", "-", name).strip("-")[:40].strip("-")


def fetch(remote: str, repo: Path | None = None) -> list[Preview]:
    """The remote's mockups/* branches, newest commit first."""
    for ref in (
        git("for-each-ref", "--format=%(refname)", NAMESPACE, repo=repo)
        .decode()
        .split()
    ):
        git("update-ref", "-d", ref, repo=repo)
    # A shallow fetch whose refspec matches no branch fails, so look first:
    # having no mockups/* branches is the normal state between iterations.
    if not git(
        "ls-remote", "--heads", remote, f"refs/heads/{PREFIX}*", repo=repo
    ).strip():
        return []
    git(
        "fetch",
        "--quiet",
        "--no-tags",
        "--depth=1",
        remote,
        f"+refs/heads/{PREFIX}*:{NAMESPACE}*",
        repo=repo,
    )
    out = git(
        "for-each-ref",
        "--sort=-committerdate",
        "--format=%(refname)%00%(objectname)%00%(committerdate:iso-strict)",
        NAMESPACE,
        repo=repo,
    ).decode()
    previews: list[Preview] = []
    taken: set[str] = set()
    for line in out.splitlines():
        ref, sha, date = line.split("\0")
        branch = PREFIX + ref.removeprefix(NAMESPACE)
        name = slug(branch)
        if not name or name == "index":
            print(f"skip {branch}: no usable folder name", file=sys.stderr)
            continue
        base, n = name, 2
        while name in taken:
            name, n = f"{base}-{n}", n + 1
        taken.add(name)
        previews.append(Preview(branch, name, sha, date))
    return previews[:MAX_PREVIEWS]


def static_member(member: tarfile.TarInfo) -> PurePosixPath | None:
    """The path below gui/mockups/ of a file worth publishing, else None."""
    if not member.isfile():
        return None
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("gui", "mockups"):
        return None
    rel = PurePosixPath(*path.parts[2:])
    if "__pycache__" in rel.parts or any(part.startswith(".") for part in rel.parts):
        return None
    if rel.suffix not in STATIC and rel.name != "LICENSE":
        return None
    if rel == PurePosixPath("feedback/_pages.json") or rel == PurePosixPath(
        "shared/version.js"
    ):
        return None
    return rel


def copy_tree(preview: Preview, dest: Path, repo: Path | None = None) -> None:
    archive = git("archive", "--format=tar", preview.sha, "--", SOURCE, repo=repo)
    total = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            rel = static_member(member)
            if rel is None:
                continue
            total += member.size
            preview.files += 1
            if preview.files > MAX_FILES or total > MAX_BYTES:
                raise ValueError(
                    f"{preview.branch}: more than {MAX_FILES} files or {MAX_BYTES} bytes"
                )
            source = tar.extractfile(member)
            if source is None:
                continue
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
    version = {
        "branch": preview.branch,
        "date": preview.date,
        "sha": preview.sha,
        "source": "preview",
    }
    (dest / "shared").mkdir(exist_ok=True)
    (dest / "shared" / "version.js").write_text(
        f"window.WGSM_VERSION = {json.dumps(version, sort_keys=True)};\n",
        encoding="utf-8",
    )
    feedback = dest / "feedback"
    feedback.mkdir(exist_ok=True)
    pages = sorted(
        p.stem for p in feedback.glob("*.json") if not p.stem.startswith("_")
    )
    (feedback / "_pages.json").write_text(json.dumps(pages), encoding="utf-8")


def index_page(previews: list[Preview]) -> str:
    rows = "\n".join(
        f'      <li class="card"><a href="{p.name}/index.html"><strong>{html.escape(p.branch)}</strong></a>'
        f'<span class="small muted">commit <code>{p.sha[:7]}</code> · {html.escape(p.date)}</span></li>'
        for p in previews
    )
    body = (
        rows
        or '      <li class="muted">No <code>mockups/*</code> branches right now.</li>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mockup branch previews · WGS Extract</title>
<link rel="stylesheet" href="../design-system/wgse.css">
<link rel="stylesheet" href="../shared/docs.css">
</head>
<body>
<main class="doc">
  <header class="doc__hero">
    <span class="eyebrow">WGS Extract mockups</span>
    <h1>Branch previews</h1>
    <p>Each <code>mockups/&lt;name&gt;</code> branch of the repository is published here
    a few minutes after a push, and removed when the branch is deleted. Notes made
    on a preview stay in your browser: export them from the Notes panel. The
    mockups on <code>main</code> are at <a href="../index.html">the main mockups page</a>.</p>
  </header>
  <ul class="stack" role="list">
{body}
  </ul>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out", type=Path, required=True, help="folder to write the previews to"
    )
    parser.add_argument(
        "--remote", default="origin", help="git remote or URL to fetch (default origin)"
    )
    args = parser.parse_args()
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    previews = []
    for preview in fetch(args.remote):
        dest = args.out / preview.name
        try:
            copy_tree(preview, dest)
        except (ValueError, subprocess.CalledProcessError) as err:
            shutil.rmtree(dest, ignore_errors=True)
            print(f"skip {preview.branch}: {err}", file=sys.stderr)
            continue
        print(
            f"{preview.branch} -> {preview.name}/ ({preview.files} files, {preview.sha[:7]})"
        )
        previews.append(preview)
    (args.out / "index.html").write_text(index_page(previews), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
