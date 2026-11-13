#!/usr/bin/env python3
"""Install per-user launchd jobs; never requires sudo or a GUI login."""

from __future__ import annotations

import argparse
import plistlib
import subprocess
from pathlib import Path
from typing import Any

from local_repo import install as install_local_repo


def install(root: Path, agents: Path, domain: str) -> None:
    agents.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    install_local_repo(root)
    template = Path(__file__).with_name("server.plist.template").read_text("utf-8")
    server = plistlib.loads(template.replace("@ROOT@", str(root)).encode())
    jobs = [server]
    for task, schedule in (
        ("backup", {"StartCalendarInterval": {"Hour": 3, "Minute": 15}}),
        ("update", {"StartInterval": 300, "RunAtLoad": True}),
    ):
        args = [
            "/usr/bin/python3",
            str(root / "app/gui/mockups/hosting" / (task + ".py")),
            "--root",
            str(root),
        ]
        if task == "update":
            args.extend(["--domain", domain])
        job: dict[str, Any] = {
            "Label": "org.wgsextract.mockups." + task,
            "LimitLoadToSessionType": "Background",
            "ProgramArguments": args,
            "WorkingDirectory": str(root),
            "LowPriorityIO": True,
            "Nice": 10,
            "StandardOutPath": str(root / "logs" / (task + ".log")),
            "StandardErrorPath": str(root / "logs" / (task + "-error.log")),
            **schedule,
        }
        jobs.append(job)
    for job in jobs:
        path = agents / (job["Label"] + ".plist")
        # A first install can have no job to boot out.
        subprocess.run(
            ["/bin/launchctl", "bootout", domain + "/" + job["Label"]],
            check=False,
            capture_output=True,
        )
        path.write_bytes(plistlib.dumps(job))
        subprocess.run(["/bin/launchctl", "bootstrap", domain, str(path)], check=True)
        print("installed", path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/Users/wgse/mockup-server"))
    parser.add_argument(
        "--agents", type=Path, default=Path.home() / "Library/LaunchAgents"
    )
    parser.add_argument("--domain", default="user/502")
    args = parser.parse_args()
    install(args.root, args.agents, args.domain)
