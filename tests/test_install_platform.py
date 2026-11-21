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
    ("step", "detached"),
    [
        ("install", False),
        ("deps", False),
        ("deps", True),
        ("concurrent", False),
        ("finalization", False),
        ("layout", False),
    ],
    ids=[
        "install-fails",
        "verification-fails",
        "detached-verification-fails",
        "concurrent-upgrade",
        "finalization-fails",
        "optional-envs-cannot-relocate",
    ],
)
def test_1211_failed_upgrade_keeps_the_previous_app(
    tmp_path: Path, step: str, detached: bool
) -> None:
    payload = tmp_path / "source"
    payload.mkdir()
    (payload / "pixi.toml").write_text(
        '[workspace]\nplatforms = ["linux-64", "linux-aarch64", "osx-arm64", "osx-64"]\n'
    )
    shutil.copy(ROOT / "uninstall.sh", payload / "uninstall.sh")
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="wgsextract-cli")

    installation = tmp_path / "installation"
    app = installation / "app"
    (app / "reference").mkdir(parents=True)
    (app / "reference/keep.fa").write_text("user reference")
    (app / "pixi.toml").write_text("previous manifest")
    (installation / "wgsextract").write_text("previous launcher")
    (installation / "uninstall.sh").write_text("#!/bin/sh\n# previous uninstaller\n")
    install_record = installation / "install-manifest.json"
    if step == "finalization":
        install_record.mkdir()
        install_record /= "keep"
    install_record.write_text("previous install record")
    local_envs = app / ".pixi/envs"
    envs = tmp_path / "detached/envs" if detached else local_envs
    (envs / "default").mkdir(parents=True)
    (envs / "default/keep").write_text("working environment")
    if detached:
        local_envs.parent.mkdir()
        local_envs.symlink_to(envs, target_is_directory=True)
    if step in {"finalization", "layout"}:
        (local_envs / "gatk").mkdir()
        (local_envs / "gatk/keep").write_text("working optional environment")

    binary = tmp_path / "pixi"
    candidate_detached = tmp_path / "detached/envs" if step == "layout" else envs
    create_env = (
        f'ln -s "{candidate_detached}" .pixi/envs && mkdir -p "{candidate_detached}/default"'
        if detached or step == "layout"
        else "mkdir -p .pixi/envs/default"
    )
    second_log = tmp_path / "second-upgrade.log"
    second_upgrade = ""
    if step == "concurrent":
        other_pixi = tmp_path / "working-pixi"
        other_pixi.write_text("#!/bin/sh\nexit 0\n")
        other_pixi.chmod(0o755)
        second_upgrade = (
            f'  PIXI="{other_pixi}" sh "{ROOT / "install.sh"}" > "{second_log}" 2>&1 '
            f'|| echo "$?" >> "{second_log}"\n'
            "  echo 'injected concurrent failure' >&2; exit 42\n"
        )
    binary.write_text(
        "#!/bin/sh\nset -eu\n"
        'if [ "$1" = install ]; then\n'
        f"  {create_env}\n"
        "  printf partial > .pixi/envs/default/keep\n"
        f"{second_upgrade}"
        f"  [ '{step}' != install ] || {{ echo 'injected install failure' >&2; exit 42; }}\n"
        f"elif [ \"$3\" = deps ] && [ '{step}' = deps ]; then\n"
        "  echo 'injected deps failure' >&2; exit 42\n"
        "fi\n"
    )
    binary.chmod(0o755)
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("WGSEXTRACT_")},
        "HOME": str(tmp_path),
        "PIXI": str(binary),
        "WGSEXTRACT_INSTALL_DIR": str(installation),
        "WGSEXTRACT_ARCHIVE_URL": str(archive),
        "WGSEXTRACT_NO_OPEN": "1",
    }
    if detached or step == "layout":
        env["WGSEXTRACT_PIXI_ENV_DIR"] = str(candidate_detached.parent)
    if detached:
        envs.parent.chmod(0o555)
    try:
        result = subprocess.run(
            [str(SH), str(ROOT / "install.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        if detached:
            envs.parent.chmod(0o755)

    if step in {"finalization", "layout"}:
        assert result.returncode == 1, result.stderr
        assert (local_envs / "gatk/keep").read_text() == "working optional environment"
        assert (
            "install-manifest.json"
            if step == "finalization"
            else "optional Pixi environments"
        ) in result.stderr
    else:
        assert result.returncode == 42, result.stderr
        assert f"injected {step} failure" in result.stderr
    assert (app / "pixi.toml").read_text() == "previous manifest"
    assert (app / "reference/keep.fa").read_text() == "user reference"
    assert (local_envs / "default/keep").read_text() == "working environment"
    assert (installation / "wgsextract").read_text() == "previous launcher"
    assert "previous uninstaller" in (installation / "uninstall.sh").read_text()
    assert install_record.read_text() == "previous install record"
    assert not list(installation.glob(".source-download.*"))
    assert not list(installation.glob(".wgse-launcher.*"))
    assert not (installation / ".app-install-lock").exists()
    if step == "concurrent":
        log = second_log.read_text()
        assert "Could not lock" in log
        assert log.splitlines()[-1] == "1"
    if detached:
        assert list(envs.iterdir()) == [envs / "default"]


@pytest.mark.parametrize("location", ["explicit", "path", "home"])
def test_1138_installer_rejects_old_pixi_before_replacing_existing_app(
    tmp_path: Path, location: str
) -> None:
    payload = tmp_path / "source"
    payload.mkdir()
    (payload / "pixi.toml").write_text(
        '[workspace]\nrequires-pixi = ">=0.82.0"\nplatforms = ["linux-64", "linux-aarch64", "osx-arm64", "osx-64"]\n'
    )
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="wgsextract-cli")
    home = tmp_path / "home"
    binary = home / ".pixi/bin/pixi"
    binary.parent.mkdir(parents=True)
    binary.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "pixi 0.69.0"; else exit 99; fi\n'
    )
    binary.chmod(0o755)
    installation = tmp_path / "installation"
    app = installation / "app"
    app.mkdir(parents=True)
    (app / "keep").write_text("existing app")
    env = {
        **{
            key: value
            for key, value in os.environ.items()
            if not key.startswith("WGSEXTRACT_")
        },
        "HOME": str(home),
        "PATH": os.defpath,
        "PIXI": "",
        "WGSEXTRACT_INSTALL_DIR": str(installation),
        "WGSEXTRACT_ARCHIVE_URL": str(archive),
    }
    if location == "explicit":
        env["PIXI"] = str(binary)
    elif location == "path":
        env["PATH"] = str(binary.parent) + os.pathsep + os.defpath
    result = subprocess.run(
        [str(SH), str(ROOT / "install.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert ">=0.82.0" in result.stderr
    assert "0.69.0" in result.stderr
    assert "self-update" in result.stderr
    assert str(binary) in result.stderr
    assert (app / "keep").read_text() == "existing app"
    assert "0.69.0" in binary.read_text()


@pytest.mark.parametrize(
    "requirement",
    [None, 'requires-pixi = "unsupported"', 'requires-pixi = ">=0.82"'],
    ids=["no-minimum", "invalid-constraint", "incomplete-version"],
)
def test_1138_pinned_release_without_pixi_minimum_installs_but_bad_constraint_fails(
    tmp_path: Path, requirement: str | None
) -> None:
    payload = tmp_path / "source"
    payload.mkdir()
    manifest = '[workspace]\nplatforms = ["linux-64", "linux-aarch64", "osx-arm64", "osx-64"]\n'
    if requirement:
        manifest += requirement + "\n"
    (payload / "pixi.toml").write_text(manifest)
    shutil.copy(ROOT / "uninstall.sh", payload / "uninstall.sh")
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="wgsextract-cli")
    binary = tmp_path / "pixi"
    binary.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "pixi 0.69.0"; fi\n'
    )
    binary.chmod(0o755)
    installation = tmp_path / "installation"
    app = installation / "app"
    app.mkdir(parents=True)
    (app / "keep").write_text("existing app")
    result = subprocess.run(
        [str(SH), str(ROOT / "install.sh")],
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if not key.startswith("WGSEXTRACT_")
            },
            "HOME": str(tmp_path),
            "PIXI": str(binary),
            "WGSEXTRACT_INSTALL_DIR": str(installation),
            "WGSEXTRACT_RELEASE_TAG": "v0.6.5",
            "WGSEXTRACT_ARCHIVE_URL": str(archive),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    if requirement is None:
        assert result.returncode == 0, result.stderr
        assert (app / "pixi.toml").read_text() == manifest
        assert (installation / "wgsextract").is_file()
    else:
        assert result.returncode == 1
        assert "Cannot read the minimum Pixi version" in result.stderr
        assert (app / "keep").read_text() == "existing app"


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
        pixi.write_text(
            '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "pixi 0.82.0"; exit; fi\n'
            f'printf "%s\\n" "$*" >> \'{pixi_marker}\'\n'
        )
        pixi.chmod(0o755)

    payload = tmp_path / "payload"
    payload.mkdir()
    manifest_text = '[workspace]\nrequires-pixi = ">=0.82.0"\n' + declaration + "\n"
    (payload / "pixi.toml").write_text(manifest_text)
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
        assert (app / "pixi.toml").read_text() == manifest_text
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
