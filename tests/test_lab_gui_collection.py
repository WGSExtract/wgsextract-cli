import os
import shutil
import subprocess
from pathlib import Path

import pytest

GUEST = Path(__file__).resolve().parents[1] / "scripts" / "installlab" / "guest"


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
def test_1677_gui_actions_find_the_runtime_installed_by_packaged_setup(
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
        'process.stdout.write(fs.readFileSync(path.join(process.env.SUPPORT, name, "runtime", "cli"), "utf8"));\n',
        encoding="utf-8",
    )
    support = tmp_path / "app support"
    app_data = support / "WGSExtract-gui"
    (app_data / "runtime").mkdir(parents=True)
    (app_data / "runtime" / "cli").write_text("installed CLI", encoding="utf-8")
    env = {
        **os.environ,
        "SUPPORT": str(support),
        "APP_DATA": str(app_data),
        "APP": str(app),
    }
    env.pop("WGSEXTRACT_GUI_APP_DATA_NAME", None)
    if windows:
        text = (GUEST / "windows/gui-scenario.ps1").read_text()
        start = text.index("$NewWorkspace = ")
        launch = text[start : text.index("# The oracle reads outputs", start)]
        script = tmp_path / "run-actions.ps1"
        script.write_text(
            '$ErrorActionPreference = "Stop"; $InstallDir = $env:APP; '
            '$AppData = $env:APP_DATA; $AppIdentifier = "legacy"; '
            "$R = $UsersWorkspace = $OldWorkspace = $Workspace = $InstallDir; $UsersArgs = @(); "
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
            'APP_DIR="$APP"; WORKSPACE="$APP"; USERS_WORKSPACE="$APP"; '
            'R="$APP"; APP_ID=legacy; BUNDLE_ID=legacy; unset LAB_ARCHIVE_URL LAB_CLI_ARCHIVE_URL\n'
            + helpers
            + "\n"
            + launch
            + "}\napp_actions",
        ]

    result = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "installed CLI"


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
