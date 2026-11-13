#!/usr/bin/env python3
"""Prepare the SSH push target and trusted refresh notification hook."""

from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path


def install(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "local.git"
    if not repo.exists():
        subprocess.run(["git", "init", "--bare", str(repo)], check=True)
    hooks = repo / "hooks"
    hooks.mkdir(exist_ok=True)
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(repo),
            "config",
            "core.hooksPath",
            str(hooks.resolve()),
        ],
        check=True,
    )
    subprocess.run(
        ["git", "--git-dir", str(repo), "config", "receive.shallowUpdate", "true"],
        check=True,
    )
    hook = hooks / "post-receive"
    hook.write_text(
        "#!/bin/sh\nset -eu\ncat >/dev/null\n/usr/bin/touch "
        + shlex.quote(str((root / "refresh.trigger").resolve()))
        + "\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    return repo


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/Users/wgse/mockup-server"))
    print(install(parser.parse_args().root))
