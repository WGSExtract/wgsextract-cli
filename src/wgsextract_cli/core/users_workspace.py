"""The users workspace: one visible folder in the user's home (``~/wgs-extract``,
``%USERPROFILE%\\wgs-extract`` on Windows) holding what the user manages and
may want on another disk: config.toml and the My Genomes
library. The desktop app keeps the same folder (its ``{{usersWorkspace}}``), so
a CLI and an app on one computer share the settings file and its libraries.

System data (reference library, reference genomes, temporary files) stays local
to the CLI's own data folder unless config.toml says otherwise."""

import logging
from pathlib import Path

FOLDER_NAME = "wgs-extract"
# Marks the folder as one this program made, so the desktop app adopts it
# rather than treating it as an unrelated folder of the user's with that name.
SENTINEL_NAME = ".users-workspace"
SENTINEL_CONTENTS = "WGS Extract users workspace\n"

MYGENOMES_FOLDER = "mygenomes"
# What the My Genomes library's folder was called before it was renamed.
LEGACY_MYGENOMES_FOLDER = "datasets"


def users_workspace() -> Path:
    """The default users workspace folder in the user's home."""
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


def renamed_legacy_mygenomes(parent: Path) -> Path | None:
    """``parent/mygenomes`` when it exists, after renaming an older
    ``parent/datasets`` to it; None when neither exists. Used to keep an
    existing install's My Genomes library where it is, under its new name."""
    current = parent / MYGENOMES_FOLDER
    if current.is_dir():
        return current
    legacy = parent / LEGACY_MYGENOMES_FOLDER
    if not legacy.is_dir():
        return None
    try:
        legacy.rename(current)
    except OSError as e:
        logging.warning(
            f"Could not rename {legacy} to {current} ({e}); using it as it is."
        )
        return legacy
    logging.info(f"Renamed the My Genomes library {legacy} to {current}.")
    return current
