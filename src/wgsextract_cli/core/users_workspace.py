"""The users workspace: one visible folder in the user's home (``~/wgs-extract``,
``%USERPROFILE%\\wgs-extract`` on Windows) holding what the user manages and
may want on another disk: config.toml and the My Genomes
library. The desktop app keeps the same folder (its ``{{usersWorkspace}}``), so
a CLI and an app on one computer share the settings file and its libraries.

System data (reference library, reference genomes, temporary files) stays local
to the CLI's own data folder unless config.toml says otherwise."""

import json
import logging
from pathlib import Path

from platformdirs import user_data_dir

from wgsextract_cli.core.atomic_output import atomic_write
from wgsextract_cli.core.workspace_activity import (
    configured_desktop_data_dir,
    workspace_use,
)

FOLDER_NAME = "wgs-extract"
# Marks the folder as one this program made, so the desktop app adopts it
# rather than treating it as an unrelated folder of the user's with that name.
SENTINEL_NAME = ".users-workspace"
SENTINEL_CONTENTS = "WGS Extract users workspace\n"

MYGENOMES_FOLDER = "mygenomes"
# What the My Genomes library's folder was called before it was renamed.
LEGACY_MYGENOMES_FOLDER = "datasets"


def users_workspace() -> Path:
    """Follow the desktop's location record, including pre-split moves."""
    app_dirs = [
        Path(user_data_dir(app, appauthor=False))
        for app in ("WGSExtract-gui", "dev.guiforcli.web.embed.wgsextract")
    ]
    override = configured_desktop_data_dir()
    if override is not None:
        app_dirs.insert(0, override)
    for app_dir in dict.fromkeys(app_dirs):
        record = app_dir / "settings.json"
        try:
            settings = json.loads(record.read_text(encoding="utf-8"))
            for key in (
                "usersWorkspaces",
                "bundleWorkspacesDirectory",
                "defaultUsersWorkspaceRoots",
                "defaultWorkspaceRoots",
            ):
                if key == "bundleWorkspacesDirectory":
                    legacy = settings.get(key)
                    if isinstance(legacy, str) and Path(legacy).is_absolute():
                        return Path(legacy) / "wgs-extract"
                    continue
                entries = settings.get(key)
                chosen = (
                    entries.get("wgs-extract") if isinstance(entries, dict) else None
                )
                if isinstance(chosen, str) and Path(chosen).is_absolute():
                    return Path(chosen)
            return Path.home() / FOLDER_NAME
        except (OSError, ValueError, AttributeError) as error:
            logging.debug("Cannot read users workspace record %s: %s", record, error)
    return Path.home() / FOLDER_NAME


def default_mygenomes_library() -> Path:
    return users_workspace() / MYGENOMES_FOLDER


def claim_users_workspace(folder: Path) -> None:
    """Create ``folder`` and mark it as a users workspace when it is new or
    empty. A folder the user already filled is used as it is, unmarked."""
    try:
        existed = folder.exists()
        if existed and any(folder.iterdir()):
            return
        folder.mkdir(parents=True, exist_ok=True)
        (folder / SENTINEL_NAME).write_text(
            SENTINEL_CONTENTS, encoding="utf-8", newline="\n"
        )
    except OSError as e:
        logging.debug(f"Could not mark {folder} as the users workspace: {e}")


@workspace_use()
def write_initial_config(config_path: Path, contents: str) -> None:
    """Claim the default workspace before a CLI-first bootstrap populates it."""
    if config_path.parent == users_workspace():
        claim_users_workspace(config_path.parent)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(config_path, contents.encode("utf-8"))


def existing_mygenomes_library(parent: Path) -> Path | None:
    """Find an existing library without renaming selected input paths."""
    current = parent / MYGENOMES_FOLDER
    if current.is_dir():
        return current
    legacy = parent / LEGACY_MYGENOMES_FOLDER
    return legacy if legacy.is_dir() else current if current.is_dir() else None
