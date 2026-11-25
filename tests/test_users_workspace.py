"""The users workspace (~/wgs-extract): where a new install keeps config.toml
and the My Genomes library, while an older install keeps the
config.toml and library it already has."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.home_isolation import state_directory_setting
from wgsextract_cli.api import ExtractRegionParams
from wgsextract_cli.api._params import with_config_defaults
from wgsextract_cli.commands._analyze_workflows import AnalyzeJob, run_cli_subcommand
from wgsextract_cli.core import config
from wgsextract_cli.core.config import (
    get_config_path,
    legacy_config_paths,
    mygenomes_library,
    save_config,
    settings,
)
from wgsextract_cli.core.users_workspace import SENTINEL_NAME
from wgsextract_cli.core.workspace_activity import desktop_data_dir, workspace_use


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


def test_1322_cli_cannot_start_or_save_during_a_desktop_move(tmp_path, monkeypatch):
    file = tmp_path / "config.toml"
    file.write_text("cpu_threads = 2\n")
    # The desktop publishes its PID before any files are moved.
    activity = desktop_data_dir() / "workspace-activity"
    activity.mkdir(parents=True, exist_ok=True)
    mover = activity / f"move-{os.getpid()}-a"
    mover.touch()
    monkeypatch.setenv("WGSEXTRACT_GUI_APP_DATA", str(tmp_path / "custom-app-data"))
    args = [
        sys.executable,
        "-m",
        "wgsextract_cli",
        "--config",
        str(file),
        "ref",
        "status",
    ]

    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert "being moved" in result.stderr

    # #1506: even a version query still admits the workspace before loading config.
    version = subprocess.run(
        [*args[:-2], "--version"], capture_output=True, text=True, timeout=30
    )
    assert version.returncode == 1
    assert version.stdout == ""
    assert "being moved" in version.stderr

    with config.scoped_config(file):
        with pytest.raises(config.WGSExtractError, match="being moved"):
            save_config({"cpu_threads": 7})
        assert config.read_toml(file)["cpu_threads"] == 2

        mover.unlink()
        save_config({"cpu_threads": 7})

    assert config.read_toml(file)["cpu_threads"] == 7
    assert not list(activity.iterdir())


def test_1322_cli_without_the_desktop_app_leaves_no_app_folder(tmp_path):
    # Install Lab: a CLI-only uninstall left the app's workspace-activity folder.
    file = tmp_path / "config.toml"
    file.write_text("cpu_threads = 2\n")
    with config.scoped_config(file):
        save_config({"cpu_threads": 7})

    assert config.read_toml(file)["cpu_threads"] == 7
    assert not desktop_data_dir().exists()


def test_1322_admitted_workflow_child_survives_a_refused_move(tmp_path, capfd):
    settings_file = tmp_path / "config.toml"
    settings_file.write_text(
        state_directory_setting(tmp_path) + f"temp_directory = '{tmp_path}'\n",
        encoding="utf-8",
    )
    activity = desktop_data_dir() / "workspace-activity"
    activity.mkdir(parents=True)
    with config.scoped_config(settings_file), workspace_use():
        # A mover publishes first, but the parent's active lease means it will
        # refuse the move. Keep its marker present through the child's startup.
        mover = activity / f"move-{os.getpid()}-a"
        mover.touch()
        try:
            run_cli_subcommand(["ref", "status"], AnalyzeJob(ref=str(tmp_path)))
        finally:
            mover.unlink()

    assert f"Reference library: {tmp_path}" in capfd.readouterr().out
    assert not list(activity.iterdir())


def test_1322_cli_saves_through_a_migrated_config_path(tmp_path):
    previous = tmp_path / "old" / "config.toml"
    destination = tmp_path / "new" / "config.toml"
    destination.parent.mkdir()
    destination.write_text("cpu_threads = 2\nram_gb = 4\n")
    desktop = desktop_data_dir()
    desktop.mkdir(parents=True, exist_ok=True)
    (desktop / "settings.json").write_text(
        json.dumps({"movedConfigPaths": {str(previous): str(destination)}})
    )

    with config.scoped_config(previous):
        assert settings["cpu_threads"] == 2
        save_config({"cpu_threads": 7})
    assert not previous.exists()
    assert config.read_toml(destination) == {"cpu_threads": 7, "ram_gb": 4}

    previous.parent.mkdir()
    previous.write_text("cpu_threads = 5\n")
    with config.scoped_config(previous):
        save_config({"cpu_threads": 9})
    assert config.read_toml(previous)["cpu_threads"] == 9
    assert config.read_toml(destination)["cpu_threads"] == 7


def test_1322_cli_follows_a_redirect_recorded_through_an_alias(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    alias = tmp_path / "alias"
    if sys.platform == "win32":
        from _winapi import CreateJunction

        CreateJunction(str(original), str(alias))
    else:
        alias.symlink_to(original, target_is_directory=True)
    recorded = str(alias / "old.toml")
    if sys.platform == "win32":
        recorded = alias.drive.swapcase() + recorded[len(alias.drive) :]
    destination = tmp_path / "moved.toml"
    destination.write_text("cpu_threads = 2\n")
    desktop = desktop_data_dir()
    desktop.mkdir(parents=True, exist_ok=True)
    (desktop / "settings.json").write_text(
        json.dumps({"movedConfigPaths": {recorded: str(destination)}})
    )

    with config.scoped_config(original / "old.toml"):
        save_config({"cpu_threads": 7})

    assert not (original / "old.toml").exists()
    assert config.read_toml(destination)["cpu_threads"] == 7


def test_1322_app_launched_cli_uses_the_apps_overridden_location(tmp_path, monkeypatch):
    previous = tmp_path / "old" / "config.toml"
    moved = tmp_path / "moved"
    moved.mkdir()
    destination = moved / "config.toml"
    destination.write_text("cpu_threads = 2\n")
    app_data = tmp_path / "custom-app-data"
    app_data.mkdir()
    (app_data / "settings.json").write_text(
        json.dumps(
            {
                "usersWorkspaces": {"wgs-extract": str(moved)},
                "movedConfigPaths": {str(previous): str(destination)},
            }
        )
    )
    monkeypatch.setenv("WGSEXTRACT_GUI_APP_DATA", str(app_data))

    with config.scoped_config():
        assert get_config_path() == destination
    with config.scoped_config(previous):
        save_config({"cpu_threads": 7})

    assert not previous.exists()
    assert config.read_toml(destination)["cpu_threads"] == 7


def test_1322_save_reloads_settings_after_an_explicit_config_moves(tmp_path):
    previous = tmp_path / "old.toml"
    destination = tmp_path / "new.toml"
    previous.write_text("cpu_threads = 2\nram_gb = 4\n")
    desktop = desktop_data_dir()
    desktop.mkdir(parents=True, exist_ok=True)

    with config.scoped_config(previous):
        assert settings["cpu_threads"] == 2
        previous.rename(destination)
        (desktop / "settings.json").write_text(
            json.dumps({"movedConfigPaths": {str(previous): str(destination)}})
        )
        save_config({"cpu_threads": 7})

        assert settings["cpu_threads"] == 7
        assert settings["ram_gb"] == 4
        assert get_config_path() == destination
    assert not previous.exists()
    assert config.read_toml(destination)["cpu_threads"] == 7
