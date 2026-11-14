#!/usr/bin/env python3
"""Re-register all headless review jobs without running the scheduled backup."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["/bin/launchctl", *args], check=check, capture_output=True)


def recover(agents: Path, domain: str) -> None:
    for label in (
        "org.wgsextract.mockups",
        "org.wgsextract.mockups.backup",
        "org.wgsextract.mockups.update",
    ):
        target = domain + "/" + label
        loaded = launchctl("print", target, check=False).returncode == 0
        if not loaded:
            launchctl("bootstrap", domain, str(agents / (label + ".plist")))
        elif label == "org.wgsextract.mockups":
            launchctl("kickstart", "-k", target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agents", type=Path, default=Path.home() / "Library/LaunchAgents"
    )
    parser.add_argument("--domain", default="user/502")
    args = parser.parse_args()
    recover(args.agents, args.domain)
