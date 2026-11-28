"""The users workspace: one visible folder in the user's home (``~/wgs-extract``,
``%USERPROFILE%\\wgs-extract`` on Windows) holding what the user manages and
may want on another disk: config.toml and the My Genomes
library. The desktop app keeps the same folder (its ``{{usersWorkspace}}``), so
a CLI and an app on one computer share the settings file and its libraries.

System data (reference library, reference genomes, temporary files) stays local
to the CLI's own data folder unless config.toml says otherwise."""

import os
from collections.abc import Iterator
from pathlib import Path

from wgsextract_cli.core.workspace_activity import (
    configured_desktop_data_dir,
    relocated_config_path,
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


def _desktop_data_dirs() -> Iterator[Path]:
    override = configured_desktop_data_dir()
    if override is not None:
        yield override
    local = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    for app in ("WGSExtract-gui", "dev.guiforcli.web.embed.wgsextract"):
        if local and Path(local).is_absolute():
            # The desktop uses LOCALAPPDATA; retain the Windows known-folder
            # lookup for installs whose inherited environment has changed.
            yield Path(local) / app
        from platformdirs import user_data_dir

        yield Path(user_data_dir(app, appauthor=False))


def users_workspace() -> Path:
    """Follow the desktop's location record, including pre-split moves."""
    seen = set()
    for app_dir in _desktop_data_dirs():
        if app_dir in seen:
            continue
        seen.add(app_dir)
        record = app_dir / "settings.json"
        try:
            document = record.read_text(encoding="utf-8")
            import json

            settings = json.loads(document)
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
        except FileNotFoundError:
            continue
        except (OSError, ValueError, AttributeError) as error:
            import logging

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
        import logging

        logging.debug(f"Could not mark {folder} as the users workspace: {e}")


@workspace_use()
def write_initial_config(config_path: Path, contents: str) -> bool:
    """Claim the default workspace before a CLI-first bootstrap populates it.
    False when the app or another command created the file first."""
    from wgsextract_cli.core.atomic_output import atomic_write
    from wgsextract_cli.core.file_lock import shared_file_lock

    config_path = relocated_config_path(config_path)
    if config_path.parent == users_workspace():
        claim_users_workspace(config_path.parent)
    with shared_file_lock(config_path) as target:
        if target.exists():
            return False
        atomic_write(target, contents.encode("utf-8"))
    return True


def existing_mygenomes_library(parent: Path) -> Path | None:
    """Find an existing library without renaming selected input paths."""
    current = parent / MYGENOMES_FOLDER
    if current.is_dir():
        return current
    legacy = parent / LEGACY_MYGENOMES_FOLDER
    return legacy if legacy.is_dir() else current if current.is_dir() else None
