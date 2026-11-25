#!/usr/bin/env python3
"""Build the wheel, install it into a temporary venv, and smoke-test the CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "out" / "package-smoke"
DIST_DIR = OUT_DIR / "dist"
VENV_DIR = OUT_DIR / "venv"
OPENAPI_ASSET = "wgsextract_cli/assets/schemas/wgsextract.server.openapi.json"


def _run(command: list[str], *, quiet: bool = False) -> None:
    print("+", " ".join(command))
    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL if quiet else None,
    )


def _venv_python() -> Path:
    candidates = (
        VENV_DIR / "Scripts" / "python.exe",
        VENV_DIR / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No Python executable found in {VENV_DIR}")


def _wgsextract_launcher() -> Path:
    candidates = (
        VENV_DIR / "Scripts" / "wgsextract.exe",
        VENV_DIR / "Scripts" / "wgsextract.cmd",
        VENV_DIR / "bin" / "wgsextract",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No wgsextract launcher found in {VENV_DIR}")


def check_openapi(wheel: Path, sdist: Path, launcher: Path) -> None:
    """#1537: both distributions carry the document emitted by the installed CLI."""
    generated = json.loads(
        subprocess.check_output([str(launcher), "serve", "--openapi"], cwd=ROOT)
    )
    with zipfile.ZipFile(wheel) as archive:
        wheel_document = json.loads(archive.read(OPENAPI_ASSET))
    with tarfile.open(sdist, "r:gz") as archive:
        path = f"{sdist.name.removesuffix('.tar.gz')}/src/{OPENAPI_ASSET}"
        asset = archive.extractfile(path)
        if asset is None:
            raise RuntimeError(f"OpenAPI asset {path} is not a file in {sdist.name}")
        with asset:
            sdist_document = json.load(asset)
    for package, document in ((wheel, wheel_document), (sdist, sdist_document)):
        if document != generated:
            raise RuntimeError(f"OpenAPI asset in {package.name} differs from the CLI")


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print(
            "uv is required for package-smoke but was not found on PATH",
            file=sys.stderr,
        )
        return 1

    shutil.rmtree(OUT_DIR, ignore_errors=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    _run([uv, "build", "--out-dir", str(DIST_DIR)])

    wheel = next(DIST_DIR.glob("*.whl"), None)
    sdist = next(DIST_DIR.glob("*.tar.gz"), None)
    if wheel is None or sdist is None:
        print(f"Expected a wheel and an sdist in {DIST_DIR}", file=sys.stderr)
        return 1

    _run([uv, "venv", str(VENV_DIR), "--clear"])
    _run([uv, "pip", "install", "--python", str(_venv_python()), str(wheel)])
    launcher = _wgsextract_launcher()
    _run([str(launcher), "--help"], quiet=True)
    check_openapi(wheel, sdist, launcher)
    return 0


if __name__ == "__main__":
    sys.exit(main())
