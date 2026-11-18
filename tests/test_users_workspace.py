"""The users workspace (~/wgs-extract): where a new install keeps config.toml
and the My Genomes library, while an older install keeps the
config.toml and library it already has."""

import json
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


@pytest.mark.parametrize("moved", [False, True])
def test_1087_gui_config_does_not_replace_existing_cli_settings(
    new_install, tmp_path, monkeypatch, caplog, moved
):
    workspace = Path.home() / "wgs-extract"
    if moved:
        appdata = tmp_path / "desktop"
        appdata.mkdir()
        monkeypatch.setattr(
            "wgsextract_cli.core.users_workspace.user_data_dir",
            lambda *a, **k: str(appdata),
        )
        workspace = tmp_path / "moved workspace"
        (appdata / "settings.json").write_text(
            json.dumps({"usersWorkspaces": {"wgs-extract": str(workspace)}})
        )
    workspace.mkdir(parents=True, exist_ok=True)
    workspace_config = workspace / "config.toml"
    gui_contents = 'cpu_threads = 2\noutput_directory = "/gui/outputs"\n'
    workspace_config.write_text(gui_contents)
    legacy = legacy_config_paths()[-1]
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('cpu_threads = 3\noutput_directory = "/cli/outputs"\n')

    config.reload_settings()

    assert get_config_path() == legacy
    assert settings["cpu_threads"] == 3
    assert settings["output_directory"] == "/cli/outputs"
    assert any(
        str(legacy) in message
        and str(workspace_config) in message
        and "--config" in message
        for message in caplog.messages
    )
    save_config({"cpu_threads": 4})
    assert workspace_config.read_text() == gui_contents
    assert config.read_toml(legacy)["cpu_threads"] == 4

    caplog.clear()
    config.set_config_path(workspace_config)
    config.reload_settings()
    assert settings["cpu_threads"] == 2
    assert not any(
        "Multiple configuration files" in message for message in caplog.messages
    )


def test_genomes_default_to_the_users_workspace(new_install):
    assert mygenomes_library() == str(Path.home() / "wgs-extract" / "mygenomes")


def test_1087_legacy_dataset_lookup_does_not_move_selected_inputs(new_install):
    genome = new_install / "datasets" / "joe"
    genome.mkdir(parents=True)

    assert mygenomes_library() == str(new_install / "datasets")
    assert genome.is_dir()
    assert not (new_install / "mygenomes").exists()


def test_a_configured_output_directory_wins(new_install, tmp_path):
    settings["output_directory"] = str(tmp_path / "mine")

    filled = with_config_defaults(
        ExtractRegionParams(
            input=tmp_path / "lab" / "in.bam", target="mt", formats="fasta"
        )
    )

    assert filled.outdir == tmp_path / "mine"


@pytest.mark.parametrize(
    "record_key",
    ["usersWorkspaces", "defaultUsersWorkspaceRoots", "bundleWorkspacesDirectory"],
)
def test_1087_cli_follows_the_desktops_moved_workspace(
    new_install, tmp_path, monkeypatch, record_key
):
    appdata = tmp_path / "desktop"
    appdata.mkdir()
    monkeypatch.setattr(
        "wgsextract_cli.core.users_workspace.user_data_dir",
        lambda *a, **k: str(appdata),
    )
    chosen = tmp_path / "large disk" / "wgs-extract"
    chosen.mkdir(parents=True)
    if record_key == "bundleWorkspacesDirectory":
        record = {record_key: str(chosen.parent)}
    else:
        record = {record_key: {"wgs-extract": str(chosen)}}
    (appdata / "settings.json").write_text(json.dumps(record))
    (chosen / "config.toml").write_text('mygenomes_library = "/my/shared/genomes"\n')
    assert get_config_path() == chosen / "config.toml"
    config.reload_settings()
    assert mygenomes_library() == "/my/shared/genomes"


def test_1087_blank_canonical_library_does_not_resurrect_stale_alias(new_install):
    settings["dataset_library"] = "/no-longer-used/datasets"
    settings["mygenomes_library"] = ""
    assert mygenomes_library() == str(Path.home() / "wgs-extract" / "mygenomes")


@pytest.mark.parametrize("current_record_exists", [False, True])
def test_1087_use_default_does_not_resurrect_a_stale_legacy_desktop_location(
    new_install, tmp_path, monkeypatch, current_record_exists
):
    from wgsextract_cli.core.users_workspace import users_workspace

    monkeypatch.setattr(
        "wgsextract_cli.core.users_workspace.user_data_dir",
        lambda app, **kwargs: str(tmp_path / app),
    )
    stale = tmp_path / "previous-workspace"
    legacy = tmp_path / "dev.guiforcli.web.embed.wgsextract"
    legacy.mkdir()
    (legacy / "settings.json").write_text(
        json.dumps({"usersWorkspaces": {"wgs-extract": str(stale)}})
    )
    if current_record_exists:
        current = tmp_path / "WGSExtract-gui"
        current.mkdir()
        (current / "settings.json").write_text('{"usersWorkspaces": {}}')
    expected = Path.home() / "wgs-extract" if current_record_exists else stale
    assert users_workspace() == expected


def test_1087_interactive_cli_bootstrap_marks_workspace_before_writing_config(
    new_install, monkeypatch
):
    from wgsextract_cli.cli.parser import bootstrap_default_config

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    path = get_config_path()
    bootstrap_default_config(path)
    assert path.is_file()
    assert (path.parent / SENTINEL_NAME).is_file()
