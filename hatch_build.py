"""Include a compact help index in distribution artifacts, not the source tree."""

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):  # type: ignore[misc]
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name == "wheel" and version == "editable":
            return
        root = Path(self.root)
        package_file = "src/wgsextract_cli/assets/cli/help-index.json"
        staging = Path(self.directory)
        staging.mkdir(parents=True, exist_ok=True)
        self.generated = tempfile.TemporaryDirectory(prefix="help-index-", dir=staging)
        target = Path(self.generated.name) / "help-index.json"
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import runpy, sys; sys.path.insert(0, 'src'); "
                "runpy.run_module('wgsextract_cli.cli.help_index', run_name='__main__')",
                "--output",
                str(target),
            ],
            cwd=root,
            check=True,
        )
        destination = package_file if self.target_name == "sdist" else package_file[4:]
        build_data["force_include"][str(target)] = destination
        build_data["force_include"][str(target.with_suffix(".key"))] = (
            destination.replace(".json", ".key")
        )

    def finalize(
        self, version: str, build_data: dict[str, Any], artifact_path: str
    ) -> None:
        if self.target_name != "wheel" or version != "editable":
            self.generated.cleanup()
