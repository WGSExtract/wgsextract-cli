import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(
    SH is None or sys.platform == "win32",
    reason="POSIX installer platform guard",
)


@pytest.mark.parametrize(
    "compiler",
    ["usr/bin/clang", "Toolchains/XcodeDefault.xctoolchain/usr/bin/clang", None],
    ids=["command-line-tools", "full-xcode", "missing-compiler"],
)
def test_issue_801_macos_preflight_does_not_initialize_developer_state(
    tmp_path: Path,
    compiler: str | None,
) -> None:
    developer = tmp_path / "selected developer"
    if compiler:
        clang = developer / compiler
        clang.parent.mkdir(parents=True)
        clang.write_text("#!/bin/sh\nexit 0\n")
        clang.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    select = tmp_path / "xcode-select"
    select.write_text(f"#!/bin/sh\nprintf '%s\\n' '{developer}'\n")
    select.chmod(0o755)
    xcrun = tmp_path / "xcrun"
    xcrun.write_text('#!/bin/sh\nmkdir -p "$HOME/Library/Developer/CoreSimulator"\n')
    xcrun.chmod(0o755)
    source = (ROOT / "install.sh").read_text()
    preflight = source.split("verify_xcode_command_line_tools() {", 1)[1].split(
        "\nresolve_latest_release_tag()", 1
    )[0]
    preflight = preflight.replace("/usr/bin/xcode-select", f"'{select}'").replace(
        "/usr/bin/xcrun", f"'{xcrun}'"
    )

    result = subprocess.run(
        [
            str(SH),
            "-c",
            "log() { printf '%s\\n' \"$*\"; }\nOS_NAME=Darwin\n"
            "verify_xcode_command_line_tools() {"
            + preflight
            + "\nverify_xcode_command_line_tools",
        ],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == (0 if compiler else 1), result.stderr
    assert not list(home.iterdir())
    if compiler:
        assert str(developer) in result.stdout
    else:
        assert "clang is not available" in result.stderr


@pytest.mark.parametrize("architecture", ["aarch64", "arm64"])
@pytest.mark.parametrize(
    "parent_path", [True, False], ids=["with-path", "without-path"]
)
def test_linux_arm_reaches_normal_dependency_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    architecture: str,
    parent_path: bool,
) -> None:
    if not parent_path:
        monkeypatch.delenv("PATH", raising=False)
    commands = tmp_path / "commands"
    commands.mkdir()
    uname = commands / "uname"
    uname.write_text(
        f'#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo {architecture};; esac\n',
        encoding="utf-8",
        newline="\n",
    )
    uname.chmod(0o755)
    marker = tmp_path / "unexpected-command"
    for name in ("curl", "tar", "gzip", "pixi"):
        command = commands / name
        command.write_text(
            f"#!/bin/sh\nprintf invoked > '{marker}'\nexit 99\n",
            encoding="utf-8",
            newline="\n",
        )
        command.chmod(0o755)
    installation = tmp_path / "installation"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": os.pathsep.join(
            path for path in (str(commands), os.environ.get("PATH", os.defpath)) if path
        ),
        "WGSEXTRACT_INSTALL_DIR": str(installation),
        "WGSEXTRACT_ARCHIVE_URL": "https://example.invalid/source.tar.gz",
        "PIXI": str(tmp_path / "missing-pixi"),
    }
    result = subprocess.run(
        [str(SH), str(ROOT / "install.sh")],
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "PIXI is set but is not executable" in result.stderr
    assert "Native Linux ARM64" not in result.stderr
    assert not marker.exists()
    assert not installation.exists()


@pytest.mark.parametrize("architecture", ["aarch64", "arm64"])
def test_linux_arm_pixi_asset_is_native(tmp_path: Path, architecture: str) -> None:
    commands = tmp_path / "commands"
    commands.mkdir()
    uname = commands / "uname"
    uname.write_text(
        f'#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo {architecture};; esac\n'
    )
    uname.chmod(0o755)
    source = (ROOT / "install.sh").read_text()
    function = source.split("pixi_asset_name() {", 1)[1].split(
        "\nmake_staging_directory()", 1
    )[0]
    result = subprocess.run(
        [str(SH), "-c", "pixi_asset_name() {" + function + "\npixi_asset_name"],
        env={"PATH": str(commands)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "pixi-aarch64-unknown-linux-musl.tar.gz"


@pytest.mark.parametrize("architecture", ["aarch64", "arm64"])
@pytest.mark.parametrize(
    ("declaration", "supported", "existing_app"),
    [
        ('platforms = ["linux-64"]', False, False),
        ('platforms = ["linux-64"]', False, True),
        ('platforms = ["linux-aarch64-other"]', False, True),
        ('platforms = ["linux-64"] # "linux-aarch64"', False, True),
        (
            'platforms = ["linux-64"]\n[feature.optional]\n'
            'platforms = ["linux-aarch64"]',
            False,
            True,
        ),
        ('platforms = ["linux-64", "linux-aarch64", "win-64"]', True, False),
        ('platforms = ["linux-64", "linux-aarch64", "win-64"]', True, True),
        ("platforms = ['linux-aarch64', 'linux-64']", True, True),
        ('platforms = [\n "linux-aarch64",\n]', True, False),
        ('platforms = ["linux-64",\n "linux-aarch64"]', True, True),
        (
            "platforms = [ # a comment with ]\n"
            ' "linux-64", # another ]\n "linux-aarch64",\n]',
            True,
            False,
        ),
        ('platforms = [\n "linux-64",\n]', False, False),
        (
            'platforms = [\n "linux-aarch64-other",\n'
            ' # "linux-aarch64"\n]\n[feature.optional]\n'
            'platforms = ["linux-aarch64"]',
            False,
            True,
        ),
        ('[feature.optional]\nplatforms = ["linux-aarch64"]', False, False),
    ],
)
def test_linux_arm_validates_downloaded_ref_before_pixi_or_app_changes(
    tmp_path: Path,
    architecture: str,
    declaration: str,
    supported: bool,
    existing_app: bool,
) -> None:
    commands = tmp_path / "commands"
    commands.mkdir()
    uname = commands / "uname"
    uname.write_text(
        f'#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo {architecture};; esac\n'
    )
    uname.chmod(0o755)
    network_marker = tmp_path / "network-invoked"
    curl = commands / "curl"
    curl.write_text(f"#!/bin/sh\nprintf invoked > '{network_marker}'\nexit 99\n")
    curl.chmod(0o755)
    pixi_marker = tmp_path / "pixi-invoked"
    if supported:
        pixi = commands / "pixi"
        pixi.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> \'{pixi_marker}\'\n')
        pixi.chmod(0o755)

    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pixi.toml").write_text("[workspace]\n" + declaration + "\n")
    shutil.copy(ROOT / "uninstall.sh", payload / "uninstall.sh")
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="wgsextract-cli")

    installation = tmp_path / "installation"
    app = installation / "app"
    launcher = installation / "wgsextract"
    if existing_app:
        app.mkdir(parents=True)
        (app / "existing-app").write_text("preserve until payload is validated")
        launcher.write_text("existing launcher")
    before = {
        path.relative_to(installation): path.read_bytes() if path.is_file() else None
        for path in installation.rglob("*")
    }
    home = tmp_path / "home"
    home.mkdir()
    ref = "main" if supported else "v0.6.5"
    env = {
        **{
            key: value
            for key, value in os.environ.items()
            if not key.startswith("WGSEXTRACT_")
        },
        "HOME": str(home),
        "PATH": os.pathsep.join((str(commands), os.defpath)),
        "WGSEXTRACT_INSTALL_DIR": str(installation),
        "WGSEXTRACT_ARCHIVE_URL": str(archive),
        "WGSEXTRACT_REF": ref,
        "PIXI": "",
    }
    result = subprocess.run(
        [str(SH), str(ROOT / "install.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert not network_marker.exists()
    assert not list(installation.glob(".source-download*"))
    if supported:
        assert result.returncode == 0, result.stderr
        assert "install --locked" in pixi_marker.read_text()
        assert (app / "pixi.toml").read_text() == "[workspace]\n" + declaration + "\n"
        assert launcher.read_text() != "existing launcher"
        assert (installation / "uninstall.sh").exists()
        assert (installation / "install-manifest.json").exists()
    else:
        assert result.returncode == 1
        assert ref in result.stderr
        assert "No dependencies were installed" in result.stderr
        assert "WGSEXTRACT_REF=main" in result.stderr
        assert not pixi_marker.exists()
        assert not (home / ".pixi").exists()
        if not existing_app:
            assert not installation.exists()
        assert {
            path.relative_to(installation): path.read_bytes()
            if path.is_file()
            else None
            for path in installation.rglob("*")
        } == before


def _run_install_with_pixi(
    tmp_path: Path, version: str, *, explicit: bool
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """Install a release requiring Pixi >=0.82.0 with a Pixi of ``version``."""
    commands = tmp_path / "commands"
    commands.mkdir()
    # Downloads are the boundary: they fail, so an embedded Pixi never arrives.
    curl = commands / "curl"
    curl.write_text("#!/bin/sh\nexit 22\n")
    curl.chmod(0o755)
    home = tmp_path / "home"
    pixi_dir = tmp_path / "explicit" if explicit else home / ".pixi" / "bin"
    pixi_dir.mkdir(parents=True)
    calls = tmp_path / "pixi-calls"
    pixi = pixi_dir / "pixi"
    pixi.write_text(
        f'#!/bin/sh\nif [ "$1" = --version ]; then echo "pixi {version}"; exit 0; fi\n'
        f"printf '%s\\n' \"$*\" >> '{calls}'\n"
    )
    pixi.chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pixi.toml").write_text(
        '[workspace]\nrequires-pixi = ">=0.82.0"\nplatforms = ["linux-64", '
        '"linux-aarch64", "osx-arm64", "osx-64"]\n'
    )
    shutil.copy(ROOT / "uninstall.sh", payload / "uninstall.sh")
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="wgsextract-cli")
    installation = tmp_path / "installation"
    (installation / "app").mkdir(parents=True)
    (installation / "app" / "existing-app").write_text("keep")
    env = {
        **{
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("WGSEXTRACT_", "PIXI"))
        },
        "HOME": str(home),
        "PATH": os.pathsep.join((str(commands), os.defpath)),
        "WGSEXTRACT_INSTALL_DIR": str(installation),
        "WGSEXTRACT_ARCHIVE_URL": str(archive),
        "WGSEXTRACT_REF": "main",
        "WGSEXTRACT_NO_OPEN": "1",
        "PIXI": str(pixi) if explicit else "",
    }
    result = subprocess.run(
        [str(SH), str(ROOT / "install.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, calls, installation


def test_issue_1138_old_user_pixi_is_not_used_to_install(tmp_path: Path) -> None:
    result, calls, installation = _run_install_with_pixi(
        tmp_path, "0.69.0", explicit=False
    )
    # Setup goes on to fetch its own Pixi instead of failing in `pixi install`.
    assert "0.69.0" in result.stdout
    assert ">=0.82.0" in result.stdout
    assert "Your Pixi is not changed" in result.stdout
    assert "Downloading Pixi from" in result.stdout
    assert "requires pixi" not in result.stderr
    assert not calls.exists()
    assert (installation / "app" / "existing-app").read_text() == "keep"


def test_issue_1138_old_explicit_pixi_fails_before_changes(tmp_path: Path) -> None:
    result, calls, installation = _run_install_with_pixi(
        tmp_path, "0.69.0", explicit=True
    )
    assert result.returncode == 1
    assert "0.69.0" in result.stderr
    assert ">=0.82.0" in result.stderr
    assert "pixi self-update" in result.stderr
    assert not calls.exists()
    assert (installation / "app" / "existing-app").read_text() == "keep"


@pytest.mark.parametrize("version", ["0.82.0", "0.100.0"])
def test_new_enough_user_pixi_installs_the_app(tmp_path: Path, version: str) -> None:
    result, calls, _ = _run_install_with_pixi(tmp_path, version, explicit=False)
    assert result.returncode == 0, result.stderr
    assert "install --locked" in calls.read_text()
