"""Execute the import and full-collection safety check after static move proof."""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
from pathlib import Path


def import_modules(modules: list[str]) -> None:
    for module in modules:
        if not (
            module == "wgsextract_cli" or module.startswith("wgsextract_cli.")
        ) or not all(part.isidentifier() for part in module.split(".")):
            raise ValueError(f"Not a CLI module: {module!r}")
        importlib.import_module(module)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modules", required=True, help="JSON CLI module names")
    modules = json.loads(parser.parse_args().modules)
    import_modules(modules)
    output = Path("out/pure-move-collection.log")
    output.parent.mkdir(exist_ok=True)
    with output.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [
                "pixi",
                "run",
                "--no-install",
                "pytest",
                "--collect-only",
                "-q",
                "-o",
                "addopts=",
                "--ci-tier",
                "all",
                "--ci-shard",
                "all",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    if result.returncode:
        print(output.read_text(encoding="utf-8"))
        raise SystemExit(result.returncode)
    print(f"Imported {len(modules)} modules; full collection passed ({output}).")


if __name__ == "__main__":
    main()
