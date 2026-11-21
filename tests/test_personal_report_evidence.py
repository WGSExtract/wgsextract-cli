"""Focused personal_report evidence regressions."""

import gzip
import json
import os
import shutil
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tests.htslib_helpers import needs_tools, sam_to_bam
from tests.test_personal_report import (
    TSV_HEADER as TSV_HEADER,
)
from tests.test_personal_report import (
    _cli as _cli,
)
from tests.test_personal_report import (
    _manifest as _manifest,
)
from tests.test_personal_report import (
    _touch as _touch,
)
from tests.test_personal_report import (
    _two_people as _two_people,
)
from tests.test_personal_report import (
    sample as sample,
)
from tests.test_personal_report import (
    served_lists as served_lists,
)
from wgsextract_cli import api
from wgsextract_cli.core import personal_report as pr
from wgsextract_cli.core.sample_workspace import SampleWorkspace
from wgsextract_cli.core.utils import UsageError


def test_an_input_limits_the_report_to_that_samples_runs(tmp_path):
    folder = _two_people(tmp_path / "shared")
    alice = folder / "Alice.cram"
    vcf = _touch(folder / "Alice.snps.vcf.gz")
    _manifest(
        folder,
        "Alice_snps",
        "vcf snp",
        inputs=((alice, "input"),),
        outputs=((vcf, "vcf"),),
    )
    _manifest(  # on a file made from Alice's alignment
        folder,
        "Alice_pharmcat",
        "vcf pharmcat",
        inputs=((vcf, "vcf_input"),),
        status="failed",
    )
    report = pr.build_report(folder, input_file=alice)
    assert report["sample"]["name"] == "Alice"
    (mt,) = report["sections"]["lineage"]["findings"]
    assert mt["value"] == "H1a"
    assert report["sections"]["pharmacogenomics"]["status"] == "failed"
    sources = {s["manifest"] for s in report["sections"]["lineage"]["sources"]}
    assert sources == {"Alice_haplogrep.manifest.json"}

    with pytest.raises(UsageError, match=r"Alice\.cram.*Bob\.cram"):
        pr.build_report(folder)
    code, out = _cli("report", "--workspace", str(folder))
    assert code == 2


@needs_tools("samtools")
def test_1221_report_distinguishes_same_named_bams_by_provenance(tmp_path):
    folder = tmp_path / "shared"
    first, second = folder / "first", folder / "second"
    for directory, base in ((first, "A"), (second, "T")):
        directory.mkdir(parents=True)
        sam_to_bam(
            [
                "@HD\tVN:1.6\tSO:coordinate",
                "@SQ\tSN:chr1\tLN:1000",
                f"read\t0\tchr1\t1\t60\t1M\t*\t0\t0\t{base}\tI",
            ],
            directory / "sample.bam",
        )

    def run(*args: str) -> dict[str, Any]:
        code, output = _cli(*args, "--json")
        assert code == 0, output
        return json.loads(output)

    first_bam = str(first / "sample.bam")
    sorted_run = run(
        "bam",
        "sort",
        "--input",
        first_bam,
        "--outdir",
        str(first),
        "--threads",
        "1",
    )
    descendant = run(
        "info",
        "--input",
        str(first / "sample_sorted.bam"),
        "--outdir",
        str(first),
        "--format",
        "tsv",
    )
    expected = {Path(result["manifest"]) for result in (sorted_run, descendant)}
    report_args = ("report", "--workspace", str(folder))
    connected = run(*report_args, "--outdir", str(tmp_path / "connected"))
    assert {Path(item["path"]) for item in connected["inputs"]} == expected

    run(
        "info",
        "--input",
        str(second / "sample.bam"),
        "--outdir",
        str(second),
        "--format",
        "tsv",
    )
    outdir = tmp_path / "report"
    report_args += ("--outdir", str(outdir))
    code, output = _cli(*report_args, "--json")
    refusal = json.loads(output)
    assert code == refusal["exit_code"] == 2
    assert refusal["error"]["kind"] == "usage"
    assert "--input" in refusal["error"]["message"]
    assert not list(outdir.glob("*.html"))
    for directory in (first, second):
        assert str(directory / "sample.bam") in refusal["error"]["message"]

    selected = run(*report_args, "--input", first_bam)
    assert {Path(item["path"]) for item in selected["inputs"]} == expected | {
        Path(first_bam)
    }
    (report_json,) = [
        Path(item["path"])
        for item in selected["outputs"]
        if item["role"] == "report_json"
    ]
    report = json.loads(report_json.read_text(encoding="utf-8"))
    assert report["sample"]["alignment"] == "first/sample.bam"
    assert [
        source["manifest"] for source in report["sections"]["overview"]["sources"]
    ] == [f"first/{Path(descendant['manifest']).name}"]


def test_in_a_workspace_runs_on_another_alignment_stay_out(tmp_path):
    folder = _two_people(tmp_path / "genome")
    (folder / "genome-config.toml").write_text("")
    outside_vcf = _touch(folder / "from_a_lab.vcf.gz")  # the same person's
    _manifest(
        folder,
        "lab_clinvar",
        "vcf clinvar",
        inputs=((outside_vcf, "vcf_input"),),
        status="failed",
        error="bcftools failed",
    )
    report = pr.build_report(folder, input_file=folder / "Alice.cram")
    assert [f["value"] for f in pr.findings(report)] == ["H1a"]
    assert report["sections"]["clinvar"]["status"] == "failed"


def test_failed_pharmcat_and_clinvar_runs_show_their_errors(sample):
    vcf = sample / "HG002.vcf.gz"
    for name, command in (("pgx", "vcf pharmcat"), ("cv", "vcf clinvar")):
        _manifest(
            sample,
            name,
            command,
            inputs=((vcf, "vcf_input"),),
            status="failed",
            error=f"{command} needs java",
            finished="2026-01-01T00:00:00+00:00",
        )
    (sample / "clinvar_pathogenic.vcf.manifest.json").unlink()
    sections = pr.build_report(sample)["sections"]
    assert sections["clinvar"]["status"] == "failed"
    assert "vcf clinvar needs java" in sections["clinvar"]["error"]
    pgx = sections["pharmacogenomics"]
    assert pgx["status"] == "ok"  # Cyrius ran
    assert "vcf pharmcat needs java" in pgx["error"]
    assert [m["command"].split()[1:3] for m in pgx["missing"]] == [
        ["vcf", "pharmcat"],
        ["typing", "hla"],
    ]


def test_859_newer_failed_clinvar_attempt_is_shown_beside_the_older_evidence(
    sample, tmp_path
):
    # Issue 859: an older successful ClinVar run hid a newer failed attempt.
    results = tmp_path / "newer-failure"
    results.mkdir()
    vcf = sample / "HG002.vcf.gz"
    _manifest(
        results,
        "clinvar_pathogenic.vcf",
        "vcf clinvar",
        inputs=((vcf, "vcf_input"),),
        outputs=((results / "clinvar_pathogenic.vcf.gz", "clinvar_pathogenic"),),
        status="failed",
        error="ClinVar file not found",
        finished="2026-01-02T00:00:00+00:00",
    )
    other = _touch(tmp_path / "other.vcf.gz")
    _manifest(
        results,
        "theirs",
        "vcf clinvar",
        inputs=((other, "vcf_input"),),
        outputs=((results / "theirs.vcf.gz", "clinvar_pathogenic"),),
        status="failed",
        error="another sample's failure",
        finished="2026-02-01T00:00:00+00:00",
    )
    code, out = _cli("report", "--workspace", str(sample), "--results", str(results))
    assert code == 0, out
    assert "newer attempt failed" in out

    document = json.loads((sample / "HG002_personal_report.json").read_text())
    jsonschema.validate(document, pr.load_schema())
    clinvar = document["sections"]["clinvar"]
    assert clinvar["status"] == "ok"
    assert "BRCA2 Pathogenic" in {f["label"] for f in clinvar["findings"]}
    [source] = clinvar["sources"]
    assert (source["status"], source["finished"]) == ("ok", "2025-01-01T00:00:00+00:00")
    assert source["latest_attempt"] == {
        "command": "vcf clinvar",
        "status": "failed",
        "finished": "2026-01-02T00:00:00+00:00",
        "manifest": "../newer-failure/clinvar_pathogenic.vcf.manifest.json",
    }
    [stale] = [w for w in clinvar["warnings"] if w["code"] == "newer_attempt_failed"]
    assert "ClinVar file not found" in stale["message"]
    assert "another sample" not in json.dumps(document)
    lineage = document["sections"]["lineage"]["sources"]
    assert [s["latest_attempt"] for s in lineage] == [None]

    for page in ("HG002_personal_report.html", "HG002_personal_report.md"):
        text = (sample / page).read_text()
        assert "Newer attempt failed:" in text
        assert "ClinVar file not found" in text
        assert "vcf clinvar (failed, 2026-01-02T00:00:00+00:00)" in text
        assert "vcf clinvar (ok, 2025-01-01T00:00:00+00:00)" in text

    manifest = json.loads((sample / "HG002_personal_report.manifest.json").read_text())
    assert [w["details"]["section"] for w in manifest["warnings"]
            if w["code"] == "newer_attempt_failed"] == ["clinvar"]  # fmt: skip


def test_859_a_rerun_that_succeeds_after_a_failure_is_not_stale(sample):
    _manifest(
        sample,
        "cv-failed",
        "vcf clinvar",
        inputs=((sample / "HG002.vcf.gz", "vcf_input"),),
        status="failed",
        finished="2024-12-01T00:00:00+00:00",
    )
    clinvar = pr.build_report(sample)["sections"]["clinvar"]
    assert clinvar["status"] == "ok"
    assert [s["latest_attempt"] for s in clinvar["sources"]] == [None]
    assert "newer_attempt_failed" not in {w["code"] for w in clinvar["warnings"]}


def test_messages_from_runs_keep_paths_out_of_the_documents(sample, tmp_path):
    table = sample / "clinvar_pathogenic.confidence.tsv"
    elsewhere = tmp_path / "secret-home" / "ref" / "clinvar.vcf.gz"
    path = sample / "clinvar_pathogenic.vcf.manifest.json"
    document = json.loads(path.read_text())
    document["warnings"] = [
        {
            "code": "clinical_findings",
            "message": f"1 ClinVar call is low confidence; see {table}",
        }
    ]
    path.write_text(json.dumps(document))
    _manifest(
        sample,
        "HG002_microarray",
        "microarray",
        inputs=((sample / "HG002.cram", "input"),),
        status="failed",
        error=f"cannot read {elsewhere}: no such file",
    )
    report = pr.build_report(sample)
    clinvar = report["sections"]["clinvar"]
    assert clinvar["warnings"][0]["message"].endswith(
        "see clinvar_pathogenic.confidence.tsv"
    )
    assert "cannot read clinvar.vcf.gz" in report["sections"]["microarray"]["error"]
    for text in (
        pr.render_html(report),
        pr.render_markdown(report),
        json.dumps(report),
    ):
        assert str(tmp_path) not in text and "secret-home" not in text


def test_a_build_comes_from_the_evidence_vcf_not_stale_facts(sample):
    # the workspace thinks the input VCF is hg19; the result's header says hg38
    SampleWorkspace(sample).record(
        "build", sample / "HG002.vcf.gz", "hg19", method="old", confidence="high"
    )
    with gzip.open(sample / "clinvar_pathogenic.vcf.gz", "wt") as stream:
        stream.write("##fileformat=VCFv4.2\n##contig=<ID=chr1,length=248956422>\n")
        stream.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
    clinvar = pr.build_report(sample)["sections"]["clinvar"]
    assert clinvar["build"] == "hg38"
    assert {f["gene"] for f in clinvar["findings"]} == {"BRCA2", "MUTYH"}


def test_with_only_a_vcf_the_overrides_still_apply(tmp_path):
    folder = tmp_path / "vcf-only"
    folder.mkdir()
    (folder / "genome-config.toml").write_text("")
    vcf = _touch(folder / "me.vcf.gz")
    _manifest(folder, "me_clinvar", "vcf clinvar", inputs=((vcf, "vcf_input"),))
    SampleWorkspace(folder).set_overrides({"sex": "Female"})
    report = pr.build_report(folder)
    facts = {f["name"]: f for f in report["sections"]["overview"]["facts"]}
    assert facts["sex"]["value"] == "Female"
    assert facts["sex"]["source"] == "override"
    lineage = report["sections"]["lineage"]
    assert [m["what"] for m in lineage["missing"]] == ["mtDNA haplogroup"]


def test_a_copied_folder_reports_on_its_own_evidence(sample, tmp_path):
    copy = Path(shutil.copytree(sample, tmp_path / "copy"))
    table = copy / "clinvar_pathogenic.confidence.tsv"
    table.write_text(TSV_HEADER)  # the copy has no calls; the original has 4
    report = pr.build_report(copy)
    clinvar = report["sections"]["clinvar"]
    assert clinvar["counts"]["carried"] == 0
    assert clinvar["all_calls"] == "clinvar_pathogenic.confidence.tsv"
    (mt,) = report["sections"]["lineage"]["findings"]
    assert mt["evidence"]["file"] == "haplogrep_results.txt"
    warnings = [w["code"] for s in report["sections"].values() for w in s["warnings"]]
    assert "evidence_missing" not in warnings


def test_results_on_another_drive_keep_their_location(sample, tmp_path, monkeypatch):
    elsewhere = tmp_path / "D"
    elsewhere.mkdir()
    kit = _touch(elsewhere / "HG002_CombinedKit.txt")
    _manifest(
        elsewhere,
        "kit",
        "microarray",
        inputs=((sample / "HG002.cram", "input"),),
        outputs=((kit, "combined_kit"),),
    )
    relpath = os.path.relpath

    def across_drives(path: str, start: str = os.curdir) -> str:
        if str(path).startswith(str(elsewhere)):
            raise ValueError("path is on mount 'D:', start on mount 'C:'")
        return relpath(path, start)

    monkeypatch.setattr(pr.os.path, "relpath", across_drives)
    report = pr.build_report(sample, results=[elsewhere])
    (file,) = report["sections"]["microarray"]["files"]
    assert Path(file["file"]) == kit
    assert f'href="{kit.as_uri()}"' in pr.render_html(report)


def test_report_from_an_alignment_needs_no_samtools(sample, monkeypatch):
    def no_samtools(path: Any) -> None:
        raise AssertionError("report checked the alignment with samtools")

    monkeypatch.setattr(
        "wgsextract_cli.core.alignment.input.validate_alignment_input", no_samtools
    )
    cram = sample / "HG002.cram"
    code, out = _cli("report", "-i", str(cram), "--outdir", str(sample / "out"))
    assert code == 0, out
    result = api.report.build(api.ReportParams(input=cram, outdir=sample / "out2"))
    assert result.exit_code == 0
