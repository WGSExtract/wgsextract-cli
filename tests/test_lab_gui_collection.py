import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

GUEST = Path(__file__).resolve().parents[1] / "scripts" / "installlab" / "guest"


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
def test_1677_1688_gui_actions_use_the_installed_runtime_and_stage_ploidy_in_both_lanes(
    tmp_path: Path, platform: str
) -> None:
    windows = platform == "windows"
    shell = shutil.which("pwsh" if windows else "sh")
    node = shutil.which("node")
    if not shell or not node or (os.name == "nt" and not windows):
        pytest.skip(f"{platform} runner needs its shell and Node")

    app = tmp_path / "installed app"
    resources = app / "Contents" / "Resources" if platform == "macos" else app
    executable = resources / ("node/node.exe" if windows else "node/bin/node")
    executable.parent.mkdir(parents=True)
    if os.name == "nt":
        shutil.copyfile(node, executable)
    else:
        executable.symlink_to(node)
    runner = resources / "app/dist/web/src/server/bundle-test-cli.js"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        'const fs = require("node:fs"), path = require("node:path");\n'
        'const name = process.env.WGSEXTRACT_GUI_APP_DATA_NAME ?? "WGSExtract-gui-dev";\n'
        'const cli = fs.readFileSync(path.join(process.env.SUPPORT, name, "runtime", "cli"), "utf8");\n'
        "const args = process.argv.slice(2);\n"
        'const workspaceFlag = args.includes("--users-workspace") ? "--users-workspace" : "--workspace";\n'
        "const workspace = args[args.indexOf(workspaceFlag) + 1];\n"
        'const ploidy = fs.readFileSync(path.join(workspace, "reference", "ploidy.txt"));\n'
        'process.stdout.write(JSON.stringify([cli, ploidy.toString("hex")]));\n',
        encoding="utf-8",
    )
    guest = tmp_path / "guest"
    (guest / "windows").mkdir(parents=True)
    fixture = GUEST / "hg19-mini.ploidy.txt"
    shutil.copyfile(fixture, guest / fixture.name)
    support = tmp_path / "app support"
    app_data = support / "WGSExtract-gui"
    (app_data / "runtime").mkdir(parents=True)
    (app_data / "runtime" / "cli").write_text("installed CLI", encoding="utf-8")
    env = {
        **os.environ,
        "SUPPORT": str(support),
        "APP_DATA": str(app_data),
        "APP": str(app),
        "HERE": str(guest),
        "APP_LIFECYCLE": str(GUEST / "app-lifecycle.sh"),
    }
    env.pop("WGSEXTRACT_GUI_APP_DATA_NAME", None)
    env.pop("LAB_CLI_ARCHIVE_URL", None)
    env.pop("LAB_ARCHIVE_URL", None)
    if windows:
        text = (GUEST / "windows/gui-scenario.ps1").read_text()
        start = text.index("# everything a click would start is.")
        launch = text[start : text.index("# The oracle reads outputs", start)]
        script = guest / "windows/run-actions.ps1"
        script.write_text(
            '$ErrorActionPreference = "Stop"; $InstallDir = $env:APP; '
            '$AppData = $env:APP_DATA; $AppIdentifier = "legacy"; '
            "$R = $InstallDir; $UsersArgs = @(); "
            "$Workspace = $UsersWorkspace = $env:USERS_WORKSPACE; $OldWorkspace = $env:OLD_WORKSPACE; "
            "$lab = @{ LAB_CLI_ARCHIVE_URL = $env:LAB_CLI_ARCHIVE_URL }; "
            "function Step($name, $command, $cwd) { & $command[0] $command[1..($command.Length-1)] }\n"
            + launch
            + "\nexit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        command = [shell, "-NoProfile", "-File", str(script)]
    else:
        text = (GUEST / f"gui-{platform}.sh").read_text()
        start = text.index("app_actions() {")
        launch = text[start : text.index("    rc=$?", start)]
        helpers = ""
        if platform == "macos":
            helpers = "\n".join(
                text[text.index(f"{name}() {{") :].split("\n}\n", 1)[0] + "\n}"
                for name in ("use_workspace", "is_tauri")
            )
        command = [
            shell,
            "-c",
            'APP_DIR="$APP"; WORKSPACE="$USERS_WORKSPACE"; R="$APP"; APP_ID=legacy; BUNDLE_ID=legacy\n'
            '. "$APP_LIFECYCLE"\n' + helpers + "\n" + launch + "}\napp_actions",
        ]

    split_marker = app_data / "BundleWorkspaces/wgs-extract/.bundle-workspace"
    split_marker.parent.mkdir(parents=True)
    for lane, layout in (("pinned", "legacy"), ("pinned", "split"), ("tree", "split")):
        workspace = tmp_path / f"{lane}-{layout}-users"
        legacy = tmp_path / f"{lane}-{layout}-legacy"
        if layout == "split":
            split_marker.touch()
        else:
            legacy.mkdir()
            (legacy / ".bundle-workspace").touch()
        lane_env = {
            **env,
            "USERS_WORKSPACE": str(workspace),
            "OLD_WORKSPACE": str(legacy),
        }
        if lane == "tree":
            archive_var = (
                "LAB_ARCHIVE_URL" if platform == "macos" else "LAB_CLI_ARCHIVE_URL"
            )
            lane_env[archive_var] = "https://example.invalid/tree-archive"
        result = subprocess.run(
            command, env=lane_env, capture_output=True, text=True, timeout=15
        )
        assert result.returncode == 0, f"{lane} {layout}: {result.stderr}"
        assert json.loads(result.stdout) == [
            "installed CLI",
            fixture.read_bytes().hex(),
        ]
        if layout == "legacy":
            assert not workspace.exists()


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
@pytest.mark.parametrize(
    ("workspace", "folder"),
    [
        ("bundle", "genomes"),
        ("bundle", "datasets"),
        ("bundle", "output"),
        ("users", "mygenomes"),
        ("users", "output"),
    ],
)
def test_801_gui_collects_info_from_released_and_tree_plan_locations(
    tmp_path: Path, platform: str, workspace: str, folder: str
) -> None:
    shell = shutil.which("pwsh" if platform == "windows" else "sh")
    if shell is None:
        pytest.skip(f"{platform} shell not installed")
    bundle = tmp_path / "bundle workspace"
    users = tmp_path / "users workspace"
    results = tmp_path / "results"
    (results / "workload").mkdir(parents=True)
    origin = (bundle if workspace == "bundle" else users) / folder / "sample"
    origin.mkdir(parents=True)
    info = origin / "HG00096.hg19-mini.bam.wgse_info.json"
    info.write_text('{"sample": "HG00096", "coverage": 30}')
    if platform == "windows":
        text = (GUEST / "windows" / "gui-scenario.ps1").read_text()
        start = 'if (Test-Path -LiteralPath (Join-Path $Workspace "lab-output")) {'
        collector = (
            start + text.split(start, 1)[1].split('Save-Snapshot "after_install"', 1)[0]
        )
        script = tmp_path / "collect.ps1"
        script.write_text(
            "$Workspace, $UsersWorkspace, $R = $args\n" + collector,
            encoding="utf-8",
        )
        command = [
            shell,
            "-NoProfile",
            "-File",
            str(script),
            str(bundle),
            str(users),
            str(results),
        ]
        env = os.environ.copy()
    else:
        text = (GUEST / f"gui-{platform}.sh").read_text()
        start = '    if [ -d "$WORKSPACE/lab-output" ]; then'
        collector = start + text.split(start, 1)[1].split("    return $rc", 1)[0]
        command = [shell, "-c", collector]
        env = {
            **os.environ,
            "WORKSPACE": str(bundle),
            "USERS_WORKSPACE": str(users),
            "R": str(results),
        }
    # Missing optional search roots need not make find succeed; the copied
    # metrics are the evidence that the judge requires.
    subprocess.run(command, env=env, capture_output=True, check=False)
    collected = results / "workload" / "info" / info.name
    assert collected.read_text() == info.read_text()
