"""Retain hash-verified CI inputs for already installed certification environments."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_certify_inputs import (  # noqa: E402
    digest,
    locked_packages,
    validate_environments,
    validate_reference,
)

INPUTS = Path("out/certify/inputs")


def fetch_verified(url: str, destination: Path, expected: str) -> None:
    """Publish only complete, verified downloads; never trust a cached filename."""
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError(f"No pinned SHA-256 for {destination}")
    if destination.is_file() and digest(destination) == expected:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        with (
            urllib.request.urlopen(url, timeout=120) as source,
            partial.open("wb") as out,
        ):
            shutil.copyfileobj(source, out)
        if digest(partial) != expected:
            raise ValueError(f"SHA-256 mismatch for {destination}; download rejected")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def prepare_wheels(packages: list[dict[str, Any]]) -> None:
    for package in packages:
        if package["kind"] != "pypi" or package.get("version") is None:
            continue
        missing = [field for field in ("url", "sha256") if not package.get(field)]
        if missing:
            raise ValueError(
                f"{package['name']}: pixi list --locked --json is missing "
                f"{', '.join(missing)}; use CI's Pixi version pinned in "
                ".github/actions/setup-pixi/action.yml, then rerun just certify-setup"
            )
        url = package["url"]
        checksum = package["sha256"]
        if not url.endswith(".whl"):
            raise ValueError(
                f"{package['name']}: locked Python artifact is not a wheel"
            )
        destination = INPUTS / "wheels" / f"{checksum}.whl"
        fetch_verified(url, destination, checksum)
        print(f"Verified wheel: {package['name']}", flush=True)


def prepare_reference() -> None:
    from wgsextract_cli.core.constants import (
        BOOTSTRAP_FILENAME,
        BOOTSTRAP_SHA256,
        BOOTSTRAP_URL,
    )
    from wgsextract_cli.core.microarray.targets import HG19_TO_HG38_CHAIN
    from wgsextract_cli.core.ref_library.assets import resolve_reference_library
    from wgsextract_cli.core.reference_processing import (
        extract_archive_keep_existing,
        normalize_bootstrap_layout,
    )

    root = Path(resolve_reference_library())
    retained = INPUTS / BOOTSTRAP_FILENAME
    fetch_verified(BOOTSTRAP_URL, retained, BOOTSTRAP_SHA256)
    root.mkdir(parents=True, exist_ok=True)
    extract_archive_keep_existing(str(retained), str(root))
    normalize_bootstrap_layout(str(root))
    chain = HG19_TO_HG38_CHAIN
    for index, url in enumerate(chain.urls):
        try:
            fetch_verified(url, root / chain.name, chain.sha256)
            break
        except (OSError, ValueError):
            if index == len(chain.urls) - 1:
                raise
    subprocess.run(
        [
            "pixi",
            "run",
            "--no-install",
            "python",
            "-m",
            "wgsextract_cli.main",
            "ref",
            "library",
            "--install",
            "hs38",
            "--ref",
            str(root),
        ],
        check=True,
    )
    validate_reference()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-e",
        "--environment",
        action="append",
        dest="environments",
        help="Installed Pixi environment (repeatable); defaults to default and dev",
    )
    parser.add_argument(
        "--reference",
        action="store_true",
        help="Also prepare CI's bootstrap, liftOver chain and full hs38 genome",
    )
    args = parser.parse_args()
    environments = list(dict.fromkeys(args.environments or ["default", "dev"]))
    for name in environments:
        prepare_wheels(locked_packages(name))
    validate_environments(environments)
    if args.reference:
        prepare_reference()
    print("Certification inputs ready; run just certify on the clean pushed PR head.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Certification setup failed: {error}") from error
