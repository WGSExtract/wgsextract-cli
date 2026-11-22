"""#1180: output contracts are checked against a pinned previous-release baseline."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from scripts import output_contract_baselines as baselines
from scripts import output_contracts as contracts
from scripts.output_contract_surface import Finding
from tests.htslib_helpers import needs_htslib, sam_to_bam
from tests.smoke_utils import run_cli
from wgsextract_cli import api

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = "src/wgsextract_cli/assets/schemas"
RESULT = f"{SCHEMAS}/wgsextract.result.1.schema.json"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The contract inputs of this checkout: registry, baselines, schemas,
    release notes and the producer sources scanned for output roles."""
    root = tmp_path_factory.mktemp("contracts")
    shutil.copytree(ROOT / "contracts", root / "contracts")
    shutil.copytree(ROOT / "docs/release-notes", root / "docs/release-notes")
    shutil.copytree(
        ROOT / "src/wgsextract_cli",
        root / "src/wgsextract_cli",
        ignore=lambda folder, names: [
            name
            for name in names
            if name == "__pycache__"
            or (
                not name.endswith(".py")
                and not (Path(folder) / name).is_dir()
                and not folder.endswith("schemas")
            )
        ],
    )
    return root


@pytest.fixture
def repo(template: Path, tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(template, root)
    return root


def edit(root: Path, path: str, change: Callable[[dict[str, Any]], object]) -> None:
    file = root / path
    document = json.loads(file.read_text(encoding="utf-8"))
    change(document)
    file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def errors(found: list[Finding]) -> dict[str, Finding]:
    return {f"{f.code} {f.key()}": f for f in found if f.level == "error"}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def drop_exit_code(schema: dict[str, Any]) -> None:
    del schema["properties"]["exit_code"]
    schema["required"].remove("exit_code")


def test_1180_checkout_matches_the_pinned_v0_8_9_baseline() -> None:
    found = contracts.check(ROOT)
    assert not errors(found)
    # error.code arrived after v0.8.9: an addition, which the policy permits.
    assert any(
        f.code == "field_added" and f.key() == "wgsextract.result/1:error.code"
        for f in found
    )
    baseline = json.loads(
        (ROOT / "contracts/baselines/v0.8.9.json").read_text(encoding="utf-8")
    )
    assert baseline["release"] == "v0.8.9"
    assert baseline["digest"] == baselines.digest(baseline)


def test_1180_dropping_a_stable_field_from_producer_and_schema_still_fails(
    repo: Path, tmp_path: Path
) -> None:
    """The gap #1180 closes: a field removed from both the writer and its
    schema passes the current contract but not the previous release's."""
    library = tmp_path / "library"
    result = api.ref.library_list(api.RefLibraryListParams(ref=library)).to_dict()
    edit(repo, RESULT, drop_exit_code)
    result.pop("exit_code")
    schema = json.loads((repo / RESULT).read_text(encoding="utf-8"))
    jsonschema.validate(result, schema)  # The current-only check is satisfied.

    found = errors(contracts.check(repo))

    removed = found["stable_field_removed wgsextract.result/1:exit_code"]
    assert removed.release == "v0.8.9"
    assert "breaking-change procedure" in removed.message
    assert contracts.main(["--root", str(repo), "check"]) == 1


def test_1180_an_owner_approved_break_is_reported_and_must_stay_current(
    repo: Path,
) -> None:
    approval = {
        "document": "wgsextract.result/1",
        "path": "exit_code",
        "code": "stable_field_removed",
        "approval": "#1180",
        "reason": "Test of the documented procedure.",
    }
    edit(repo, baselines.REGISTRY, lambda r: r["approved_breaks"].append(approval))
    assert "stale_approved_break wgsextract.result/1:exit_code" in errors(
        contracts.check(repo)
    )

    edit(repo, RESULT, drop_exit_code)
    edit(
        repo,
        baselines.REGISTRY,
        lambda r: r["documents"]["wgsextract.result/1"]["fields"].pop("exit_code"),
    )
    found = contracts.check(repo)
    assert not errors(found)
    assert any(f.level == "approved" and f.path == "exit_code" for f in found)

    edit(
        repo,
        baselines.REGISTRY,
        lambda r: r["approved_breaks"][0].update(approval="looks fine"),
    )
    assert "approved_break_invalid wgsextract.result/1:exit_code" in errors(
        contracts.check(repo)
    )


def test_1180_type_requiredness_values_and_tier_regressions_are_named(
    repo: Path,
) -> None:
    def regress(schema: dict[str, Any]) -> None:
        schema["properties"]["exit_code"] = {"type": "string"}
        schema["required"].remove("manifest")
        schema["properties"]["status"]["enum"].append("skipped")

    edit(repo, RESULT, regress)
    edit(
        repo,
        baselines.REGISTRY,
        lambda registry: registry["documents"]["wgsextract.result/1"]["fields"][
            "command"
        ].update(tier="experimental"),
    )

    found = errors(contracts.check(repo))

    assert {
        "stable_type_changed wgsextract.result/1:exit_code",
        "stable_requiredness_changed wgsextract.result/1:manifest",
        "stable_values_widened wgsextract.result/1:status",
        "stable_tier_downgraded wgsextract.result/1:command",
    } <= set(found)
    assert (
        "now also string"
        in found["stable_type_changed wgsextract.result/1:exit_code"].message
    )


def test_1180_changed_stable_meaning_is_incompatible(repo: Path) -> None:
    edit(
        repo,
        baselines.REGISTRY,
        lambda registry: registry["documents"]["wgsextract.result/1"]["fields"][
            "inputs[].fingerprint"
        ].update(meaning="A full-file SHA-256 integrity digest."),
    )
    found = errors(contracts.check(repo))
    assert "stable_meaning_changed wgsextract.result/1:inputs[].fingerprint" in found


def test_1180_additive_output_fields_are_permitted(repo: Path) -> None:
    def add(schema: dict[str, Any]) -> None:
        schema["properties"]["runtime"] = {"type": "string"}
        schema["$defs"]["file"]["properties"]["checksum"] = {"type": "string"}

    edit(repo, RESULT, add)
    found = contracts.check(repo)
    assert not errors(found)
    added = {f.key() for f in found if f.code == "field_added"}
    assert {
        "wgsextract.result/1:runtime",
        "wgsextract.result/1:outputs[].checksum",
    } <= added


def test_1180_experimental_changes_need_a_release_note_notice(repo: Path) -> None:
    edit(
        repo,
        f"{SCHEMAS}/wgsextract.report.1.schema.json",
        lambda schema: (
            schema["properties"].pop("guide"),
            schema["required"].remove("guide"),
        ),
    )
    found = errors(contracts.check(repo))
    unannounced = found["experimental_field_removed wgsextract.report/1:guide"]
    assert "docs/release-notes/unreleased.md" in unannounced.message

    with (repo / "docs/release-notes/unreleased.md").open(
        "a", encoding="utf-8"
    ) as notes:
        notes.write("\n- The report's `wgsextract.report/1:guide` field is gone.\n")
    found_after = contracts.check(repo)
    assert not errors(found_after)
    assert any(
        f.level == "notice" and f.key() == "wgsextract.report/1:guide"
        for f in found_after
    )


def test_1180_internal_outputs_change_freely_but_declare_consumers(
    repo: Path,
) -> None:
    steps = f"{SCHEMAS}/wgsextract.steps.1.schema.json"
    edit(repo, steps, lambda schema: schema.update(properties={}, required=[]))
    assert not [f for f in contracts.check(repo) if f.document == "wgsextract.steps/1"]

    edit(
        repo,
        baselines.REGISTRY,
        lambda registry: registry["documents"]["wgsextract.steps/1"].pop("consumers"),
    )
    assert "internal_consumers_undeclared wgsextract.steps/1:" in errors(
        contracts.check(repo)
    )


def test_1180_an_unvalidated_scientific_result_cannot_be_stable(repo: Path) -> None:
    def promote(registry: dict[str, Any]) -> None:
        registry["documents"]["wgsextract.report/1"]["fields"]["sections"].update(
            tier="stable"
        )

    edit(repo, baselines.REGISTRY, promote)
    found = errors(contracts.check(repo))
    assert any(key.startswith("unvalidated_scientific_result ") for key in found)

    (repo / "docs/validation.md").write_text("Concordance study.\n", encoding="utf-8")
    edit(
        repo,
        baselines.REGISTRY,
        lambda registry: registry["documents"]["wgsextract.report/1"]["fields"][
            "sections"
        ].update(validation="docs/validation.md"),
    )
    assert not any(
        key.startswith("unvalidated_scientific_result ")
        for key in errors(contracts.check(repo))
    )


def test_1180_stable_output_roles_are_kept_and_produced(repo: Path) -> None:
    def change(registry: dict[str, Any]) -> None:
        del registry["roles"]["mt_bam"]
        registry["roles"]["phantom_report"] = {"tier": "stable", "meaning": "x"}

    edit(repo, baselines.REGISTRY, change)
    found = errors(contracts.check(repo))
    assert "stable_role_removed roles:mt_bam" in found
    assert "stable_role_unproduced roles:phantom_report" in found


def test_1180_editing_the_baseline_in_the_same_change_cannot_erase_the_comparison(
    repo: Path,
) -> None:
    git(repo, "init", "-q")
    base = commit_all(repo, "base")
    edit(repo, RESULT, drop_exit_code)

    def forge(baseline: dict[str, Any]) -> None:
        del baseline["documents"]["wgsextract.result/1"]["paths"]["exit_code"]
        baseline["digest"] = baselines.digest(baseline)

    edit(repo, "contracts/baselines/v0.8.9.json", forge)
    commit_all(repo, "drop exit_code and its baseline row")

    found = errors(contracts.check(repo, base))

    assert "baseline_modified baselines:v0.8.9.json" in found
    assert "stable_field_removed wgsextract.result/1:exit_code" in found
    assert contracts.main(["--root", str(repo), "check", "--base", base]) == 1

    # Nor can the same change approve its own baseline edit.
    approval = {
        "document": "baselines",
        "path": "v0.8.9.json",
        "code": "baseline_modified",
        "approval": "#1180",
        "reason": "Self-approval.",
    }
    edit(repo, baselines.REGISTRY, lambda r: r["approved_breaks"].append(approval))
    found = errors(contracts.check(repo, base))
    assert {
        "baseline_modified baselines:v0.8.9.json",
        "approved_break_invalid baselines:v0.8.9.json",
    } <= set(found)


def test_1180_baselines_are_pinned_from_a_release_tag_and_stay_immutable(
    repo: Path,
) -> None:
    git(repo, "init", "-q")
    commit = commit_all(repo, "release")
    git(repo, "tag", "v0.9.0")
    target = baselines.pin(repo, "v0.9.0")
    pinned = json.loads(target.read_text(encoding="utf-8"))
    assert (pinned["release"], pinned["commit"]) == ("v0.9.0", commit)
    assert pinned["tiers_source"] == "release"
    assert pinned["digest"] == baselines.digest(pinned)
    assert not errors(contracts.check(repo))
    with pytest.raises(SystemExit, match="immutable"):
        baselines.pin(repo, "v0.9.0")

    def forge(baseline: dict[str, Any]) -> None:
        del baseline["documents"]["wgsextract.result/1"]["paths"]["status"]
        baseline["digest"] = baselines.digest(baseline)

    edit(repo, "contracts/baselines/v0.9.0.json", forge)
    assert "baseline_unverified baselines:v0.9.0.json" in errors(contracts.check(repo))

    base = commit_all(repo, "forged")
    shutil.copy(
        repo / "contracts/baselines/v0.9.0.json",
        repo / "contracts/baselines/v9.9.9.json",
    )
    edit(
        repo,
        "contracts/baselines/v9.9.9.json",
        lambda b: (b.update(release="v9.9.9"), b.update(digest=baselines.digest(b))),
    )
    # No v9.9.9 tag exists, so a PR cannot add a newer baseline of its own.
    assert "baseline_unverified baselines:v9.9.9.json" in errors(
        contracts.check(repo, base)
    )


@needs_htslib
def test_1180_real_mt_bam_manifest_cli_and_api_results_meet_the_baseline(
    tmp_path: Path,
) -> None:
    sam = [
        "@HD\tVN:1.6\tSO:coordinate",
        "@SQ\tSN:chr1\tLN:1000",
        "@SQ\tSN:chrM\tLN:16569",
        "r1\t0\tchr1\t1\t60\t4M\t*\t0\t0\tACGT\tIIII",
        "rM\t0\tchrM\t1\t60\t4M\t*\t0\t0\tACGT\tIIII",
    ]
    bam = Path(sam_to_bam(sam, tmp_path / "fake.bam"))
    from_api = api.extract.mt_bam(
        api.MtBamParams(input=bam, outdir=tmp_path / "api", threads=1)
    ).to_dict()
    rc, stdout, stderr = run_cli(
        ["extract", "mt-bam", "--input", str(bam), "--outdir", str(tmp_path / "cli")]
        + ["--threads", "1", "--json"]
    )
    assert rc == 0, stderr
    from_cli = json.loads(stdout)
    manifest = json.loads(Path(from_cli["manifest"]).read_text(encoding="utf-8"))

    for document in (from_api, from_cli, manifest):
        assert contracts.check_output(ROOT, document) == []

    regressed = json.loads(json.dumps(manifest))
    del regressed["outputs"][0]["fingerprint"]
    regressed["outputs"][0]["role"] = "mitochondrial_bam"
    found = errors(contracts.check_output(ROOT, regressed))
    assert (
        "stable_field_missing_in_output wgsextract.result/1:outputs[].fingerprint"
        in found
    )
    assert "stable_role_missing_in_output wgsextract.result/1:mt_bam" in found
