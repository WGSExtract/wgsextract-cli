"""#1179: recorded resource releases, release recheck and semantic run delta.

Real bcftools annotates fake sample VCFs with fake ClinVar releases; reports
are built from those runs. Only the network boundary is replaced, by the
loopback server in tests/http_server.py.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.htslib_helpers import needs_htslib, write_fasta
from tests.http_server import serving
from tests.test_1052_offline_defaults import blocked_network as blocked_network
from tests.test_clinvar_matching import SEQ, vcf
from wgsextract_cli import api
from wgsextract_cli.core import release_recheck

pytestmark = needs_htslib

GENE = "MUTYH:4595"
EXPERT = "reviewed_by_expert_panel"
NO_CRITERIA = "no_assertion_criteria_provided"
# Release name -> {position: (CLNSIG, CLNREVSTAT)}.
OCTOBER = {11: ("Pathogenic", NO_CRITERIA), 14: ("Pathogenic", EXPERT),
           15: ("Uncertain_significance", EXPERT)}  # fmt: skip
NOVEMBER = {11: ("Likely_pathogenic", NO_CRITERIA), 14: ("Pathogenic", EXPERT),
            15: ("Pathogenic", EXPERT)}  # fmt: skip
CALLS = {11: ("C", "A", "0/1:30:99:15,15"), 14: ("T", "C", "0/1:30:99:15,15"),
         15: ("A", "G", "0/1:30:99:15,15")}  # fmt: skip
LISTING = (
    '<a href="clinvar_20261108.vcf.gz">clinvar_20261108.vcf.gz</a>\n'
    '<a href="clinvar_20261115.vcf.gz">clinvar_20261115.vcf.gz</a>\n'
    '<a href="clinvar_20261115.vcf.gz.tbi">clinvar_20261115.vcf.gz.tbi</a>\n'
)


def _clinvar(
    path: Path, release: str | None, classes: dict[int, tuple[str, str]]
) -> Path:
    headers = ("##source=ClinVar", "##reference=GRCh38")
    if release:
        headers = (f"##fileDate={release}", *headers)
    rows = [
        f"{pos} VCV{pos} {CALLS[pos][0]} {CALLS[pos][1]} . PASS "
        f"CLNSIG={sig};CLNDN=MAP;GENEINFO={GENE};CLNREVSTAT={status} GT 0/1"
        for pos, (sig, status) in classes.items()
    ]
    return vcf(path, rows, resource=True, info_headers=headers)


def _run(
    root: Path,
    name: str,
    resource: Path,
    calls: dict[int, tuple[str, str, str]] = CALLS,
) -> Path:
    """vcf clinvar then report for sample S1 in root/name/S1; the report's
    manifest."""
    folder = root / name / "S1"
    folder.mkdir(parents=True)
    ref = root / "ref.fa"
    if not ref.exists():
        write_fasta(ref, {"1": SEQ})
    sample = vcf(
        folder / "S1.vcf",
        [
            f"{pos} . {r} {a} 99 PASS . GT:DP:GQ:AD {gt}"
            for pos, (r, a, gt) in calls.items()
        ],
    )
    api.vcf.clinvar(
        api.VcfClinvarParams(
            vcf_input=sample, clinvar_file=resource, ref=ref, outdir=folder, threads=1
        ),
        check=False,
    )
    genes = root / "genes.tsv"
    genes.write_text("gene\tinheritance\nMUTYH\tAR\n")
    report = api.report.build(api.ReportParams(workspace=folder, gene_list=str(genes)))
    assert report.manifest
    return Path(report.manifest)


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("runs")
    october = _clinvar(root / "clinvar_oct.vcf", "2026-10-15", OCTOBER)
    november = _clinvar(root / "clinvar_nov.vcf", "2026-11-15", NOVEMBER)
    december = _clinvar(root / "clinvar_dec.vcf", "2026-12-01", NOVEMBER)
    undated = _clinvar(root / "clinvar_undated.vcf", None, NOVEMBER)
    shallow = {**CALLS, 14: ("T", "C", "0/1:4:10:2,2")}
    broken = _clinvar(root / "clinvar_broken.vcf", "2026-11-15", NOVEMBER)
    broken.write_text(
        broken.read_text().replace(f"length={len(SEQ)}", "length=249250621")
    )
    return {
        "october": _run(root, "october", october),
        "november": _run(root, "november", november),
        "november_again": _run(root, "november_again", november),
        "december": _run(root, "december", december),
        "shallow": _run(root, "shallow", november, shallow),
        "undated": _run(root, "undated", undated),
        "failed": _run(root, "failed", broken),
    }


def _delta(before: Path, after: Path) -> dict[str, Any]:
    result = api.report.delta(api.ReportDeltaParams(before=before, after=after))
    assert result.details is not None
    return result.details


def _clinvar_section(delta: dict[str, Any]) -> dict[str, Any]:
    (section,) = [s for s in delta["sections"] if s["section"] == "clinvar"]
    return section


def _answers(path: Path, latest: str | None, **extra: Any) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "wgsextract.release_answers/1",
                "retrieved": "2026-11-20T09:00:00+00:00",
                "source": "copied from the ClinVar FTP listing",
                "answers": [
                    {"id": "clinvar", "build": "hg38", "latest": latest, **extra}
                ],
            }
        )
    )
    return path


def test_1179_manifests_record_the_clinvar_release_and_gene_list_used(runs):
    clinvar_manifest = runs["october"].parent / "clinvar_pathogenic.vcf.manifest.json"
    (clinvar,) = json.loads(clinvar_manifest.read_text())["resources"]
    assert (clinvar["id"], clinvar["build"], clinvar["release"]) == (
        "clinvar",
        "hg38",
        "2026-10-15",
    )
    assert clinvar["release_basis"] == "file_header"
    assert clinvar["fingerprint"].startswith("wgse-fp1:")
    assert "NCBI" in clinvar["licence"]  # audited source terms travel with it
    report = json.loads(runs["october"].read_text())
    assert {(r["id"], r["release"]) for r in report["resources"]} == {
        ("clinvar", "2026-10-15"),
        ("gene-list", None),  # a user's TSV has no release
    }


def test_1179_undated_clinvar_release_stays_unknown(runs, tmp_path):
    (clinvar,) = [
        r
        for r in json.loads(runs["undated"].read_text())["resources"]
        if r["id"] == "clinvar"
    ]
    assert (clinvar["release"], clinvar["release_basis"]) == (None, "unrecorded")
    answers = _answers(tmp_path / "answers.json", "2026-11-15")
    result = api.ref.recheck(
        api.RefRecheckParams(manifests=(runs["undated"],), answers=answers)
    )
    (checked,) = [r for r in result.details["resources"] if r["id"] == "clinvar"]
    assert checked["status"] == "unknown"
    assert checked["latest_release"] == "2026-11-15"


def test_1179_delta_separates_reclassification_from_unchanged_calls(runs):
    delta = _delta(runs["october"], runs["november"])
    assert [
        (r["before"], r["after"], r["change"])
        for r in delta["releases"]
        if r["id"] == "clinvar"
    ] == [("2026-10-15", "2026-11-15", "changed")]
    section = _clinvar_section(delta)
    assert section["compared"] and section["unchanged"] == 1  # pos 14
    by_key = {f["key"]: f for f in section["findings"]}
    assert by_key["1:11:C:A"]["changes"] == ["reclassified"]
    assert (
        by_key["1:11:C:A"]["before"]["value"],
        by_key["1:11:C:A"]["after"]["value"],
    ) == (
        "Pathogenic",
        "Likely pathogenic",
    )
    assert by_key["1:15:A:G"]["changes"] == ["added"]
    for finding in section["findings"]:
        assert finding["release_changes"] == ["clinvar"]
        assert finding["confidence"] == "high"
        assert finding["disclaimer"] == "Technical result, not diagnosis."
    assert delta["disclaimer"] and delta["limitations"]
    assert delta["summary"]["resource_only"] is False


def test_1179_resource_only_update_has_no_finding_changes(runs):
    delta = _delta(runs["november"], runs["december"])
    assert delta["summary"]["resource_only"] is True
    assert delta["summary"]["findings_unchanged"] == 3
    assert not any(s["findings"] for s in delta["sections"])


def test_1179_changed_callability_is_a_confidence_change_not_a_reclassification(runs):
    delta = _delta(runs["november"], runs["shallow"])
    (finding,) = _clinvar_section(delta)["findings"]
    assert finding["key"] == "1:14:T:C"
    assert finding["changes"] == ["confidence_changed"]
    assert finding["after"]["confidence"] == "low"
    assert finding["release_changes"] == []
    assert not any(r["change"] != "same" for r in delta["releases"])


def test_1179_failed_analysis_is_not_compared_as_removed_findings(runs):
    delta = _delta(runs["november"], runs["failed"])
    section = _clinvar_section(delta)
    assert (section["before_status"], section["after_status"]) == ("ok", "failed")
    assert not section["compared"] and not section["findings"]
    assert delta["summary"]["findings_removed"] == 0
    assert delta["summary"]["sections_not_compared"] >= 1


def test_1179_one_failed_run_in_a_shared_section_removes_none_of_its_findings(
    runs, tmp_path
):
    # The section stays "ok" while another of its runs succeeds in both reports.
    cyrius = {"command": "typing cyp2d6", "status": "ok", "finished": None,
              "manifest": "cyp2d6.manifest.json", "latest_attempt": None}  # fmt: skip
    finding = {"section": "clinvar", "label": "CYP2D6 diplotype (Cyrius)",
               "value": "*1/*2", "detail": None, "gene": "CYP2D6", "note": None,
               "confidence": "high", "confidence_reasons": [], "confidence_basis": "x",
               "evidence": {"file": None, "region": None, "manifest": "cyp2d6.manifest.json"},
               "variant": None, "links": [], "disclaimer": "Technical result, not diagnosis."}  # fmt: skip
    paths = []
    for name, clinvar_status in (("before", "ok"), ("after", "failed")):
        document = json.loads(runs["november"].read_text())
        section = document["details"]["sections"]["clinvar"]
        section["sources"][0]["status"] = clinvar_status
        if clinvar_status != "ok":
            section["findings"] = []
        section["sources"].append(cyrius)
        section["findings"].append(finding)
        paths.append(tmp_path / f"{name}.manifest.json")
        paths[-1].write_text(json.dumps(document))
    section = _clinvar_section(_delta(*paths))
    assert section["compared"] and section["not_compared"] == ["vcf clinvar"]
    assert section["findings"] == [] and section["unchanged"] == 1
    assert "vcf clinvar" in section["note"]


def test_1179_annotator_runs_with_changed_results_are_not_resource_only(runs):
    before, after = (
        runs[name].parent / "clinvar_pathogenic.vcf.manifest.json"
        for name in ("october", "november")
    )
    delta = _delta(before, after)
    assert delta["summary"]["resource_only"] is False
    assert any(c["area"] == "output" for c in delta["changes"])
    assert [r["change"] for r in delta["releases"]] == ["changed"]


def test_1179_timestamps_and_ordering_are_ignored(runs, tmp_path):
    again = _delta(runs["november"], runs["november_again"])
    assert not any(s["findings"] for s in again["sections"]) and not again["changes"]
    assert again["summary"]["findings_unchanged"] == 3
    shuffled = json.loads(runs["november"].read_text())
    for section in shuffled["details"]["sections"].values():
        section["findings"].reverse()
    shuffled["resources"].reverse()
    shuffled["provenance"]["finished"] = "2030-01-01T00:00:00+00:00"
    copy = tmp_path / "S1_personal_report.manifest.json"
    copy.write_text(json.dumps(shuffled, indent=4))
    delta = _delta(runs["november"], copy)
    assert not any(s["findings"] for s in delta["sections"]) and not delta["changes"]
    assert all(r["change"] == "same" for r in delta["releases"])


@pytest.mark.parametrize(
    ("edit", "code"),
    [
        (lambda d: d["details"]["sample"].update(name="S2"), "delta_sample_mismatch"),
        (
            lambda d: d["details"]["sections"]["clinvar"].update(build="hg19"),
            "delta_build_mismatch",
        ),
        # Copilot review of #1363: one section on another build was compared.
        (
            lambda d: d["details"]["sections"].update(
                acmg_research={"status": "ok", "build": "hg19", "findings": []}
            ),
            "delta_build_mismatch",
        ),
        (lambda d: d.update(command="vcf clinvar"), "delta_command_mismatch"),
        (lambda d: d.update(schema="wgsextract.result/9"), "unsupported_schema"),
        (
            lambda d: d["details"].update(schema="wgsextract.report/9"),
            "unsupported_schema",
        ),
    ],
)
def test_1179_incompatible_or_unsupported_runs_are_refused(runs, tmp_path, edit, code):
    document = json.loads(runs["november"].read_text())
    edit(document)
    other = tmp_path / "other.manifest.json"
    other.write_text(json.dumps(document))
    result = api.report.delta(
        api.ReportDeltaParams(before=runs["november"], after=other), check=False
    )
    assert result.exit_code == 1
    assert result.error and result.error.code == code


def test_1179_offline_answers_match_a_mocked_online_query(runs, tmp_path, monkeypatch):
    manifests = (runs["october"], runs["november"])
    with serving(files={"vcf_GRCh38/weekly/": LISTING.encode()}) as server:
        monkeypatch.setitem(
            release_recheck.RELEASE_LISTINGS,
            ("clinvar", "hg38"),
            server.url_for("vcf_GRCh38/weekly/"),
        )
        saved = tmp_path / "answers.json"
        online = api.ref.recheck(
            api.RefRecheckParams(manifests=manifests, online=True, save_answers=saved)
        )
        # The only request is a GET of the public listing: no query, body or
        # sample-derived text (the release name is all that comes back).
        ((method, target, headers),) = server.raw_requests
        assert (method, target) == ("GET", server.prefix + "vcf_GRCh38/weekly/")
        sent = target + json.dumps(headers)
        for private in ("S1", "MUTYH", ":11", str(tmp_path), "2026-10-15"):
            assert private not in sent
        assert "Content-Length" not in headers
    offline = api.ref.recheck(api.RefRecheckParams(manifests=manifests, answers=saved))

    def comparison(result: Any) -> list[tuple[Any, ...]]:
        return [
            (r["id"], r["build"], r["used_release"], r["latest_release"], r["status"])
            for r in result.details["resources"]
        ]

    assert comparison(online) == comparison(offline)
    assert (
        "clinvar",
        "hg38",
        "2026-10-15",
        "2026-11-15",
        "newer_available",
    ) in comparison(offline)
    assert ("clinvar", "hg38", "2026-11-15", "2026-11-15", "up_to_date") in comparison(
        offline
    )
    assert ("gene-list", None, None, None, "unknown") in comparison(offline)
    assert [w.code for w in offline.warnings].count("newer_release_available") == 1
    (stale,) = [
        r for r in offline.details["resources"] if r["used_release"] == "2026-10-15"
    ]
    # A report rests on its analyses: rerun those first, then the report.
    assert "release 2026-11-15" in stale["next_step"]
    assert "'vcf clinvar --clinvar-file FILE'" in stale["next_step"]
    # ref clinvar may install an older pinned snapshot, and says so.
    assert "'wgsextract ref clinvar' installs the release" in stale["next_step"]
    assert stale["next_step"].endswith("rerun vcf clinvar, report.")
    provenance = offline.details["answers"]
    assert provenance["mode"] == "answers_file" and provenance["path"] == str(saved)
    assert provenance["sha256"] and provenance["retrieved"]
    assert any(i.role == "release_answers" and i.fingerprint for i in offline.inputs)
    assert online.details["answers"]["contacted"] == [
        server.url_for("vcf_GRCh38/weekly/")
    ]


def test_1179_failed_lookup_is_an_error_never_up_to_date(runs, monkeypatch):
    with serving(data=None) as server:
        monkeypatch.setitem(
            release_recheck.RELEASE_LISTINGS,
            ("clinvar", "hg38"),
            server.url_for("gone/"),
        )
        result = api.ref.recheck(
            api.RefRecheckParams(manifests=(runs["november"],), online=True)
        )
    (clinvar,) = [r for r in result.details["resources"] if r["id"] == "clinvar"]
    assert clinvar["status"] == "lookup_error"
    assert "release_lookup_failed" in [w.code for w in result.warnings]


def test_1179_truncated_listing_is_a_lookup_error(runs, monkeypatch):
    with serving(files={"weekly/": LISTING.encode() * 50}) as server:
        server.drops = 1  # the body stops half-way: http.client.IncompleteRead
        monkeypatch.setitem(
            release_recheck.RELEASE_LISTINGS,
            ("clinvar", "hg38"),
            server.url_for("weekly/"),
        )
        result = api.ref.recheck(
            api.RefRecheckParams(manifests=(runs["november"],), online=True)
        )
    (clinvar,) = [r for r in result.details["resources"] if r["id"] == "clinvar"]
    assert clinvar["status"] == "lookup_error"


def test_1179_default_and_offline_paths_make_no_request(
    runs, tmp_path, blocked_network
):
    refused = api.ref.recheck(
        api.RefRecheckParams(manifests=(runs["october"],)), check=False
    )
    assert refused.exit_code == 2 and refused.error
    message = refused.error.message
    assert "NCBI ClinVar" in message and "2026-10-15" in message
    assert "--answers FILE" in message and "--online" in message
    assert "ftp.ncbi.nlm.nih.gov" in message
    answers = _answers(tmp_path / "answers.json", "2026-11-15")
    assert (
        api.ref.recheck(
            api.RefRecheckParams(manifests=(runs["october"],), answers=answers)
        ).exit_code
        == 0
    )
    assert _delta(runs["october"], runs["november"])["summary"]["findings_added"] == 1


def test_1179_unsupported_answers_and_unrecorded_manifests(runs, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "something/2", "answers": []}))
    result = api.ref.recheck(
        api.RefRecheckParams(manifests=(runs["october"],), answers=bad), check=False
    )
    assert result.error and result.error.code == "unsupported_schema"
    old = json.loads(runs["october"].read_text())
    del old["resources"]
    legacy = tmp_path / "legacy.manifest.json"
    legacy.write_text(json.dumps(old))
    answers = _answers(tmp_path / "answers.json", "2026-11-15")
    checked = api.ref.recheck(
        api.RefRecheckParams(manifests=(legacy,), answers=answers)
    )
    assert checked.details["unrecorded_manifests"] == [str(legacy)]
    assert checked.details["resources"] == []
    assert "resources_not_recorded" in [w.code for w in checked.warnings]


def test_1179_installed_change_is_reported_without_downloading(runs, tmp_path):
    folder = tmp_path / "moved"
    shutil.copytree(runs["october"].parent, folder)
    manifest = folder / "clinvar_pathogenic.vcf.manifest.json"
    document = json.loads(manifest.read_text())
    resource = Path(document["resources"][0]["path"])
    installed = _clinvar(tmp_path / "clinvar_now.vcf", "2026-11-15", NOVEMBER)
    document["resources"][0]["path"] = str(installed)
    manifest.write_text(json.dumps(document))
    answers = _answers(tmp_path / "answers.json", "2026-11-15")
    result = api.ref.recheck(
        api.RefRecheckParams(manifests=(manifest,), answers=answers)
    )
    (clinvar,) = result.details["resources"]
    assert (clinvar["installed"], clinvar["installed_release"]) == (
        "changed",
        "2026-11-15",
    )
    assert "vcf clinvar" in clinvar["next_step"]
    assert resource.exists()


def test_1179_answers_older_than_the_release_used_never_suggest_a_downgrade(
    runs, tmp_path
):
    answers = _answers(tmp_path / "answers.json", "2026-10-15")
    result = api.ref.recheck(
        api.RefRecheckParams(manifests=(runs["november"],), answers=answers)
    )
    (clinvar,) = [r for r in result.details["resources"] if r["id"] == "clinvar"]
    assert (clinvar["status"], clinvar["next_step"]) == ("unknown", None)
    assert "newer_release_available" not in [w.code for w in result.warnings]


def test_1179_an_unordered_release_is_not_warned_about_as_newer(runs, tmp_path):
    # Copilot review of #1363: different_release warned newer_release_available.
    answers = _answers(tmp_path / "answers.json", "weekly-48")
    result = api.ref.recheck(
        api.RefRecheckParams(manifests=(runs["november"],), answers=answers)
    )
    (clinvar,) = [r for r in result.details["resources"] if r["id"] == "clinvar"]
    assert clinvar["status"] == "different_release"
    codes = [w.code for w in result.warnings]
    assert "release_differs" in codes and "newer_release_available" not in codes
