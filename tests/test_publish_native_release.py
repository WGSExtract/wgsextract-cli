"""The shared native-package release step (issue 822) stages, hashes and publishes."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ACTION = Path(__file__).resolve().parents[1] / (
    ".github/actions/publish-native-release/action.yml"
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the step uses GNU find and sha256sum on an Ubuntu runner",
)


def publish_script() -> str:
    lines = ACTION.read_text().splitlines()
    start = lines.index("      run: |") + 1
    body = []
    for line in lines[start:]:
        if line and not line.startswith("        "):
            break
        body.append(line[8:])
    return "\n".join(body) + "\n"


def publish(
    tmp_path: Path, files: dict[str, bytes], **inputs: str
) -> tuple[subprocess.CompletedProcess[str], list[str] | None]:
    download = tmp_path / "out" / "release" / "download"
    for name, content in files.items():
        (download / name).parent.mkdir(parents=True, exist_ok=True)
        (download / name).write_bytes(content)
    # gh is the network boundary: record the release it would create.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GH_ARGS"\n')
    gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GH_ARGS": str(tmp_path / "gh-args"),
        "GITHUB_SHA": "abc123",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "42",
        "TAG": "tools-3",
        "PREFIX": "tools-",
        "TITLE": "Tools tools-3",
        "DESCRIPTION": "Tools as conda packages.\n",
        "SOURCE": "packaging/tools",
        "ASSETS": "*.conda",
        "EXTRA": "",
        "SUMS": "false",
        **inputs,
    }
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", publish_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    args = tmp_path / "gh-args"
    return result, args.read_text().splitlines() if args.exists() else None


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_assets_from_every_artifact_are_hashed_and_published(tmp_path: Path) -> None:
    files = {
        "tools-linux-64/b.conda": b"bee",
        "tools-win-64/a.conda": b"ay",
        "tools-win-64/w.tar.gz": b"weights",
        "tools-win-64/msys2-packages-used.txt": b"msys2",
    }
    result, args = publish(
        tmp_path,
        files,
        ASSETS="*.conda *.tar.gz",
        EXTRA="msys2-packages-used.txt",
        SUMS="true",
    )
    assert result.returncode == 0, result.stderr
    staged = "out/release/assets"
    assert args is not None
    # contract: every asset is uploaded once, as a non-latest prerelease at this commit.
    assert args[:8] == [
        "release",
        "create",
        "tools-3",
        f"{staged}/a.conda",
        f"{staged}/b.conda",
        f"{staged}/w.tar.gz",
        f"{staged}/msys2-packages-used.txt",
        f"{staged}/SHA256SUMS",
    ]
    assert {"--prerelease", "--latest=false", "abc123", "Tools tools-3"} <= set(args)
    notes = (tmp_path / "out/release/notes.md").read_text()
    assert notes.startswith("Tools as conda packages.\nBuilt from packaging/tools")
    assert "https://github.com/owner/repo/actions/runs/42." in notes
    assert f"| a.conda | `{sha(b'ay')}` |" in notes
    assert f"| w.tar.gz | `{sha(b'weights')}` |" in notes
    assert "msys2" not in notes
    sums = (tmp_path / staged / "SHA256SUMS").read_text().splitlines()
    assert sums == [
        f"{sha(b'ay')}  a.conda",
        f"{sha(b'bee')}  b.conda",
        f"{sha(b'weights')}  w.tar.gz",
    ]


@pytest.mark.parametrize(
    ("files", "inputs", "error"),
    [
        ({"x/a.conda": b"a"}, {"TAG": "other-3"}, "Tag must start with tools-"),
        ({"x/a.conda": b"a", "y/a.conda": b"b"}, {}, "Duplicate release asset a.conda"),
        ({"x/a.tar.gz": b"a"}, {}, "No release asset matches *.conda"),
        ({"x/a.conda": b"a"}, {"EXTRA": "msys2-packages-used.txt"}, "No release"),
    ],
)
def test_bad_tags_and_asset_sets_publish_nothing(
    tmp_path: Path, files: dict[str, bytes], inputs: dict[str, str], error: str
) -> None:
    result, args = publish(tmp_path, files, **inputs)
    assert result.returncode != 0
    assert error in result.stderr
    assert args is None
