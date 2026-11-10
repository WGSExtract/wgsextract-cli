#!/usr/bin/env python3
"""Pin pixi.toml's Windows tool packages to a published Windows Tools release.

    python packaging/windows/pin.py windows-tools-2
    pixi lock

Rewrites the default Windows-tools block, the GATK Windows-tools block (GKL) and
the PharmCAT Windows-tools block (bcftools and htslib) with each .conda asset's
URL and GitHub's SHA-256 digest. A GKL-only release leaves the others untouched.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = "WGSExtract/wgsextract-cli"
MANIFEST = Path(__file__).resolve().parents[2] / "pixi.toml"
BLOCK = re.compile(r"(# windows-tools-begin[^\n]*\n).*?(# windows-tools-end)", re.S)
GKL_BLOCK = re.compile(
    r"(# gatk-windows-tools-begin[^\n]*\n).*?(# gatk-windows-tools-end)", re.S
)
PHARMCAT_BLOCK = re.compile(
    r"(# pharmcat-windows-tools-begin[^\n]*\n).*?(# pharmcat-windows-tools-end)", re.S
)
PHARMCAT_TOOLS = ("bcftools-", "htslib-")
# <name>-<version>-<build>.conda; names may contain dashes (bwa-mem2).
ASSET = re.compile(r"^(?P<name>.+)-(?P<version>[^-]+)-(?P<build>[^-]+)\.conda$")


def release_assets(tag: str) -> list[dict[str, str]]:
    url = f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return list(json.load(response)["assets"])


def pin_lines(assets: list[dict[str, str]]) -> list[str]:
    lines = []
    for asset in sorted(assets, key=lambda a: a["name"]):
        match = ASSET.match(asset["name"])
        if not match:
            continue
        digest = asset.get("digest") or ""
        if not digest.startswith("sha256:"):
            raise SystemExit(f"{asset['name']}: GitHub reports no SHA-256 digest")
        lines.append(
            f'{match["name"]} = {{ url = "{asset["browser_download_url"]}", '
            f'sha256 = "{digest.removeprefix("sha256:")}" }}'
        )
    if not lines:
        raise SystemExit("The release has no .conda packages.")
    return lines


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    assets = release_assets(sys.argv[1])
    text = MANIFEST.read_text(encoding="utf-8")
    printed = []
    for block, selected in (
        (BLOCK, [asset for asset in assets if not asset["name"].startswith("gkl-")]),
        (GKL_BLOCK, [asset for asset in assets if asset["name"].startswith("gkl-")]),
        (
            PHARMCAT_BLOCK,
            [asset for asset in assets if asset["name"].startswith(PHARMCAT_TOOLS)],
        ),
    ):
        if not any(ASSET.match(asset["name"]) for asset in selected):
            continue
        lines = pin_lines(selected)
        if not block.search(text):
            raise SystemExit(f"{MANIFEST} has no matching Windows-tools pin block.")
        text = block.sub(
            lambda m, lines=lines: m[1] + "\n".join(lines) + "\n" + m[2], text, count=1
        )
        printed.extend(lines)
    if not printed:
        raise SystemExit("The release has no .conda packages.")
    MANIFEST.write_text(text, encoding="utf-8", newline="\n")
    print("\n".join(printed))
    print("Now run `pixi lock`.")


if __name__ == "__main__":
    main()
