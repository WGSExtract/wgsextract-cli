"""The users workspace (~/wgs-extract): where a new install keeps config.toml
and the My Genomes library, while an older install keeps the
config.toml and library it already has."""

from pathlib import Path

import pytest

from wgsextract_cli.api import ExtractRegionParams
from wgsextract_cli.api._params import with_config_defaults
from wgsextract_cli.core import config
from wgsextract_cli.core.config import (
    get_config_path,
    legacy_config_paths,
    mygenomes_library,
    save_config,
    settings,
)
from wgsextract_cli.core.users_workspace import SENTINEL_NAME


@pytest.fixture
def new_install(tmp_path, monkeypatch):
    """A home with no config.toml anywhere and an empty data folder."""
    monkeypatch.setattr(config._PROCESS_CONFIG, "path", None)
    for path in [*legacy_config_paths(), Path.home() / "wgs-extract" / "config.toml"]:
        path.unlink(missing_ok=True)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr("wgsextract_cli.core.runtime.repo_root", lambda: data)
    for key in ("output_directory", "mygenomes_library", "dataset_library"):
        settings.pop(key, None)
    return data


def test_a_new_install_keeps_its_settings_in_the_users_workspace(new_install):
    workspace = Path.home() / "wgs-extract"
    assert get_config_path() == workspace / "config.toml"

    save_config({"cpu_threads": 2})

    assert "cpu_threads = 2" in (workspace / "config.toml").read_text()
    # Marked as this program's, so the desktop app shares it.
    assert (workspace / SENTINEL_NAME).is_file()


def test_an_older_install_keeps_its_config_where_it_is(new_install):
    legacy = legacy_config_paths()[-1]
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("cpu_threads = 3\n")

    assert get_config_path() == legacy


def test_genomes_default_to_the_users_workspace(new_install):
    assert mygenomes_library() == str(Path.home() / "wgs-extract" / "mygenomes")


def test_an_older_datasets_folder_is_renamed_mygenomes_and_kept(new_install):
    genome = new_install / "datasets" / "joe"
    genome.mkdir(parents=True)

    assert mygenomes_library() == str(new_install / "mygenomes")
    assert (new_install / "mygenomes" / "joe").is_dir()
    assert not (new_install / "datasets").exists()


def test_a_configured_output_directory_wins(new_install, tmp_path):
    settings["output_directory"] = str(tmp_path / "mine")

    filled = with_config_defaults(
        ExtractRegionParams(
            input=tmp_path / "lab" / "in.bam", target="mt", formats="fasta"
        )
    )

    assert filled.outdir == tmp_path / "mine"
