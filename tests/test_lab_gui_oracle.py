import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LAB_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "installlab"
PLIST = "/Users/runner/Library/Preferences/dev.guiforcli.embed.wgsextract.plist"
OTHER = "dir\t/Users/runner/Library/Application Support/dev.guiforcli.embed.wgsextract"


@pytest.fixture
def oracle(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(LAB_SCRIPTS))
    for name in ("oracle", "gui_oracle"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    import gui_oracle

    (tmp_path / "lab-run.json").write_text(json.dumps({"target": "host"}))
    return gui_oracle.GuiOracle(tmp_path, "nonce", {}, signed=True)


def save(oracle, prefs) -> None:
    data = plistlib.dumps(prefs, fmt=plistlib.FMT_BINARY)
    (oracle.results / "uninstall-preferences.plist").write_bytes(data)


def test_1065_mutation_evidence_excludes_unreadable_locks_and_guards(
    tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(LAB_SCRIPTS))
    import lab

    source = tmp_path / "results"
    reference = source / "workload" / "reference"
    reference.mkdir(parents=True)
    evidence = source / "evidence"
    evidence.mkdir()
    (evidence / "snp.rc").write_text("0\n")
    (reference / "chain.gz").write_bytes(b"chain data")
    locks = [reference / f"chain.gz{suffix}" for suffix in (".lock", ".lock.guard")]
    for path in locks:
        path.touch()
        path.chmod(0)
    try:
        destination = tmp_path / "mutant"
        lab.copy_mutation_evidence(source, destination)
        assert (destination / "evidence" / "snp.rc").read_text() == "0\n"
        assert (
            destination / "workload" / "reference" / "chain.gz"
        ).read_bytes() == b"chain data"
        assert not list(destination.rglob("*.lock"))
        assert not list(destination.rglob("*.guard"))
        assert all(path.exists() for path in locks)

        unreadable = evidence / "snp.rc"
        unreadable.chmod(0)
        if os.name != "nt" and not os.access(unreadable, os.R_OK):
            with pytest.raises(shutil.Error):
                lab.copy_mutation_evidence(source, tmp_path / "bad-mutant")
    finally:
        for path in [*locks, evidence / "snp.rc"]:
            path.chmod(0o600)


def test_an_empty_preferences_file_is_excused(oracle):
    save(oracle, {})
    added, excused = oracle.excuse_leftovers([f"file\t{PLIST}", OTHER])
    assert added == [OTHER]
    assert excused == [f"excused, empty (cfprefsd rewrote it): {PLIST}"]


def test_preferences_with_any_key_are_left_behind(oracle):
    save(oracle, {"NSWindow Frame main": "x"})
    assert oracle.excuse_leftovers([f"file\t{PLIST}"]) == ([f"file\t{PLIST}"], [])


def test_preferences_without_a_saved_copy_are_left_behind(oracle):
    assert oracle.excuse_leftovers([f"file\t{PLIST}"]) == ([f"file\t{PLIST}"], [])


def test_a_corrupt_copy_is_not_empty(oracle):
    (oracle.results / "uninstall-preferences.plist").write_bytes(b"not a plist")
    assert oracle.excuse_leftovers([f"file\t{PLIST}"]) == ([f"file\t{PLIST}"], [])


def test_other_platforms_excuse_nothing(oracle):
    import gui_oracle

    save(oracle, {})
    oracle.app = gui_oracle.WINDOWS
    assert oracle.excuse_leftovers([f"file\t{PLIST}"]) == ([f"file\t{PLIST}"], [])


@pytest.mark.parametrize("rendered", [True, False])
def test_tauri_macos_requires_the_current_apps_window_evidence(oracle, rendered):
    import gui_oracle

    (oracle.results / "lab-run.json").write_text(
        json.dumps({"target": "host", "macos_runtime": "tauri"})
    )
    # The old Swift window marker must not make a Tauri run pass.
    (oracle.results / "startup.txt").write_text("window_appeared_ms=42\n")
    (oracle.results / "app-stdout.txt").write_text(
        "metric webAppRendered_ms=42\n" if rendered else "metric windowShown_ms=42\n"
    )
    tauri = gui_oracle.GuiOracle(oracle.results, "nonce", {}, signed=True)
    tauri.check_app_reports()
    window = next(
        check for check in tauri.checks if check.name == "app.window_appeared"
    )
    assert window.ok is rendered
    assert "runner" not in tauri.app.steps


@pytest.mark.parametrize("runtime", ["swiftui", "tauri"])
def test_no_window_mutation_removes_the_active_runtime_evidence(oracle, runtime):
    import gui_oracle
    import lab

    (oracle.results / "lab-run.json").write_text(
        json.dumps({"target": "host", "macos_runtime": runtime})
    )
    (oracle.results / "startup.txt").write_text("window_appeared_ms=42\n")
    (oracle.results / "app-stdout.txt").write_text("metric webAppRendered_ms=42\n")
    lab.GUI_MUTATIONS["no-window"](oracle.results)
    current = gui_oracle.GuiOracle(oracle.results, "nonce", {}, signed=True)
    current.check_app_reports()
    window = next(
        check for check in current.checks if check.name == "app.window_appeared"
    )
    assert not window.ok


@pytest.mark.parametrize("runtime", ["swiftui", "tauri"])
def test_gatekeeper_off_mutation_is_checked_by_both_macos_runtimes(oracle, runtime):
    import gui_oracle
    import lab
    import macos_policy

    (oracle.results / "lab-run.json").write_text(
        json.dumps({"target": "host", "macos_runtime": runtime})
    )
    (oracle.results / "gatekeeper-status.txt").write_text("assessments enabled\n")
    (oracle.results / "gatekeeper-policy.json").write_text(
        json.dumps(dict.fromkeys(macos_policy.POLICY_KEYS, True))
    )
    current = gui_oracle.GuiOracle(oracle.results, "nonce", {}, signed=True)
    checks = {check.name: check.ok for check in current.run(oracle.results, [])}
    assert checks["baseline.gatekeeper_enabled"]
    assert checks["baseline.gatekeeper_policy"]

    lab.GUI_MUTATIONS["gatekeeper-off"](oracle.results)
    current = gui_oracle.GuiOracle(oracle.results, "nonce", {}, signed=True)
    checks = {check.name: check.ok for check in current.run(oracle.results, [])}
    assert not checks["baseline.gatekeeper_enabled"]


def test_tauri_preferences_mutation_uses_the_current_identifier(oracle):
    import gui_oracle
    import lab

    (oracle.results / "lab-run.json").write_text(
        json.dumps(
            {"target": "host", "host_platform": "darwin", "macos_runtime": "tauri"}
        )
    )
    (oracle.results / "inventory-after_uninstall.txt").write_text("")
    lab.GUI_MUTATIONS["preferences-kept"](oracle.results)
    current = gui_oracle.GuiOracle(oracle.results, "nonce", {}, signed=True)
    added = (oracle.results / "inventory-after_uninstall.txt").read_text().splitlines()
    assert added[0].endswith(current.app.emptied_preferences)
    assert current.excuse_leftovers(added) == (added, [])


@pytest.mark.parametrize(
    ("meta", "home"),
    [
        ({"target": "linux", "host_platform": "darwin"}, "/home/tester"),
        ({"target": "macos", "host_platform": "darwin"}, "/Users/admin"),
        ({"target": "host", "host_platform": "darwin", "home": "/Users/x"}, "/Users/x"),
        ({"target": "host", "host_platform": "linux", "home": "/home/x"}, "/home/x"),
    ],
)
def test_mutations_plant_the_run_machines_paths(tmp_path, monkeypatch, meta, home):
    # Where lab.py ran says nothing about a container's or VM's paths.
    monkeypatch.syspath_prepend(str(LAB_SCRIPTS))
    import lab

    (tmp_path / "lab-run.json").write_text(json.dumps(meta))
    assert lab._planted(tmp_path)["home"] == home


def _linux_run(results: Path, timeline: list[str] | None, signal_text: str) -> None:
    (results / "lab-run.json").write_text(json.dumps({"target": "linux"}))
    events = [
        {"type": "step-start", "step": {"id": "install-wgsextract"}},
        {"type": "output", "id": "install-wgsextract", "text": "Installing Pixi...\n"},
        {
            "type": "step-complete",
            "result": {
                "id": "install-wgsextract",
                "status": "failed",
                "exitCode": 1,
                "signal": None,
                "stderr": signal_text,
            },
        },
        {"type": "complete", "result": {"status": "failed", "results": []}},
    ]
    (results / "setup-events.ndjson").write_text(
        "".join(json.dumps(e) + "\n" for e in events)
    )
    if timeline is not None:
        (results / "timeline.tsv").write_text("".join(f"{t}\n" for t in timeline))


def _linux_checks(results: Path) -> dict[str, str]:
    import gui_oracle

    current = gui_oracle.GuiOracle(results, "nonce", {}, signed=False)
    current.check_app_reports()
    return {c.name: ("ok: " if c.ok else "FAIL: ") + c.detail for c in current.checks}


# Issue #448: the harness missed the just-launched app, ran the later steps
# beside its setup, and the app's Uninstall TERMed install.sh ("Terminated").
RACED = [
    "1000\tstep-start setup",
    "1010\tapp-launched 42",
    "1030\tsetup-events 0",
    "1040\tstep-end setup 1",
    "1050\tstep-start optional_tools",
    "1060\tstep-end optional_tools 1",
    "1070\tstep-start actions",
    "3000\tstep-end actions 1",
    "3010\tstep-start uninstall",
]


def test_a_setup_step_terminated_by_a_step_run_beside_it_names_that_step(oracle):
    _linux_run(oracle.results, RACED, "Terminated\n")
    checks = _linux_checks(oracle.results)
    assert checks["app.setup"].startswith("FAIL")
    assert "install-wgsextract got SIGTERM" in checks["app.setup"]
    # The scenario had stopped watching setup, so every step since is a suspect.
    assert "ran while setup was unwatched" in checks["app.setup"]
    assert (
        "scenario step 'uninstall', started while setup was running, whose app Uninstall stops"
        in checks["app.setup"]
    )
    assert checks["app.setup_alone"] == (
        "FAIL: the app was never seen to close; steps started while the app ran "
        "setup: optional_tools, actions, uninstall"
    )


def test_a_setup_step_terminated_when_the_harness_closed_the_app_blames_it(oracle):
    _linux_run(
        oracle.results,
        [
            "1000\tstep-start setup",
            "1010\tapp-launched 42",
            "5000\tsetup-events 2",
            "9000\tharness-closes-app setup did not finish within 3 hours",
            "9500\tsetup-events 4",
            "9600\tapp-exited 0",
            "9650\tapp-closed",
            "9700\tstep-end setup 1",
            "9800\tstep-start optional_tools",
        ],
        "Terminated\n",
    )
    checks = _linux_checks(oracle.results)
    assert "the lab harness (harness-closes-app" in checks["app.setup"]
    assert checks["app.setup_alone"].startswith("ok: the app ran setup alone")


def test_a_setup_step_terminated_when_the_app_crashed_blames_the_app(oracle):
    # The harness closes what a crashed app left behind, but the events the app
    # wrote while dying are noted first, so the harness isn't blamed for them.
    _linux_run(
        oracle.results,
        [
            "1000\tstep-start setup",
            "1010\tapp-launched 42",
            "5000\tsetup-events 2",
            "9000\tapp-exited 139",
            "9001\tsetup-events 4",
            "9002\tharness-closes-app the app exited during setup",
            "9600\tapp-closed",
            "9700\tstep-end setup 1",
        ],
        "Terminated\n",
    )
    setup = _linux_checks(oracle.results)["app.setup"]
    assert "from the app, which exited (status 139)" in setup
    assert "harness" not in setup


def test_without_a_timeline_the_terminator_is_unknown_and_setup_alone_fails(oracle):
    _linux_run(oracle.results, None, "Killed\n")
    checks = _linux_checks(oracle.results)
    assert (
        "install-wgsextract got SIGKILL, from an unknown process" in checks["app.setup"]
    )
    assert checks["app.setup_alone"] == "FAIL: no timeline.tsv"


def test_an_ordinary_setup_failure_blames_no_one(oracle):
    _linux_run(oracle.results, RACED, "pixi: not found\n")
    assert "got SIG" not in _linux_checks(oracle.results)["app.setup"]


def test_the_step_beside_setup_mutation_fails_a_clean_timeline(oracle):
    import lab

    _linux_run(
        oracle.results,
        # setup's own start in the same millisecond as the launch is not beside it
        ["1000\tstep-start setup", "1000\tapp-launched 42", "9000\tapp-closed"],
        "",
    )
    assert _linux_checks(oracle.results)["app.setup_alone"].startswith("ok")
    lab.LINUX_GUI_MUTATIONS["step-beside-setup"](oracle.results)
    assert _linux_checks(oracle.results)["app.setup_alone"] == (
        "FAIL: steps started while the app ran setup: uninstall"
    )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="pgrep -f and /bin/sh")
def test_the_harness_notes_a_crashed_apps_events_then_stops_what_it_left(oracle):
    # Review of #459: the harness closed what a crashed app left before noting
    # the events it wrote while dying, so the harness got the blame.
    import subprocess

    root = oracle.results
    (root / "lab-run.json").write_text(json.dumps({"target": "linux"}))
    events = root / "setup-events.ndjson"
    (root / "app").mkdir()
    (root / "ws").mkdir()
    (root / "ws" / "install.sh").write_text(
        "trap 'exit 143' TERM\nwhile :; do sleep 0.2; done\n"
    )
    done = {"type": "step-complete", "result": {"id": "install-wgsextract"}}
    done["result"].update(status="failed", exitCode=143, stderr="Terminated\n")
    app = root / "app" / "fake-app"
    app.write_text(
        f'sh "{root}/ws/install.sh" &\n'
        f"printf '%s\\n' '{json.dumps({'type': 'step-start'})}' >> \"{events}\"\n"
        "sleep 3\n"
        f"printf '%s\\n' '{json.dumps(done)}' >> \"{events}\"\n"
        "exit 3\n"
    )
    driver = root / "driver.sh"
    driver.write_text(
        f'. "{LAB_SCRIPTS}/guest/app-lifecycle.sh"\n'
        'sh "$APP_MAIN" & app_pid=$!\n'
        "app_launched\n"
        "await_setup 60\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "R": str(root),
        "events": str(events),
        "APP_MAIN": str(app),
        "APP_TREE": f"{root}/app/|{root}/ws/",
    }
    run = subprocess.run(
        ["sh", str(driver)], env=env, capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 1, run.stdout + run.stderr
    assert "the app exited during setup (exit status 3)" in run.stdout
    found = subprocess.run(["pgrep", "-f", f"{root}/ws/"], capture_output=True)
    assert found.stdout == b""
    timeline = [
        line.split("\t", 1)[1]
        for line in (root / "timeline.tsv").read_text().splitlines()
    ]
    closes = timeline.index("harness-closes-app the app exited during setup")
    # Everything the app wrote is noted before the harness acts, and it ends closed.
    assert timeline.index("app-exited 3") < closes
    assert timeline.index("setup-events 2") < closes
    assert timeline[-1] == "app-closed"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX guest script")
def test_optional_tools_install_into_the_workspace_runtime(tmp_path: Path) -> None:
    # Regression: once v0.8.5 (PR #531) became the latest app, its CLI runtime
    # lived in ~/wgs-extract, and the lab's optional-tools step looked only in
    # app data, so every app Install Lab failed its gatk action.
    workspace = tmp_path / "wgs-extract"
    manifest = workspace / "runtime" / "wgsextract-cli" / "app" / "pixi.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("")
    pixi = manifest.parents[1] / ".pixi" / "bin" / "pixi"
    pixi.parent.mkdir(parents=True)
    calls = tmp_path / "pixi-args"
    pixi.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{calls}"\n')
    pixi.chmod(0o755)
    app_data = tmp_path / "app-data"
    (app_data / "WebKitCache").mkdir(parents=True)
    script = LAB_SCRIPTS / "guest" / "install-optional-tools.sh"

    found = subprocess.run(
        ["sh", str(script), str(app_data), str(workspace)],
        capture_output=True,
        text=True,
    )
    missing = subprocess.run(
        ["sh", str(script), str(app_data)],
        capture_output=True,
        text=True,
    )

    assert found.returncode == 0, found.stderr
    # contract: the gatk env must be installed into the app's own runtime.
    assert calls.read_text().split() == [
        "install",
        "--locked",
        "--manifest-path",
        str(manifest),
        "-e",
        "gatk",
    ]
    assert missing.returncode == 1
    assert "No installed CLI manifest" in missing.stderr


# Issue #625: the tree's plan ran against the released v0.8.5 app, whose bundle
# lacked the renamed action; the judge said only "download_test_genome=failed",
# which read like a broken download.
def test_a_plan_action_the_released_app_lacks_is_named_as_a_version_mismatch(oracle):
    _linux_run(oracle.results, None, "")
    (oracle.results / "bundle-report.json").write_text(
        json.dumps(
            {
                "bundleID": "wgs-extract",
                "bundleVersion": "0.8.5",
                "steps": [
                    {
                        "id": "download_test_genome",
                        "actionID": "test-dataset-download",
                        "status": "failed",
                        "error": "Unknown action: test-dataset-download",
                    },
                    {"id": "download_reference", "status": "skipped"},
                ],
            }
        )
    )
    actions = _linux_checks(oracle.results)["app.actions"]
    assert actions.startswith("FAIL")
    assert (
        "download_test_genome: the test plan expects action test-dataset-download, "
        "which this app version (wgs-extract 0.8.5) doesn't have"
    ) in actions


@pytest.mark.parametrize("release_step_passed", [True, False])
def test_801_pinned_expectations_ignore_tree_and_artifact_plans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, release_step_passed: bool
) -> None:
    monkeypatch.syspath_prepend(str(LAB_SCRIPTS))
    from app_contract import load_contract

    source = tmp_path / "trusted-tag"
    scripts = source / "scripts" / "installlab"
    (scripts / "gui").mkdir(parents=True)
    shutil.copy2(LAB_SCRIPTS / "gui_oracle.py", scripts / "gui_oracle.py")
    release_plan = {"steps": [{"id": "released-action"}]}
    plan_path = scripts / "gui" / "wgsextract-plan.json"
    plan_path.write_text(json.dumps(release_plan))
    plan, oracle_type = load_contract(source)
    assert json.loads(plan.read_text()) == release_plan

    results = tmp_path / "evidence"
    results.mkdir()
    _linux_run(results, None, "")
    # Collected evidence cannot replace the trusted tag's required checks.
    (results / "gui-plan.json").write_text(json.dumps({"steps": []}))
    steps = [{"id": "not-a-release-action", "status": "passed"}]
    if release_step_passed:
        steps.append({"id": "released-action", "status": "passed"})
    (results / "bundle-report.json").write_text(json.dumps({"steps": steps}))
    current = oracle_type(results, "nonce", {}, signed=False)
    current.check_app_reports()
    actions = next(c for c in current.checks if c.name == "app.actions")
    assert actions.ok is release_step_passed
    assert "1/1" in actions.detail if release_step_passed else "0/1" in actions.detail


def test_801_tagged_oracle_cannot_relax_scientific_or_state_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(LAB_SCRIPTS))
    from app_contract import load_contract

    scripts = tmp_path / "tag" / "scripts" / "installlab"
    (scripts / "gui").mkdir(parents=True)
    (scripts / "gui" / "wgsextract-plan.json").write_text('{"steps": []}')
    # Model a tag whose historical scientific/state checks were less strict.
    (scripts / "gui_oracle.py").write_text(
        "from pathlib import Path\n"
        "from gui_oracle import app_platform\n"
        "PLAN = Path(__file__).parent / 'gui' / 'wgsextract-plan.json'\n"
        "class GuiOracle:\n"
        "    def check_steps(self):\n"
        "        self.record('release.steps', False, 'tagged step requirement')\n"
        "    def check_app_reports(self):\n"
        "        self.record('release.reports', False, 'tagged report requirement')\n"
        "    def check_outputs(self, dataset):\n"
        "        raise AssertionError('must not use old scientific checks')\n"
        "    def check_state(self, noise_patterns):\n"
        "        raise AssertionError('must not use old state checks')\n"
    )
    _, oracle_type = load_contract(tmp_path / "tag")
    results = tmp_path / "evidence"
    results.mkdir()
    _linux_run(results, None, "")
    current = oracle_type(results, "nonce", {}, signed=False)
    current.check_steps()
    current.check_app_reports()
    assert all(not c.ok for c in current.checks)
    assert {c.name for c in current.checks} == {"release.steps", "release.reports"}
    current.check_outputs(tmp_path)
    scientific = [c for c in current.checks if c.name.startswith("output.")]
    assert len(scientific) == 8
    assert all(not c.ok for c in scientific)
    current.check_state([])
    assert any(c.name == "state.app_installed" and not c.ok for c in current.checks)


@pytest.mark.parametrize("target", ["linux", "windows", "macos"])
@pytest.mark.parametrize("missing", ["", "step", "signature", "window"])
def test_801_release_lifecycle_uses_tagged_platform_without_changing_state_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, missing: str
) -> None:
    monkeypatch.syspath_prepend(str(LAB_SCRIPTS))
    from app_contract import load_contract

    scripts = tmp_path / "tag" / "scripts" / "installlab"
    (scripts / "gui").mkdir(parents=True)
    shutil.copy2(
        LAB_SCRIPTS / "gui" / "wgsextract-plan.json",
        scripts / "gui" / "wgsextract-plan.json",
    )
    (scripts / "gui_oracle.py").write_text(
        (LAB_SCRIPTS / "gui_oracle.py").read_text()
        + "\ntree_app_platform = app_platform\n"
        "def app_platform(meta):\n"
        "    return replace(tree_app_platform(meta),\n"
        "        steps=['release-only', 'release-signature'],\n"
        "        signature_step='release-signature', signed_text='release-signed',\n"
        "        unsigned_text='release-unsigned', window_file='release-window.txt',\n"
        "        window_text='release-rendered', install_marker='/release-state')\n"
    )
    _, oracle_type = load_contract(tmp_path / "tag")
    results = tmp_path / "evidence"
    results.mkdir()
    _linux_run(results, None, "")
    (results / "lab-run.json").write_text(json.dumps({"target": target}))
    (results / "evidence").mkdir(exist_ok=True)
    if missing != "step":
        (results / "evidence" / "release-only.rc").write_text("0")
    (results / "evidence" / "release-signature.rc").write_text("0")
    (results / "evidence" / "release-signature.log").write_text(
        "" if missing == "signature" else "release-signed"
    )
    if missing != "window":
        (results / "release-window.txt").write_text("release-rendered")
    current = oracle_type(results, "nonce", {}, signed=True)
    tree_platform = current.app
    current.check_steps()
    current.check_app_reports()
    checks = {c.name: c.ok for c in current.checks}
    assert checks["step.release-only"] is (missing != "step")
    assert checks["step.release-signature"] is (missing != "signature")
    assert checks["app.window_appeared"] is (missing != "window")
    assert current.app is tree_platform
    assert current.install_marker == tree_platform.install_marker
    current.check_state([])
    assert any(c.name == "state.app_installed" and not c.ok for c in current.checks)


@pytest.mark.parametrize("step", ["snp", "release-renamed-action"])
def test_801_action_failed_mutation_corrupts_a_required_selected_plan_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    monkeypatch.syspath_prepend(str(LAB_SCRIPTS))
    import lab
    from app_contract import load_contract

    scripts = tmp_path / "tag" / "scripts" / "installlab"
    (scripts / "gui").mkdir(parents=True)
    shutil.copy2(LAB_SCRIPTS / "gui_oracle.py", scripts / "gui_oracle.py")
    plan = scripts / "gui" / "wgsextract-plan.json"
    plan.write_text(json.dumps({"steps": [{"id": step}]}))
    _, oracle_type = load_contract(tmp_path / "tag")
    monkeypatch.setattr(lab.app_contract, "plan_path", plan)
    results = tmp_path / "evidence"
    results.mkdir()
    _linux_run(results, None, "")
    report = results / "bundle-report.json"
    report.write_text(json.dumps({"steps": [{"id": step, "status": "passed"}]}))
    before = oracle_type(results, "nonce", {}, signed=False)
    before.check_app_reports()
    assert next(c.ok for c in before.checks if c.name == "app.actions")
    lab.GUI_MUTATIONS["action-failed"](results)
    after = oracle_type(results, "nonce", {}, signed=False)
    after.check_app_reports()
    assert not next(c.ok for c in after.checks if c.name == "app.actions")


def test_801_missing_tag_contract_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(LAB_SCRIPTS))
    from app_contract import load_contract

    with pytest.raises(ValueError, match="lacks its plan/oracle"):
        load_contract(tmp_path)
