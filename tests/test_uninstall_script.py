from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNINSTALL_SH = ROOT / "uninstall.sh"
SH = shutil.which("sh") or "sh"

pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None or sys.platform == "win32",
    reason="uninstall.sh is the macOS and Linux uninstaller (POSIX sh)",
)


def make_fake_install(root: Path) -> Path:
    install_dir = root / "wgsextract-cli"
    app_dir = install_dir / "app"
    pixi_dir = install_dir / ".pixi"
    app_dir.mkdir(parents=True)
    (app_dir / "pixi.toml").write_text("[workspace]\nname = 'fake'\n", encoding="utf-8")
    (pixi_dir / "cache").mkdir(parents=True)
    (pixi_dir / "envs").mkdir(parents=True)
    (install_dir / "wgsextract").write_text("#!/bin/sh\n", encoding="utf-8")
    shutil.copy(UNINSTALL_SH, install_dir / "uninstall.sh")
    return install_dir


def run_uninstall(
    script: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [SH, str(script), *args],
        check=True,
        env=run_env,
        text=True,
        capture_output=True,
        input=input_text,
    )


def make_fake_pixi_home(home: Path) -> Path:
    pixi_home = home / ".pixi"
    pixi_bin = pixi_home / "bin"
    pixi_bin.mkdir(parents=True)
    pixi = pixi_bin / "pixi"
    pixi.write_text("#!/bin/sh\necho pixi 0.0.0\n", encoding="utf-8")
    pixi.chmod(0o755)
    (home / ".zshrc").write_text(
        'export OTHER=1\nexport PATH="$HOME/.pixi/bin:$PATH"\n',
        encoding="utf-8",
    )
    fish_config = home / ".config" / "fish" / "config.fish"
    fish_config.parent.mkdir(parents=True)
    fish_config.write_text(
        "set -x PIXI_BIN_PATH $HOME/.pixi/bin\nset -gx PATH $HOME/.pixi/bin $PATH\n",
        encoding="utf-8",
    )
    return pixi_home


def assert_profile_backup_exists(home: Path) -> None:
    assert list(home.glob(".zshrc.wgsextract-uninstall-backup.*"))


def test_uninstall_removes_default_install_tree(tmp_path: Path) -> None:
    install_dir = make_fake_install(tmp_path)

    result = run_uninstall(install_dir / "uninstall.sh", "--yes")

    assert "Uninstall complete." in result.stdout
    assert not install_dir.exists()


@pytest.mark.parametrize("remove_config", [False, True])
@pytest.mark.parametrize("has_data", [False, True])
def test_1087_uninstall_removes_only_explicitly_requested_workspace_config(
    tmp_path: Path, remove_config: bool, has_data: bool
) -> None:
    install = make_fake_install(tmp_path)
    home = tmp_path / "home"
    workspace = home / "wgs-extract"
    workspace.mkdir(parents=True)
    config = workspace / "config.toml"
    config.write_text("cpu_threads = 2\n")
    marker = workspace / ".users-workspace"
    marker.write_text("WGS Extract users workspace\n")
    library = workspace / "mygenomes" / "sample"
    if has_data:
        library.mkdir(parents=True)
        (library / "keep.bam").write_bytes(b"keep")

    run_uninstall(
        install / "uninstall.sh",
        "--yes",
        "--keep-pixi",
        *(["--remove-config"] if remove_config else []),
        env={"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")},
    )

    assert config.exists() is not remove_config
    if has_data:
        assert (library / "keep.bam").read_bytes() == b"keep"
        assert marker.is_file()
    elif remove_config:
        assert not workspace.exists()
    else:
        assert marker.is_file()


def test_1087_uninstall_keeps_config_in_an_unmarked_user_folder(tmp_path: Path) -> None:
    install = make_fake_install(tmp_path)
    home = tmp_path / "home"
    workspace = home / "wgs-extract"
    workspace.mkdir(parents=True)
    config = workspace / "config.toml"
    config.write_text("unrelated = true\n")

    run_uninstall(
        install / "uninstall.sh",
        "--yes",
        "--keep-pixi",
        "--remove-config",
        env={"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")},
    )

    assert config.read_text() == "unrelated = true\n"


def test_1087_1246_uninstall_refuses_symlinked_workspace_config_before_removing(
    tmp_path: Path,
) -> None:
    install = make_fake_install(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "config.toml").write_text("cpu_threads = 3\n")
    (home / "wgs-extract").symlink_to(shared, target_is_directory=True)

    result = subprocess.run(
        [SH, str(install / "uninstall.sh"), "--yes", "--keep-pixi", "--remove-config"],
        env={**os.environ, "HOME": str(home)},
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert (
        "Refusing to remove users workspace config through a symlink" in result.stderr
    )
    assert "Uninstall complete." not in result.stdout
    assert (shared / "config.toml").read_text() == "cpu_threads = 3\n"
    # The refusal comes before anything is deleted, not after the install is gone.
    assert (install / "app" / "pixi.toml").is_file()


def test_uninstall_yes_keeps_pixi_by_default(tmp_path: Path) -> None:
    install_dir = make_fake_install(tmp_path)
    home = tmp_path / "home"
    pixi_home = make_fake_pixi_home(home)

    result = run_uninstall(
        install_dir / "uninstall.sh",
        "--yes",
        env={"HOME": str(home)},
    )

    assert "Pass --remove-pixi" in result.stdout
    assert not install_dir.exists()
    assert (pixi_home / "bin" / "pixi").exists()
    assert ".pixi/bin" in (home / ".zshrc").read_text(encoding="utf-8")


def test_uninstall_eof_cancels_without_error(tmp_path: Path) -> None:
    install_dir = make_fake_install(tmp_path)

    result = subprocess.run(
        [SH, str(install_dir / "uninstall.sh")],
        env=os.environ,
        text=True,
        capture_output=True,
        input="",
    )

    assert result.returncode == 0
    assert "Uninstall cancelled." in result.stdout
    assert install_dir.exists()


def test_uninstall_interactive_can_remove_pixi(tmp_path: Path) -> None:
    install_dir = make_fake_install(tmp_path)
    home = tmp_path / "home"
    pixi_home = make_fake_pixi_home(home)

    result = run_uninstall(
        install_dir / "uninstall.sh",
        env={"HOME": str(home)},
        input_text="y\ny\n",
    )

    assert "Remove Pixi too?" in result.stdout
    assert not install_dir.exists()
    assert not pixi_home.exists()
    zshrc = (home / ".zshrc").read_text(encoding="utf-8")
    assert "export OTHER=1" in zshrc
    assert 'export PATH="$HOME/.pixi/bin:$PATH"' not in zshrc
    fish_config = (home / ".config" / "fish" / "config.fish").read_text(
        encoding="utf-8"
    )
    assert "set -x PIXI_BIN_PATH $HOME/.pixi/bin" in fish_config
    assert "set -gx PATH $HOME/.pixi/bin $PATH" not in fish_config
    assert_profile_backup_exists(home)


def test_uninstall_yes_remove_pixi_removes_pixi(tmp_path: Path) -> None:
    install_dir = make_fake_install(tmp_path)
    home = tmp_path / "home"
    pixi_home = make_fake_pixi_home(home)

    result = run_uninstall(
        install_dir / "uninstall.sh",
        "--yes",
        "--remove-pixi",
        env={"HOME": str(home)},
    )

    assert "Removing Pixi directory" in result.stdout
    assert not install_dir.exists()
    assert not pixi_home.exists()
    assert 'export PATH="$HOME/.pixi/bin:$PATH"' not in (home / ".zshrc").read_text(
        encoding="utf-8"
    )
    assert_profile_backup_exists(home)


def test_uninstall_pixi_profile_cleanup_preserves_non_path_mentions(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    home = tmp_path / "home"
    pixi_home = make_fake_pixi_home(home)
    (home / ".zshrc").write_text(
        "\n".join(
            [
                "# TODO: check whether ~/.pixi/bin is needed later",
                'alias pixi-note="echo ~/.pixi/bin/pixi"',
                'export PATH="/usr/bin:$PATH"  # TODO: maybe add ~/.pixi/bin later',
                'export PATH="$HOME/.pixi/bin:$PATH"',
                "export OTHER=1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    run_uninstall(
        install_dir / "uninstall.sh",
        "--yes",
        "--remove-pixi",
        env={"HOME": str(home)},
    )

    zshrc = (home / ".zshrc").read_text(encoding="utf-8")
    assert not pixi_home.exists()
    assert "# TODO: check whether ~/.pixi/bin is needed later" in zshrc
    assert 'alias pixi-note="echo ~/.pixi/bin/pixi"' in zshrc
    assert 'export PATH="/usr/bin:$PATH"  # TODO: maybe add ~/.pixi/bin later' in zshrc
    assert 'export PATH="$HOME/.pixi/bin:$PATH"' not in zshrc
    assert "export OTHER=1" in zshrc
    assert_profile_backup_exists(home)


def test_uninstall_remove_pixi_handles_missing_pixi_executable(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    home = tmp_path / "home"
    pixi_home = make_fake_pixi_home(home)
    (pixi_home / "bin" / "pixi").unlink()

    result = run_uninstall(
        install_dir / "uninstall.sh",
        "--yes",
        "--remove-pixi",
        env={"HOME": str(home)},
    )

    assert "Removing Pixi directory" in result.stdout
    assert not install_dir.exists()
    assert not pixi_home.exists()


def test_uninstall_remove_pixi_refuses_symlinked_shell_profile(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    home = tmp_path / "home"
    pixi_home = make_fake_pixi_home(home)
    dotfiles_dir = home / "dotfiles"
    dotfiles_dir.mkdir()
    zshrc_target = dotfiles_dir / "zshrc"
    zshrc_target.write_text(
        'export PATH="$HOME/.pixi/bin:$PATH"\n',
        encoding="utf-8",
    )
    (home / ".zshrc").unlink()
    (home / ".zshrc").symlink_to(zshrc_target)

    result = subprocess.run(
        [SH, str(install_dir / "uninstall.sh"), "--yes", "--remove-pixi"],
        env={
            **os.environ,
            "HOME": str(home),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "Refusing to edit symlinked shell profile" in result.stderr
    assert (home / ".zshrc").is_symlink()
    assert pixi_home.exists()


def test_uninstall_refuses_empty_home(tmp_path: Path) -> None:
    install_dir = make_fake_install(tmp_path)

    result = subprocess.run(
        [SH, str(install_dir / "uninstall.sh"), "--yes"],
        env={
            **os.environ,
            "HOME": "",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "HOME environment variable is not set or is unsafe" in result.stderr
    assert install_dir.exists()


def test_uninstall_dry_run_keeps_install_tree(tmp_path: Path) -> None:
    install_dir = make_fake_install(tmp_path)

    result = run_uninstall(install_dir / "uninstall.sh", "--dry-run")

    assert "Dry run only" in result.stdout
    assert install_dir.exists()


def make_pixi_env(project: Path, manifest: Path) -> Path:
    """An installed Pixi environment, which records the manifest it belongs to."""
    env = project / "envs" / "default"
    (env / "conda-meta").mkdir(parents=True)
    (env / "conda-meta" / "pixi").write_text(
        f'{{\n  "manifest_path": "{manifest}",\n  "environment_name": "default"\n}}\n',
        encoding="utf-8",
    )
    (env / "keep.txt").write_text(f"{project.name}\n", encoding="utf-8")
    return env


def other_project_env(env_dir: Path, tmp_path: Path) -> Path:
    return make_pixi_env(
        env_dir / "other-project-456", tmp_path / "other" / "pixi.toml"
    )


def link_detached_envs(install_dir: Path, env_dir: Path, folder: str) -> Path:
    """Lay out envs the way Pixi's detached-environments setting does."""
    project = env_dir / folder
    make_pixi_env(project, (install_dir / "app" / "pixi.toml").resolve())
    app_pixi = install_dir / "app" / ".pixi"
    app_pixi.mkdir(parents=True, exist_ok=True)
    (app_pixi / "envs").symlink_to(project / "envs", target_is_directory=True)
    return project


def write_layout(install_dir: Path, **paths: Path | str) -> None:
    (install_dir / "install-layout.txt").write_text(
        "".join(f"{key}\t{path}\n" for key, path in paths.items()), encoding="utf-8"
    )


def test_uninstall_removes_external_launcher_and_installer_created_pixi_dirs(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    bin_dir = tmp_path / "bin"
    cache_dir = tmp_path / "cache"
    env_dir = tmp_path / "envs"
    bin_dir.mkdir()
    cache_dir.mkdir()
    link_detached_envs(install_dir, env_dir, "wgsextract-cli-123")
    (bin_dir / "wgsextract").write_text(
        '#!/bin/sh\n# WGS Extract CLI installer launcher\nexec /path/to/pixi run wgsextract "$@"\n',
        encoding="utf-8",
    )
    write_layout(
        install_dir,
        bin_dir=bin_dir,
        pixi_cache_dir=cache_dir,
        pixi_env_dir=env_dir,
        created_pixi_cache_dir=cache_dir,
        created_pixi_env_dir=env_dir,
    )

    run_uninstall(install_dir / "uninstall.sh", "--yes")

    assert not install_dir.exists()
    assert not (bin_dir / "wgsextract").exists()
    assert not cache_dir.exists()
    assert not env_dir.exists()


def test_1246_uninstall_removes_only_its_own_envs_from_a_shared_env_dir(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    env_dir = tmp_path / "shared-envs"
    own = link_detached_envs(install_dir, env_dir, "wgsextract-cli-123")
    other = other_project_env(env_dir, tmp_path)

    result = run_uninstall(
        install_dir / "uninstall.sh",
        "--pixi-env-dir",
        str(env_dir),
        env={"HOME": str(tmp_path / "home")},
        input_text="y\n",
    )

    prompt = result.stdout.split("Continue?")[0]
    assert f"{own} (this install's Pixi environments)" in prompt
    assert f"{env_dir} (" not in prompt
    assert not own.exists()
    assert (other / "keep.txt").is_file()
    assert not install_dir.exists()


def test_1246_uninstall_keeps_env_dir_without_this_installs_envs(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    env_dir = tmp_path / "shared-envs"
    other = other_project_env(env_dir, tmp_path)

    result = run_uninstall(
        install_dir / "uninstall.sh", "--yes", "--pixi-env-dir", str(env_dir)
    )

    assert "environments were not found" in result.stdout
    assert other.is_dir()
    assert not install_dir.exists()


@pytest.mark.parametrize("recorded_as_created", [False, True])
def test_1246_uninstall_removes_external_cache_only_if_installer_created_it(
    tmp_path: Path, recorded_as_created: bool
) -> None:
    install_dir = make_fake_install(tmp_path)
    cache_dir = tmp_path / "shared-cache"
    (cache_dir / "pkgs").mkdir(parents=True)
    (cache_dir / "pkgs" / "other.conda").write_bytes(b"other project")
    # Pixi's own cached environments are part of the cache, not other projects.
    (cache_dir / "cached-envs-v0" / "63c1a1447ff92cee" / "conda-meta").mkdir(
        parents=True
    )
    layout: dict[str, Path] = {"pixi_cache_dir": cache_dir}
    if recorded_as_created:
        layout["created_pixi_cache_dir"] = cache_dir
    write_layout(install_dir, **layout)

    result = run_uninstall(install_dir / "uninstall.sh", "--dry-run")
    assert (
        f"{cache_dir} (the Pixi cache the installer created)" in result.stdout
    ) is recorded_as_created

    run_uninstall(install_dir / "uninstall.sh", "--yes")

    assert cache_dir.exists() is not recorded_as_created
    assert not install_dir.exists()


def test_1246_uninstall_keeps_an_installer_created_cache_that_holds_the_env_dir(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    cache_dir = tmp_path / "pixi"
    env_dir = cache_dir / "envs"
    own = link_detached_envs(install_dir, env_dir, "wgsextract-cli-123")
    other = other_project_env(env_dir, tmp_path)
    write_layout(
        install_dir,
        pixi_cache_dir=cache_dir,
        pixi_env_dir=env_dir,
        created_pixi_cache_dir=cache_dir,
    )

    run_uninstall(install_dir / "uninstall.sh", "--yes")

    assert not own.exists()
    assert (other / "keep.txt").is_file()


def test_1246_uninstall_keeps_envs_of_the_install_a_copy_links_to(
    tmp_path: Path,
) -> None:
    original = make_fake_install(tmp_path / "original")
    env_dir = tmp_path / "shared-envs"
    original_envs = link_detached_envs(original, env_dir, "wgsextract-cli-123")
    copy = make_fake_install(tmp_path / "copy")
    (copy / "app" / ".pixi").mkdir()
    (copy / "app" / ".pixi" / "envs").symlink_to(
        original_envs / "envs", target_is_directory=True
    )

    result = run_uninstall(
        copy / "uninstall.sh", "--yes", "--pixi-env-dir", str(env_dir)
    )

    assert "environments were not found" in result.stdout
    assert (original_envs / "envs" / "default" / "keep.txt").is_file()
    assert not copy.exists()


@pytest.mark.parametrize("env_root", ["recorded-inside-cache", "elsewhere"])
def test_1246_uninstall_keeps_an_installer_created_cache_holding_other_envs(
    tmp_path: Path, env_root: str
) -> None:
    install_dir = make_fake_install(tmp_path)
    cache_dir = tmp_path / "pixi-cache"
    cache_dir.mkdir()
    layout: dict[str, Path] = {
        "pixi_cache_dir": cache_dir,
        "created_pixi_cache_dir": cache_dir,
    }
    if env_root == "recorded-inside-cache":
        # The recorded env root is in the cache, but a different one is named.
        layout["pixi_env_dir"] = cache_dir / "envs"
        other = other_project_env(cache_dir / "envs", tmp_path)
        args = ["--keep-pixi-envs", "--pixi-env-dir", str(tmp_path / "other-envs")]
    else:
        # Another project later kept detached environments in the cache.
        other = other_project_env(cache_dir / "later-envs", tmp_path)
        args = []
    write_layout(install_dir, **layout)

    result = run_uninstall(install_dir / "uninstall.sh", "--yes", *args)

    assert "Keeping the Pixi cache, which holds" in result.stdout
    assert (other / "keep.txt").is_file()
    assert not install_dir.exists()


def test_1246_uninstall_scans_a_cache_named_like_pixis_cached_envs(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    cache_dir = tmp_path / "cached-envs-v0"
    other = other_project_env(cache_dir / "envs", tmp_path)
    write_layout(
        install_dir, pixi_cache_dir=cache_dir, created_pixi_cache_dir=cache_dir
    )

    run_uninstall(install_dir / "uninstall.sh", "--yes")

    assert (other / "keep.txt").is_file()


def test_1246_keep_pixi_envs_keeps_a_cache_holding_the_recorded_env_dir(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    cache_dir = tmp_path / "pixi-cache"
    env_dir = cache_dir / "envs"
    env_dir.mkdir(parents=True)
    (env_dir / "notes.txt").write_text("kept\n", encoding="utf-8")
    write_layout(
        install_dir,
        pixi_cache_dir=cache_dir,
        pixi_env_dir=env_dir,
        created_pixi_cache_dir=cache_dir,
    )

    result = run_uninstall(install_dir / "uninstall.sh", "--yes", "--keep-pixi-envs")

    assert "holds the kept Pixi envs folder" in result.stdout
    assert (env_dir / "notes.txt").is_file()


def test_1246_uninstall_keeps_its_envs_when_they_hold_the_kept_cache(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    env_dir = tmp_path / "envs"
    own = link_detached_envs(install_dir, env_dir, "wgsextract-cli-123")
    cache_dir = own / "cache"
    (cache_dir / "pkgs").mkdir(parents=True)
    write_layout(install_dir, pixi_cache_dir=cache_dir, pixi_env_dir=env_dir)

    result = run_uninstall(install_dir / "uninstall.sh", "--yes", "--keep-pixi-cache")

    assert f"{own} (this install's Pixi environments)" not in result.stdout
    assert (cache_dir / "pkgs").is_dir()


def test_1246_keep_flags_do_not_claim_to_keep_pixi_dirs_inside_the_install(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)

    result = run_uninstall(
        install_dir / "uninstall.sh",
        "--dry-run",
        "--keep-pixi-envs",
        "--keep-pixi-cache",
    )

    assert "--keep-pixi-envs does not keep" in result.stdout
    assert "--keep-pixi-cache does not keep" in result.stdout
    assert "Keeping Pixi environments" not in result.stdout
    assert "Keeping Pixi cache" not in result.stdout


def test_1246_prompt_lists_config_and_pixi_home_it_will_remove(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    home = tmp_path / "home"
    pixi_home = make_fake_pixi_home(home)
    workspace = home / "wgs-extract"
    workspace.mkdir()
    (workspace / "config.toml").write_text("cpu_threads = 2\n")
    (workspace / ".users-workspace").write_text("WGS Extract users workspace\n")

    result = run_uninstall(
        install_dir / "uninstall.sh",
        "--remove-config",
        "--remove-pixi",
        env={"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")},
        input_text="n\n",
    )

    prompt = result.stdout.split("Continue?")[0]
    assert str(workspace / "config.toml") in prompt
    assert str(pixi_home) in prompt
    assert (workspace / "config.toml").exists()


def test_uninstall_refuses_external_launcher_that_is_not_generated(
    tmp_path: Path,
) -> None:
    install_dir = make_fake_install(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "wgsextract"
    launcher.write_text("#!/bin/sh\necho unrelated\n", encoding="utf-8")

    result = subprocess.run(
        [SH, str(install_dir / "uninstall.sh"), "--yes"],
        env={
            **os.environ,
            "WGSEXTRACT_BIN_DIR": str(bin_dir),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "does not look like WGS Extract CLI" in result.stderr
    assert launcher.exists()
    assert install_dir.exists()


@pytest.mark.parametrize("cache_dir", ["/", "/usr/../usr"])
def test_uninstall_refuses_unsafe_external_directory(
    tmp_path: Path, cache_dir: str
) -> None:
    install_dir = make_fake_install(tmp_path)
    write_layout(
        install_dir, pixi_cache_dir=cache_dir, created_pixi_cache_dir=cache_dir
    )

    result = subprocess.run(
        [SH, str(install_dir / "uninstall.sh"), "--yes"],
        env=os.environ,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "Refusing to remove unsafe directory" in result.stderr
    assert install_dir.exists()


def test_uninstall_refuses_symlinked_external_directory(tmp_path: Path) -> None:
    install_dir = make_fake_install(tmp_path)
    victim_dir = tmp_path / "victim"
    cache_dir = victim_dir / "cache"
    link_dir = tmp_path / "link"
    cache_dir.mkdir(parents=True)
    (cache_dir / "keep.txt").write_text("do not delete\n", encoding="utf-8")
    link_dir.symlink_to(victim_dir, target_is_directory=True)
    linked_cache = link_dir / "cache"
    write_layout(
        install_dir, pixi_cache_dir=linked_cache, created_pixi_cache_dir=linked_cache
    )

    result = subprocess.run(
        [SH, str(install_dir / "uninstall.sh"), "--yes"],
        env=os.environ,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "Refusing to remove directory through a symlink" in result.stderr
    assert (cache_dir / "keep.txt").exists()
    assert install_dir.exists()
