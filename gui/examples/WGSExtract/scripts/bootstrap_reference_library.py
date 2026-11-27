"""Bootstrap the configured library, not the last selected reference FASTA."""

import os
import sys
import tomllib
from pathlib import Path


def main() -> None:
    from wgsextract_cli.main import main as cli

    workspace = os.environ.get("WGSEXTRACT_GUI_USERS_WORKSPACE") or os.environ.get(
        "WGSEXTRACT_GUI_BUNDLE_WORKSPACE"
    )
    root = Path(workspace) if workspace else Path(__file__).resolve().parents[1]
    config = Path(os.environ.get("WGSEXTRACT_GUI_CONFIG_PATH") or root / "config.toml")
    values = (
        tomllib.loads(config.read_text(encoding="utf-8-sig"))
        if config.is_file()
        else {}
    )
    library = (
        sys.argv[1].removeprefix("--library=") if len(sys.argv) > 1 else ""
    ) or values.get("reference_library")
    arguments = ["--config", str(config), "ref", "bootstrap", *sys.argv[2:]]
    if isinstance(library, str) and library:
        arguments.extend(["--ref", library])
    cli(arguments)


if __name__ == "__main__":
    main()
