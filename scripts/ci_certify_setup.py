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
    check_reference_bytes,
    digest,
    locked_packages,
    validate_environments,
)
from scripts.ci_reference_store import ReferenceStore  # noqa: E402
from wgsextract_cli.core.errors import WGSExtractError  # noqa: E402

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


def download_reference(store: ReferenceStore) -> None:
    from wgsextract_cli.core.atomic_output import atomic_write
    from wgsextract_cli.core.constants import (
        BOOTSTRAP_FILENAME,
        BOOTSTRAP_SHA256,
        BOOTSTRAP_URL,
    )
    from wgsextract_cli.core.microarray.targets import HG19_TO_HG38_CHAIN
    from wgsextract_cli.core.reference_processing import (
        extract_archive_keep_existing,
        normalize_bootstrap_layout,
    )

    root = store.library
    retained = store.version / BOOTSTRAP_FILENAME
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
    installer_config = store.version / "installer.toml"
    atomic_write(installer_config, b"")
    subprocess.run(
        [
            "pixi",
            "run",
            "--no-install",
            "python",
            "-m",
            "wgsextract_cli.main",
            "--config",
            str(installer_config),
            "ref",
            "library",
            "--install",
            "hs38",
            "--ref",
            str(root),
        ],
        check=True,
        timeout=3600,
    )


def prepare_reference(*, download: bool) -> None:
    from wgsextract_cli.core.config import ensure_settings_loaded
    from wgsextract_cli.core.file_lock import exclusive_lock
    from wgsextract_cli.core.ref_library.assets import (
        reference_genomes_dir,
        resolve_reference_library,
    )

    ensure_settings_loaded()
    root = Path(resolve_reference_library())
    if Path(reference_genomes_dir(root)).resolve() != (root / "genomes").resolve():
        raise ValueError(
            "A separate reference_genome_library cannot use certification's pinned library"
        )
    store = ReferenceStore.for_checkout(Path.cwd())
    store.check_location()
    store.check_destination(root)
    if download:
        store.version.mkdir(parents=True, exist_ok=True)
    elif not store.version.is_dir():
        raise ValueError(
            f"Shared reference inputs are missing in {store.version}; run just certify-setup --reference"
        )
    with exclusive_lock(store.version / "setup", timeout=120):
        if not store.verified():
            if store.receipt.exists() or store.receipt.is_symlink():
                raise ValueError(
                    f"Verified inputs changed in {store.version}; "
                    "see the shared reference recovery steps in docs/ci_routing.md"
                )
            if not download:
                raise ValueError(
                    f"Shared reference not prepared in {store.version}; run just certify-setup --reference"
                )
            download_reference(store)
            store.record_verified(
                check_reference_bytes(store.library, store.version, shared=True)
            )
        store.link_checkout(root)
    print(f"Using verified shared reference: {store.library}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-e",
        "--environment",
        action="append",
        dest="environments",
        help="Installed Pixi environment (repeatable); defaults to default and dev",
    )
    reference_mode = parser.add_mutually_exclusive_group()
    reference_mode.add_argument(
        "--reference",
        action="store_true",
        help="Also prepare and link shared reference inputs; downloads missing data",
    )
    reference_mode.add_argument(
        "--link-reference",
        action="store_true",
        help="Only link the shared reference inputs; no downloads",
    )
    reference_mode.add_argument(
        "--link-hs38",
        nargs="+",
        type=Path,
        metavar="FASTA",
        help="Replace explicitly named byte-identical hs38 copies in linked worktrees",
    )
    args = parser.parse_args()
    if args.link_reference:
        prepare_reference(download=False)
        return
    if args.link_hs38:
        store = ReferenceStore.for_checkout(Path.cwd())
        for fasta in args.link_hs38:
            linked = store.link_existing_hs38(fasta)
            print(f"{'Linked' if linked else 'Already linked'}: {fasta}", flush=True)
        return
    environments = list(dict.fromkeys(args.environments or ["default", "dev"]))
    for name in environments:
        prepare_wheels(locked_packages(name))
    validate_environments(environments)
    if args.reference:
        prepare_reference(download=True)
    print("Certification inputs ready; run just certify on the clean pushed PR head.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, WGSExtractError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Certification setup failed: {error}") from error
