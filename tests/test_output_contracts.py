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
from tests.test_trio_vcfs import _trio
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


def latest_pinned_release(root: Path) -> str:
    pinned, problems = baselines.effective_baselines(root, None)
    latest = baselines.newest(pinned)
    assert not errors(problems) and latest is not None
    return str(latest["release"])


def test_1180_checkout_matches_the_latest_pinned_baseline() -> None:
    assert not errors(contracts.check(ROOT))


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
    assert removed.release == latest_pinned_release(repo)
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


def test_1448_experimental_change_hints_point_to_release_note_fragments(
    repo: Path,
) -> None:
    edit(
        repo,
        f"{SCHEMAS}/wgsextract.report.1.schema.json",
        lambda schema: schema["required"].remove("guide"),
    )
    edit(repo, baselines.REGISTRY, lambda registry: registry["roles"].pop("aadr"))

    found = errors(contracts.check(repo))

    for key in (
        "experimental_requiredness_changed wgsextract.report/1:guide",
        "experimental_role_removed roles:aadr",
    ):
        assert "docs/release-notes/unreleased/" in found[key].message
        assert "unreleased.md" not in found[key].message


def test_1180_experimental_changes_need_a_release_note_notice(repo: Path) -> None:
    edit(
        repo,
        f"{SCHEMAS}/wgsextract.report.1.schema.json",
        lambda schema: (
            schema["properties"].pop("guide"),
            schema["required"].remove("guide"),
        ),
    )
    assert "experimental_field_removed wgsextract.report/1:guide" in errors(
        contracts.check(repo)
    )

    # A mention in the baseline release's own notes is history, not a notice.
    baseline_notes = repo / "docs/release-notes" / f"{latest_pinned_release(repo)}.md"
    with baseline_notes.open("a", encoding="utf-8") as old:
        old.write("\n- `wgsextract.report/1:guide` changed.\n")
    assert "experimental_field_removed wgsextract.report/1:guide" in errors(
        contracts.check(repo)
    )
    (repo / "docs/release-notes/unreleased/1180-report-guide.md").write_text(
        "## Changed\n\n- The report's `wgsextract.report/1:guide` field is gone.\n",
        encoding="utf-8",
    )
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


def test_1180_stable_output_roles_are_kept_and_name_their_producers(repo: Path) -> None:
    def change(registry: dict[str, Any]) -> None:
        del registry["roles"]["mt_bam"]
        registry["roles"]["phantom_report"] = {"tier": "stable", "meaning": "x"}

    edit(repo, baselines.REGISTRY, change)
    found = errors(contracts.check(repo))
    assert "stable_role_removed roles:mt_bam" in found
    assert "stable_role_without_producer roles:phantom_report" in found


def test_1180_dropping_a_stable_string_length_bound_is_a_widening(repo: Path) -> None:
    """Review of #1365: minLength was dropped, so an empty `command` passed."""
    edit(repo, RESULT, lambda schema: schema["properties"]["command"].pop("minLength"))
    found = errors(contracts.check(repo))
    assert (
        "minLength" in found["stable_type_changed wgsextract.result/1:command"].message
    )


def test_1180_an_unrestricted_object_alternative_widens_its_children(
    repo: Path,
) -> None:
    """Review of #1365: `error: {}` became valid while `error.kind` stayed
    required in the surface, so the widening passed."""
    edit(
        repo,
        RESULT,
        lambda schema: schema["properties"]["error"]["anyOf"].append(
            {"type": "object"}
        ),
    )
    found = errors(contracts.check(repo))
    assert {
        "stable_requiredness_changed wgsextract.result/1:error.kind",
        "stable_type_changed wgsextract.result/1:error.kind",
    } <= set(found)


def test_1180_removing_a_document_with_stable_fields_is_a_stable_break(
    repo: Path,
) -> None:
    """Review of #1365: an experimental document's stable `schema` and
    `generated` fields vanished behind a mere release-note notice."""
    edit(repo, baselines.REGISTRY, lambda r: r["documents"].pop("wgsextract.report/1"))
    (repo / "docs/release-notes/unreleased/1180-report-document.md").write_text(
        "## Removed\n\n- `wgsextract.report/1:` is gone.\n", encoding="utf-8"
    )
    assert "stable_document_removed wgsextract.report/1:" in errors(
        contracts.check(repo)
    )


def test_1180_sample_folder_overrides_are_not_checked_as_file_overrides() -> None:
    """Review of #1365: `override.sex` was also read as an `override.*` entry."""
    folder_wide = {"schema": "wgsextract.sample/1", "override": {"sex": "Female"}}
    assert contracts.check_output(ROOT, folder_wide) == []
    unknown = {"schema": "wgsextract.sample/1", "override": {"sex": "Unknown"}}
    assert set(errors(contracts.check_output(ROOT, unknown))) == {
        "stable_type_mismatch_in_output wgsextract.sample/1:override.sex"
    }


def test_1180_editing_the_baseline_in_the_same_change_cannot_erase_the_comparison(
    repo: Path,
) -> None:
    git(repo, "init", "-q")
    filename = f"{latest_pinned_release(repo)}.json"
    base = commit_all(repo, "base")
    edit(repo, RESULT, drop_exit_code)

    def forge(baseline: dict[str, Any]) -> None:
        del baseline["documents"]["wgsextract.result/1"]["paths"]["exit_code"]
        baseline["digest"] = baselines.digest(baseline)

    edit(repo, f"contracts/baselines/{filename}", forge)
    commit_all(repo, "drop exit_code and its baseline row")

    found = errors(contracts.check(repo, base))

    assert f"baseline_modified baselines:{filename}" in found
    assert "stable_field_removed wgsextract.result/1:exit_code" in found
    assert contracts.main(["--root", str(repo), "check", "--base", base]) == 1

    # Nor can the same change approve its own baseline edit.
    approval = {
        "document": "baselines",
        "path": filename,
        "code": "baseline_modified",
        "approval": "#1180",
        "reason": "Self-approval.",
    }
    edit(repo, baselines.REGISTRY, lambda r: r["approved_breaks"].append(approval))
    found = errors(contracts.check(repo, base))
    assert {
        f"baseline_modified baselines:{filename}",
        f"approved_break_invalid baselines:{filename}",
    } <= set(found)


def test_1180_baselines_are_pinned_from_a_release_tag_and_stay_immutable(
    repo: Path,
) -> None:
    git(repo, "init", "-q")
    commit = commit_all(repo, "release")
    git(repo, "tag", "v9.9.8")
    target = baselines.pin(repo, "v9.9.8")
    pinned = json.loads(target.read_text(encoding="utf-8"))
    assert (pinned["release"], pinned["commit"]) == ("v9.9.8", commit)
    assert pinned["tiers_source"] == "release"
    assert pinned["digest"] == baselines.digest(pinned)
    assert not errors(contracts.check(repo))
    with pytest.raises(SystemExit, match="immutable"):
        baselines.pin(repo, "v9.9.8")

    def forge(baseline: dict[str, Any]) -> None:
        del baseline["documents"]["wgsextract.result/1"]["paths"]["status"]
        baseline["digest"] = baselines.digest(baseline)

    edit(repo, "contracts/baselines/v9.9.8.json", forge)
    assert "baseline_unverified baselines:v9.9.8.json" in errors(contracts.check(repo))

    base = commit_all(repo, "forged")
    shutil.copy(
        repo / "contracts/baselines/v9.9.8.json",
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


def test_1180_a_later_release_cannot_bootstrap_its_own_tiers(repo: Path) -> None:
    """Review of #1365: a `bootstrap` baseline of a tag that has a registry
    skipped the tag's tiers, so an empty one could become the newest."""
    git(repo, "init", "-q")
    commit = commit_all(repo, "release")
    git(repo, "tag", "v9.9.8")
    forged = json.loads(
        (repo / "contracts/baselines/v0.8.9.json").read_text(encoding="utf-8")
    )
    forged.update(release="v9.9.8", commit=commit, documents={}, roles={})
    forged["digest"] = baselines.digest(forged)
    (repo / "contracts/baselines/v9.9.8.json").write_text(
        json.dumps(forged), encoding="utf-8"
    )
    found = errors(contracts.check(repo, commit))
    assert "bootstrap" in found["baseline_unverified baselines:v9.9.8.json"].message


@needs_htslib
def test_1180_real_outputs_of_every_stable_role_producer_meet_the_baseline(
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
        + ["--threads", "1", "--card", "--json"]
    )
    assert rc == 0, stderr
    from_cli = json.loads(stdout)
    manifest = json.loads(Path(from_cli["manifest"]).read_text(encoding="utf-8"))
    child, mother, father = _trio(
        tmp_path,
        child=["100 denovo A G 0/1:99"],
        mother=["100 . A . 0/0:99"],
        father=["100 . A . 0/0:99"],
    )
    trio = api.vcf.trio(
        api.VcfTrioParams(
            proband=Path(child),
            mother=Path(mother),
            father=Path(father),
            outdir=tmp_path / "trio",
        )
    ).to_dict()
    rc, stdout, stderr = run_cli(
        ["vcf", "filter", "--vcf-input", child, "--expr", "QUAL>30"]
        + ["--outdir", str(tmp_path / "filter"), "--json"]
    )
    assert rc == 0, stderr
    filtered = json.loads(stdout)

    documents: tuple[dict[str, Any], ...] = (
        from_api,
        from_cli,
        manifest,
        trio,
        filtered,
    )
    for document in documents:
        assert contracts.check_output(ROOT, document) == []
    recorded = {
        record["role"]
        for document in documents
        for key in ("inputs", "outputs")
        for record in document[key]
    }
    registry = json.loads((ROOT / baselines.REGISTRY).read_text(encoding="utf-8"))
    stable = {r for r, e in registry["roles"].items() if e["tier"] == "stable"}
    assert stable <= recorded, "each stable role needs a representative real run here"

    regressed = json.loads(json.dumps(manifest))
    del regressed["outputs"][0]["fingerprint"]
    regressed["outputs"] = [
        o for o in regressed["outputs"] if o["role"] != "result_card"
    ]
    regressed["exit_code"] = 256
    regressed["provenance"]["started"] = "yesterday"
    found = errors(contracts.check_output(ROOT, regressed))
    assert {
        "stable_field_missing_in_output wgsextract.result/1:outputs[].fingerprint",
        "stable_role_missing_in_output wgsextract.result/1:result_card",
        "stable_type_mismatch_in_output wgsextract.result/1:exit_code",
        "stable_type_mismatch_in_output wgsextract.result/1:provenance.started",
    } <= set(found)
