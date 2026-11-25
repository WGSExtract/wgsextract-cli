"""Focused personal_report documents regressions."""

import json
import shutil
import socket
from pathlib import Path

import jsonschema
from markdown_it import MarkdownIt

from tests.test_personal_report import (
    HOSTILE as HOSTILE,
)
from tests.test_personal_report import (
    TSV_HEADER as TSV_HEADER,
)
from tests.test_personal_report import (
    _assert_shown_not_rendered as _assert_shown_not_rendered,
)
from tests.test_personal_report import (
    _cli as _cli,
)
from tests.test_personal_report import (
    _hostile as _hostile,
)
from tests.test_personal_report import (
    _manifest as _manifest,
)
from tests.test_personal_report import (
    _mid as _mid,
)
from tests.test_personal_report import (
    _touch as _touch,
)
from tests.test_personal_report import (
    sample as sample,
)
from tests.test_personal_report import (
    served_lists as served_lists,
)
from wgsextract_cli import api
from wgsextract_cli.core import personal_report as pr


def test_every_finding_carries_evidence_and_the_disclaimer_and_json_is_valid(
    sample,
):
    report = pr.build_report(sample)
    jsonschema.Draft202012Validator.check_schema(pr.load_schema())
    jsonschema.validate(json.loads(json.dumps(report)), pr.load_schema())
    findings = pr.findings(report)
    assert len(findings) == 4
    for finding in findings:
        assert finding["disclaimer"] == "Technical result, not diagnosis."
        assert finding["confidence"] in pr.CONFIDENCE
        assert finding["evidence"]["region"]
        assert (sample / finding["evidence"]["file"]).is_file()
        assert (sample / finding["evidence"]["manifest"]).is_file()
    html = pr.render_html(report)
    markdown = pr.render_markdown(report)
    # One disclaimer per finding plus the banner at the top.
    assert html.count("Technical result, not diagnosis.") == len(findings) + 1
    assert markdown.count("Technical result, not diagnosis.") == len(findings) + 1


def test_text_from_results_is_shown_as_text_in_html_and_markdown(sample):
    pos = _mid("BRCA2")
    rows = [*pos, *[HOSTILE] * 4, "high", "", 30, 99, 0.5, f"Pathogenic{HOSTILE}"]
    rows.append(HOSTILE)
    table = sample / "clinvar_pathogenic.confidence.tsv"
    table.write_text(TSV_HEADER + "\t".join(map(str, rows)) + "\n")
    haplogrep = json.loads(
        (sample / "manifests" / "haplogrep.manifest.json").read_text()
    )
    haplogrep["metrics"]["haplogroup"] = HOSTILE
    (sample / "manifests" / "haplogrep.manifest.json").write_text(json.dumps(haplogrep))
    report = pr.build_report(sample)
    (clinvar,) = report["sections"]["clinvar"]["findings"]
    assert {clinvar["variant"][k] for k in ("ref", "alt", "genotype", "zygosity")} == {
        HOSTILE
    }
    assert HOSTILE.replace("_", " ") in clinvar["detail"]
    assert report["sections"]["lineage"]["findings"][0]["value"] == HOSTILE

    # Every other string (sample name, folder, files, regions, labels...) too.
    report = _hostile(report)
    html_tags = {"html", "head", "meta", "title", "style", "body", "h1", "h2"}
    html_tags |= {"p", "b", "i", "br", "code", "span", "a", "ul", "li"}
    html_tags |= {"table", "tr", "th", "td"}
    _assert_shown_not_rendered(pr.render_html(report), html_tags)
    markdown = MarkdownIt("commonmark", {"html": True}).enable("table")
    md_tags = {"h1", "h2", "p", "blockquote", "strong", "em", "code", "a", "ul"}
    md_tags |= {"li", "table", "thead", "tbody", "tr", "th", "td"}
    _assert_shown_not_rendered(markdown.render(pr.render_markdown(report)), md_tags)


def test_a_moved_sample_folder_still_finds_its_evidence(sample, tmp_path):
    moved = Path(shutil.move(sample, tmp_path / "copied" / "HG002-renamed"))
    report = pr.build_report(moved)
    assert len(pr.findings(report)) == 4
    for finding in pr.findings(report):
        assert (moved / finding["evidence"]["file"]).is_file()
    warnings = [w["code"] for s in report["sections"].values() for w in s["warnings"]]
    assert "evidence_missing" not in warnings


def test_results_folders_contribute_only_this_samples_runs(sample, tmp_path):
    elsewhere = tmp_path / "results"
    elsewhere.mkdir()
    other = _touch(tmp_path / "other.cram")
    kit = _touch(elsewhere / "HG002_CombinedKit.txt")
    _manifest(
        elsewhere,
        "mine",
        "microarray",
        inputs=((sample / "HG002.cram", "input"),),
        outputs=((kit, "combined_kit"),),
    )
    _manifest(
        elsewhere,
        "theirs",
        "info",
        inputs=((other, "input"),),
        outputs=((_touch(elsewhere / "other.tsv"), "chromosome_table"),),
        metrics={"ard_mapped": 31.0},
    )
    report = pr.build_report(sample, results=[elsewhere])
    microarray = report["sections"]["microarray"]
    assert microarray["status"] == "ok"
    assert microarray["files"][0]["file"] == "../results/HG002_CombinedKit.txt"
    assert report["sections"]["overview"]["qc"] == []


def test_cli_writes_offline_self_contained_documents(sample, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("report made a network call")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    code, out = _cli("report", "--workspace", str(sample))
    assert code == 0, out
    assert "Technical result, not diagnosis." in out

    html = (sample / "HG002_personal_report.html").read_text()
    markdown = (sample / "HG002_personal_report.md").read_text()
    document = json.loads((sample / "HG002_personal_report.json").read_text())
    jsonschema.validate(document, pr.load_schema())
    for remote in ("<script", "<link", "src=", "@import", "url("):
        assert remote not in html
    assert 'href="clinvar_pathogenic.vcf.gz"' in html
    for text in (html, markdown, json.dumps(document)):
        assert str(sample) not in text

    rows = (sample / "HG002_personal_report_findings.tsv").read_text().splitlines()
    rows = [r for r in rows if not r.startswith("#")]
    assert len(rows) == 1 + len(pr.findings(document))

    manifest = json.loads(
        (sample / "manifests" / "HG002_personal_report.manifest.json").read_text()
    )
    assert manifest["status"] == "ok"
    assert {o["role"] for o in manifest["outputs"]} == {
        "report_html",
        "report_markdown",
        "report_json",
        "findings_table",
    }
    assert manifest["metrics"]["findings_low"] == 1
    assert manifest["metrics"]["section_microarray"] == "not_run"


def test_api_report_to_another_folder_links_back(sample, tmp_path):
    outdir = tmp_path / "reports"
    result = api.report.build(api.ReportParams(workspace=sample, outdir=outdir))
    assert result.details["schema"] == "wgsextract.report/1"
    html = (outdir / "HG002_personal_report.html").read_text()
    assert 'href="../HG002/clinvar_pathogenic.vcf.gz"' in html
    # A second report does not read the first one as a source.
    again = api.report.build(api.ReportParams(workspace=sample, outdir=outdir))
    assert len(pr.findings(again.details)) == len(pr.findings(result.details))
