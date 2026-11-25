"""Include generated CLI assets in Hatch distributions without editing sources."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

OPENAPI = "wgsextract_cli/assets/schemas/wgsextract.server.openapi.json"


class PackageArtifacts(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # Editable installs and runtime consumers generate on demand instead.
        if self.target_name not in {"wheel", "sdist"} or version == "editable":
            return
        self._staging = TemporaryDirectory(dir=self.directory)
        document = Path(self._staging.name) / Path(OPENAPI).name
        subprocess.run(
            [
                sys.executable,
                str(Path(self.root) / "scripts/gen_server_openapi.py"),
                "--output",
                str(document),
            ],
            check=True,
        )
        destination = f"src/{OPENAPI}" if self.target_name == "sdist" else OPENAPI
        build_data["force_include"][str(document)] = destination

    def finalize(
        self, version: str, build_data: dict[str, Any], artifact_path: str
    ) -> None:
        if hasattr(self, "_staging"):
            self._staging.cleanup()
