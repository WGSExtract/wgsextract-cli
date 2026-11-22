"""Preserve locally edited source in seemingly generated worktree caches."""

import base64
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.test_cleanup_worktrees import (
    cleanup,
    generated_env,
    pull,
    result,
    topic,
    write,
)
from tests.test_cleanup_worktrees import (
    github as github,
)
from tests.test_cleanup_worktrees import (
    proc as proc,
)
from tests.test_cleanup_worktrees import (
    repo as repo,
)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process guards")


@pytest.mark.parametrize(
    "case",
    [
        "conda-edit",
        "conda-unhashed-prefix",
        "wheel-edit",
        "wheel-unhashed",
        "wheel-valid",
    ],
)
def test_1393_never_trims_locally_edited_or_unverifiable_installed_source(
    repo: Path, github: Callable[..., None], case: str
) -> None:
    path = topic(repo)
    env = generated_env(path)
    github([pull(path)])
    installed = env / "bin/demo"
    contents = "experimental edits\n"
    if case.startswith("wheel"):
        packages = env / "lib/python3.13/site-packages"
        info = packages / "demo-1.0.dist-info"
        installed = packages / "demo.py"
        original = b"print('wheel version')\n"

        def digest(value: bytes) -> str:
            return (
                base64.urlsafe_b64encode(hashlib.sha256(value).digest())
                .rstrip(b"=")
                .decode()
            )

        installer_hash = digest(b"uv-pixi\n")
        source_hash = "" if case == "wheel-unhashed" else f"sha256={digest(original)}"
        write(info / "INSTALLER", "uv-pixi\n")
        write(
            info / "RECORD",
            f"demo.py,{source_hash},{len(original)}\n"
            f"demo-1.0.dist-info/INSTALLER,sha256={installer_hash},8\n"
            "demo-1.0.dist-info/RECORD,,\n",
        )
        if case != "wheel-edit":
            contents = original.decode()
    elif case == "conda-unhashed-prefix":
        record = env / "conda-meta/demo-1.0-py_0.json"
        metadata = json.loads(record.read_text())
        del metadata["paths_data"]["paths"][0]["sha256_in_prefix"]
        record.write_text(json.dumps(metadata))
        contents = installed.read_text()
    write(installed, contents)
    write(path / "out/model.bam", "keep sample")

    run = cleanup(repo, "--github", "--trim-caches", "--apply")
    assert run.returncode == 0, run.stderr
    if case == "wheel-valid":
        assert result(repo, path)["reason"] == "cache-pruned"
        assert not installed.exists()
    else:
        assert result(repo, path)["reason"] == "unverified-cache: .pixi/envs"
        assert installed.read_text() == contents


@pytest.mark.parametrize(
    ("relative", "data", "reason"),
    [
        ("not-ignored.py", "new script", "dirty-or-untracked"),
        ("scratch/notes.db", "unknown scratch file", "unknown-data"),
        (".pixi/notes.py", "unique notes outside envs", "unknown-data"),
        ("out/model.bam", "sample data", "unknown-data"),
        ("out/output.log", "x" * (2 * 1024 * 1024), "evidence-too-large"),
        ("out/credentials.json", "private information", "unknown-data"),
        ("out/invalid.log", "header\0binary", "non-text-evidence"),
        ("source.txt", "uncommitted tracked change", "dirty-or-untracked"),
    ],
    ids=[
        "untracked",
        "ignored-source",
        "pixi-notes",
        "genome",
        "large",
        "credential",
        "binary",
        "tracked",
    ],
)
def test_1393_preserves_unknown_or_dirty_data(
    repo: Path, github: Callable[..., None], relative: str, data: str, reason: str
) -> None:
    path = topic(repo)
    write(path / relative, data)
    github([pull(path)])
    run = cleanup(repo, "--apply", "--github")
    assert run.returncode == 0, run.stderr
    assert str(result(repo, path)["reason"]).split(":")[0] == reason
    assert (path / relative).read_text() == data
