"""Read-only checks for the installed tools and reference inputs CI prepares."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_reference_store import ReferenceStore, fingerprint, hs38_pin  # noqa: E402
from scripts.reference_store_paths import is_store_library  # noqa: E402


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return digest_stream(stream)


def digest_stream(stream: IO[bytes]) -> str:
    value = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(chunk)
    return value.hexdigest()


def check_wheel(
    prefix: Path, distribution: importlib.metadata.Distribution, package: dict[str, Any]
) -> None:
    expected = package.get("sha256")
    if not expected or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError(
            f"{package['name']}: pinned wheel SHA-256 is missing from pixi list; "
            f"use CI's Pixi version and run just certify-setup -e {prefix.name}"
        )
    wheel = Path("out/certify/inputs/wheels") / f"{expected}.whl"
    if not wheel.is_file() or digest(wheel) != expected:
        raise ValueError(
            f"{package['name']}: retain the pinned wheel at {wheel} before certification; "
            f"missing or corrupt wheel archive; run just certify-setup -e {prefix.name}"
        )
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            path = Path(member.filename)
            if member.is_dir():
                continue
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Invalid pinned wheel member")
            if path.name in {"RECORD", "INSTALLER"} and path.parent.name.endswith(
                ".dist-info"
            ):
                continue
            if path.parts[0].endswith(".data"):
                kind = path.parts[1]
                relative = Path(*path.parts[2:])
                if kind in {"purelib", "platlib"}:
                    target = Path(str(distribution.locate_file(str(relative))))
                elif kind == "scripts":
                    target = (
                        prefix
                        / ("Scripts" if sys.platform == "win32" else "bin")
                        / relative
                    )
                elif kind == "data":
                    target = prefix / relative
                else:
                    raise ValueError(
                        f"Unprovable pinned wheel installation scheme: {kind}"
                    )
            else:
                target = Path(str(distribution.locate_file(member.filename)))
            if not target.is_file():
                raise ValueError(
                    f"{package['name']}: pinned wheel file missing: {member.filename}"
                )
            with archive.open(member) as source:
                expected_bytes = source.read()
            installed_bytes = target.read_bytes()
            if expected_bytes.startswith((b"#!python\n", b"#!pythonw\n")):
                expected_bytes = expected_bytes.partition(b"\n")[2]
                installed_bytes = installed_bytes.partition(b"\n")[2]
            if expected_bytes != installed_bytes:
                raise ValueError(
                    f"{package['name']}: installed file differs from pinned wheel: {member.filename}"
                )


def check_packages(prefix: Path, packages: list[dict[str, Any]]) -> None:
    """Compare installed records with Pixi's locked package identities."""
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (prefix / "conda-meta").glob("*.json")
    ]
    installed = {record["name"]: record for record in records}
    sites = [prefix / "Lib/site-packages", *prefix.glob("lib/python*/site-packages")]

    def normalize(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    distributions = {
        normalize(dist.metadata["Name"]): dist
        for dist in importlib.metadata.distributions(path=[str(site) for site in sites])
    }
    for package in packages:
        name = package["name"]
        if package["kind"] == "conda":
            record = installed.get(name, {})
            if any(
                package.get(key) != record.get(key)
                for key in ("version", "build", "url", "sha256")
            ) or not package.get("sha256"):
                raise ValueError(f"{prefix.name}: {name} differs from pixi.lock")
        else:
            dist = distributions.get(normalize(name))
            if dist is None:
                raise ValueError(f"{prefix.name}: missing locked Python package {name}")
            if package.get("version") is None:
                direct = json.loads(dist.read_text("direct_url.json") or "{}")
                if (
                    not direct.get("dir_info", {}).get("editable")
                    or direct.get("url") != Path.cwd().as_uri()
                ):
                    raise ValueError(f"{name}: editable checkout provenance missing")
                continue
            if dist.version != package["version"]:
                raise ValueError(f"{prefix.name}: stale Python package {name}")
            check_wheel(prefix, dist, package)
            record_text = dist.read_text("RECORD")
            if not record_text:
                raise ValueError(f"{name}: installed wheel integrity record missing")
            for file, encoded, _size in csv.reader(record_text.splitlines()):
                if not encoded:
                    continue
                algorithm, value = encoded.split("=", 1)
                if algorithm != "sha256":
                    raise ValueError(f"{name}: unsupported installed wheel digest")
                expected = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
                installed_file = Path(str(dist.locate_file(file)))
                if (
                    not installed_file.is_file()
                    or bytes.fromhex(digest(installed_file)) != expected
                ):
                    raise ValueError(f"{name}: installed wheel file changed: {file}")


def locked_packages(name: str) -> list[dict[str, Any]]:
    prefix = Path(".pixi/envs") / name
    if not (prefix / "conda-meta/pixi").is_file():
        raise ValueError(
            f"Missing CI environment: {prefix}; run pixi install --locked -e {name}, "
            f"then just certify-setup -e {name}"
        )
    packages: list[dict[str, Any]] = json.loads(
        subprocess.check_output(
            ["pixi", "list", "--locked", "--json", "-e", name], text=True
        )
    )
    return packages


def validate_environments(environments: list[str]) -> None:
    for name in environments:
        prefix = Path(".pixi/envs") / name
        packages = locked_packages(name)
        check_packages(prefix, packages)
        print(f"Verified locked installed packages: {name}", flush=True)


def validate_bootstrap(
    root: Path,
    archive_name: str,
    expected: str,
    *,
    digest_file: Callable[[Path], str] = digest,
) -> None:
    archive = root / archive_name
    if not archive.is_file() or digest_file(archive) != expected:
        raise ValueError(
            f"Prepared reference bootstrap archive is missing or not pinned: {archive}; "
            "run just certify-setup --reference"
        )
    with tarfile.open(archive, "r:gz") as stream:
        checked = 0
        for member in stream:
            if not member.isfile():
                continue
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Invalid reference bootstrap member")
            if "__MACOSX" in relative.parts or relative.name.startswith("._"):
                continue
            if relative.parts[0] == "reference":
                relative = Path(*relative.parts[1:])
            if relative.parts[0] == "genomes" and relative.suffix in {
                ".dict",
                ".fai",
                ".gzi",
            }:
                # CI regenerates these from its pinned FASTA; dictionary UR
                # fields contain machine-specific absolute paths.
                continue
            source = stream.extractfile(member)
            if source is None:
                raise ValueError("Unreadable reference bootstrap member")
            destination = root / relative
            expected_digest = digest_stream(source)
            if not destination.is_file() or digest_file(destination) != expected_digest:
                raise ValueError(
                    f"Prepared reference input differs from bootstrap: {destination}; "
                    "move the conflicting file aside, then run just certify-setup --reference"
                )
            checked += 1
    if not checked:
        raise ValueError("Reference bootstrap contains no files")


def check_reference_bytes(
    root: Path, retained: Path, *, shared: bool = False, genomes: Path | None = None
) -> dict[Path, list[int]]:
    from wgsextract_cli.core.constants import BOOTSTRAP_FILENAME, BOOTSTRAP_SHA256
    from wgsextract_cli.core.microarray.targets import HG19_TO_HG38_CHAIN

    files: dict[Path, list[int]] = {}

    def capture(path: Path) -> None:
        current = fingerprint(path, retained)
        if files.setdefault(path, current) != current:
            raise ValueError(f"Reference input changed during verification: {path}")

    def digest_file(path: Path) -> str:
        if shared:
            capture(path)
        value = digest(path)
        if shared:
            capture(path)
        return value

    name, expected = hs38_pin()
    genomes = genomes if genomes is not None else root / "genomes"
    if shared:
        for suffix in ("fai", "gzi"):
            index = genomes / f"{name}.{suffix}"
            if not index.is_file() or not index.stat().st_size:
                raise ValueError(
                    f"Shared pinned hs38 FASTA index is missing or empty: {index}"
                )
            capture(index)
    validate_bootstrap(
        root,
        str(retained / BOOTSTRAP_FILENAME),
        BOOTSTRAP_SHA256,
        digest_file=digest_file,
    )
    chain = root / HG19_TO_HG38_CHAIN.name
    if not chain.is_file() or digest_file(chain) != HG19_TO_HG38_CHAIN.sha256:
        raise ValueError(
            f"Prepared pinned hg19-to-hg38 chain is missing or corrupt: {chain}; "
            "run just certify-setup --reference"
        )
    candidates = [genomes / name] if shared else genomes.rglob(name)
    fasta = next(
        (
            path
            for path in candidates
            if path.is_file() and digest_file(path) == expected
        ),
        None,
    )
    if fasta is None:
        raise ValueError(
            f"Prepared pinned hs38 reference is missing or corrupt in {genomes}; "
            f"move any existing {name} FASTA and its indexes aside, "
            "then run just certify-setup --reference to obtain the pinned copy"
        )
    return files


def validate_reference() -> None:
    from wgsextract_cli.core.config import ensure_settings_loaded
    from wgsextract_cli.core.ref_library.assets import (
        reference_genomes_dir,
        resolve_reference_library,
    )

    ensure_settings_loaded()
    store = ReferenceStore.for_checkout(Path.cwd())
    root = Path(resolve_reference_library())
    resolved = root.resolve()
    genomes = Path(reference_genomes_dir(root))
    if is_store_library(resolved, store.store):
        if (
            resolved != store.library
            or genomes.resolve() != store.library / "genomes"
            or not store.verified()
        ):
            raise ValueError(
                f"Shared certification reference is unverified or out of date: {root}; "
                "see the shared reference recovery steps in docs/ci_routing.md"
            )
    else:
        check_reference_bytes(
            root, Path("out/certify/inputs").absolute(), genomes=genomes
        )


if __name__ == "__main__":
    if sys.argv[1:] == ["--reference"]:
        validate_reference()
