"""Include generated CLI assets in Hatch distributions without editing sources."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

OPENAPI = "wgsextract_cli/assets/schemas/wgsextract.server.openapi.json"
HELP_INDEX = "wgsextract_cli/assets/cli/help-index.json"


class PackageArtifacts(BuildHookInterface):  # type: ignore[misc]
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # Editable installs and runtime consumers generate on demand instead.
        if self.target_name not in {"wheel", "sdist"} or version == "editable":
            return
        self._staging = TemporaryDirectory(dir=self.directory)
        staging = Path(self._staging.name)
        document = staging / Path(OPENAPI).name
        subprocess.run(
            [
                sys.executable,
                str(Path(self.root) / "scripts/gen_server_openapi.py"),
                "--output",
                str(document),
            ],
            check=True,
        )
        index = staging / Path(HELP_INDEX).name
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import runpy, sys; sys.path.insert(0, 'src'); "
                "runpy.run_module('wgsextract_cli.cli.help_index', run_name='__main__')",
                "--output",
                str(index),
            ],
            cwd=self.root,
            check=True,
        )
        for local, path in (
            (document, OPENAPI),
            (index, HELP_INDEX),
            (index.with_suffix(".key"), HELP_INDEX.removesuffix(".json") + ".key"),
        ):
            destination = f"src/{path}" if self.target_name == "sdist" else path
            build_data["force_include"][str(local)] = destination

    def finalize(
        self, version: str, build_data: dict[str, Any], artifact_path: str
    ) -> None:
        if hasattr(self, "_staging"):
            self._staging.cleanup()
