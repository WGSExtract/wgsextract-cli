"""Focused personal_report sections regressions."""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, write_fasta
from tests.test_panel_spec import _audit_inputs
from tests.test_personal_report import (
    _acmg_run as _acmg_run,
)
from tests.test_personal_report import (
    _acmg_section as _acmg_section,
)
from tests.test_personal_report import (
    _cli as _cli,
)
from tests.test_personal_report import (
    _manifest as _manifest,
)
from tests.test_personal_report import (
    _overview_contamination as _overview_contamination,
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
from wgsextract_cli.core.sample_workspace import SampleWorkspace


def test_pgx_and_lineage_labels_come_from_the_callers_evidence(sample):
    sections = pr.build_report(sample)["sections"]
    (cyp2d6,) = sections["pharmacogenomics"]["findings"]
    assert (cyp2d6["value"], cyp2d6["confidence"]) == ("*1/*2", "high")
    (mt,) = sections["lineage"]["findings"]
    assert (mt["value"], mt["confidence"]) == ("H1a", "high")
    assert any("H1a" in link["url"] for link in mt["links"])


@needs_htslib
def test_1287_report_shows_only_the_samples_research_audit(tmp_path: Path) -> None:
    panel, vcf, gvcf = _audit_inputs(tmp_path, phase_set="1")
    assert "panel_audit" not in pr.build_report(tmp_path, input_file=vcf)["sections"]
    api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=vcf,
            gvcf=gvcf,
            research_panel=True,
            outdir=tmp_path,
        )
    )
    _manifest(
        tmp_path,
        "later-same-panel",
        "panel audit",
        inputs=((vcf, "vcf_input"), (panel, "panel")),
        status="failed",
        finished="2099-01-01T00:00:00+00:00",
    )
    other_panel = _touch(tmp_path / "other-panel.toml")
    _manifest(
        tmp_path,
        "later-other-panel",
        "panel audit",
        inputs=((vcf, "vcf_input"), (other_panel, "panel")),
        status="failed",
        finished="2099-01-02T00:00:00+00:00",
    )
    result = api.report.build(api.ReportParams(input=vcf, outdir=tmp_path))
    outputs = {output.role: Path(output.path) for output in result.outputs}
    report = json.loads(outputs["report_json"].read_text(encoding="utf-8"))
    jsonschema.validate(report, pr.load_schema())
    section = report["sections"]["panel_audit"]
    assert section["research_only"] is True
    (source,) = section["sources"]
    assert source["latest_attempt"]["manifest"] == "later-same-panel.manifest.json"
    assert "newer_attempt_failed" in {w["code"] for w in section["warnings"]}
    (finding,) = section["findings"]
    assert finding["value"] == "2 qualifying allele copies"
    assert finding["evidence"]["file"] == "audit.panel_audit.json"
    for role in ("report_html", "report_markdown", "findings_table"):
        content = outputs[role].read_text(encoding="utf-8")
        assert "Research use only; not a diagnosis" in content
        assert "audit.panel_audit.json" in content


@needs_htslib
def test_1287_shared_panel_file_never_connects_another_samples_audit(
    tmp_path: Path,
) -> None:
    alice = tmp_path / "alice"
    bob = tmp_path / "bob"
    shared = tmp_path / "results"
    alice.mkdir()
    bob.mkdir()
    (alice / "genome-config.toml").touch()
    alignment = _touch(alice / "alice.bam")
    panel, calls, gvcf = _audit_inputs(bob, phase_set="1")
    panel = panel.rename(alice / "shared-panel.json")
    api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=calls,
            research_panel=True,
            outdir=shared,
        )
    )
    report = pr.build_report(alice, input_file=alignment, results=(shared,))
    assert "panel_audit" not in report["sections"]
    own_gvcf = gvcf.rename(alice / "sample.g.vcf")
    api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=calls,
            gvcf=own_gvcf,
            research_panel=True,
            outdir=shared,
        )
    )
    report = pr.build_report(alice, input_file=alignment, results=(shared,))
    assert report["sections"]["panel_audit"]["findings"][0]["value"] == (
        "2 qualifying allele copies"
    )


def test_cyp2d6_from_runs_before_issue_844_is_not_high_or_called_d6_copies(sample):
    # Issue 844: older runs recorded Cyrius's CYP2D6+CYP2D7 Total_CN as
    # cyp2d6_copy_number and no Call_info, so a unique match is unproven.
    manifest = sample / "manifests" / "HG002_cyp2d6.manifest.json"
    data = json.loads(manifest.read_text())
    del data["metrics"]["cyp2d6_call_status"]
    data["metrics"]["cyp2d6_copy_number"] = 4
    manifest.write_text(json.dumps(data))
    (cyp2d6,) = pr.build_report(sample)["sections"]["pharmacogenomics"]["findings"]
    assert (cyp2d6["value"], cyp2d6["confidence"]) == ("*1/*2", "moderate")
    assert cyp2d6["confidence_reasons"] == ["call_info_unknown"]
    assert "CYP2D6+CYP2D7 copy number 4" in cyp2d6["detail"]


def test_pharmcat_genes_take_their_weakest_labelled_call(sample):
    def diplotype(a: str, b: str, phenotype: str) -> dict[str, Any]:
        return {
            "recommendationDiplotypes": [
                {
                    "allele1": {"name": a},
                    "allele2": {"name": b},
                    "phenotypes": [phenotype],
                }  # fmt: skip
            ]
        }

    phenotypes = sample / "HG002.phenotype.json"
    phenotypes.write_text(
        json.dumps(
            {
                "geneReports": {
                    "CYP2C19": diplotype("*1", "*17", "Rapid Metabolizer"),
                    "SLCO1B1": diplotype("*1", "*1", "Normal Function"),
                }
            }
        )
    )
    table = sample / "HG002.confidence.tsv"
    table.write_text(
        "# PharmCAT calls\nchrom\tpos\tref\talt\tgenotype\tzygosity\tconfidence\t"
        "reasons\tdepth\tgenotype_quality\talt_fraction\tgene\n"
        "chr10\t1\tC\tT\t0/1\thet\thigh\t\t30\t99\t0.5\tCYP2C19\n"
        "chr10\t2\tG\tA\t0/1\thet\tmoderate\tmoderate_gq\t30\t25\t0.5\tCYP2C19\n"
    )
    report = _touch(sample / "HG002.report.html")
    _manifest(
        sample,
        "HG002.report.html",
        "vcf pharmcat",
        inputs=((sample / "HG002.vcf.gz", "input"),),
        outputs=(
            (report, "pharmcat_report"),
            (phenotypes, "pharmcat_phenotypes"),
            (table, "confidence_table"),
        ),
    )
    findings = pr.build_report(sample)["sections"]["pharmacogenomics"]["findings"]
    genes = {f["gene"]: f for f in findings}
    assert genes["CYP2C19"]["value"] == "*1/*17"
    assert genes["CYP2C19"]["confidence"] == "moderate"
    assert genes["CYP2C19"]["confidence_reasons"] == ["moderate_gq"]
    assert genes["SLCO1B1"]["confidence"] == "not_assessed"
    assert genes["CYP2C19"]["evidence"]["file"] == "HG002.report.html"
    assert genes["CYP2D6"]["confidence"] == "high"  # Cyrius


@needs_htslib
def test_842_non_definition_reference_blocks_do_not_downgrade_gene_report(sample):
    from wgsextract_cli.api.vcf import VcfPharmcatParams
    from wgsextract_cli.commands.vcf.pharmcat import label_pgx_calls, prepare_input
    from wgsextract_cli.core.variant_confidence import write_table

    release = sample / "release"
    release.mkdir()
    (release / "pharmcat_regions.bed").write_text("chr1\t100\t200\tPX=CYP2C19\n")
    (release / "pharmcat_positions.vcf").write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=400>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t105\t.\tAA\tA\t.\tPASS\t.\n"
    )
    reference = write_fasta(release / "ref.fa", {"chr1": "A" * 400})
    plain = sample / "input.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n##reference=GRCh38\n##contig=<ID=chr1,length=400>\n"
        '##INFO=<ID=END,Number=1,Type=Integer,Description="Block end">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Quality">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG002\n"
        "chr1\t101\t.\tA\t<NON_REF>\t.\tPASS\tEND=104\tGT:DP:GQ\t0/0:40:2\n"
        "chr1\t105\t.\tA\t<NON_REF>\t.\tPASS\tEND=106\tGT:DP:GQ\t0/0:40:60\n"
        "chr1\t107\t.\tA\t<NON_REF>\t.\tPASS\tEND=200\tGT:DP:GQ\t0/0:40:2\n"
    )
    vcf = bgzip_index(plain, sample / "HG002.vcf.gz")
    work = sample / "work"
    work.mkdir()
    prepare_input(vcf, str(release), str(reference), str(work))
    confidence = label_pgx_calls(vcf, str(work), VcfPharmcatParams())
    assert {(f.pos, f.ref) for f in confidence.findings} == {
        (105, "A"),
        (106, "A"),
        (105, "AA"),
    }
    table = sample / "reference-confidence.tsv"
    write_table(str(table), confidence, ("gene", "PGX_STATE"), "PharmCAT calls")
    phenotypes = sample / "reference.phenotype.json"
    phenotypes.write_text(
        json.dumps(
            {
                "geneReports": {
                    "CYP2C19": {
                        "recommendationDiplotypes": [
                            {
                                "allele1": {"name": "*1"},
                                "allele2": {"name": "*1"},
                                "phenotypes": ["Normal Metabolizer"],
                            }
                        ],
                    }
                }
            }
        )
    )
    _manifest(
        sample,
        "definition-reference",
        "vcf pharmcat",
        inputs=((Path(vcf), "input"),),
        outputs=((phenotypes, "pharmcat_phenotypes"), (table, "confidence_table")),
    )
    findings = pr.build_report(sample)["sections"]["pharmacogenomics"]["findings"]
    (gene,) = [f for f in findings if f["gene"] == "CYP2C19"]
    assert gene["confidence"] == "high"
    assert "low_gq" not in gene["confidence_reasons"]


@pytest.mark.parametrize(
    ("phenotypes", "status", "consistent"),
    [
        (["Normal Metabolizer", "Intermediate Metabolizer"], "ambiguous", False),
        (["Normal Metabolizer", "Normal Metabolizer"], "ambiguous", True),
        ([], "no_call", False),
    ],
)
def test_843_ambiguous_pgx_is_one_gene_without_definitive_conflicting_guidance(
    sample, phenotypes, status, consistent
):
    phenotype = sample / "ambiguous.phenotype.json"
    alternatives = [
        {"allele1": {"name": "*1"}, "allele2": {"name": allele}, "phenotypes": [name]}
        for allele, name in zip(("*1", "*2"), phenotypes, strict=False)
    ]
    if not alternatives:
        alternatives = [
            {"allele1": {"name": "Unknown"}, "allele2": {"name": "Unknown"}}
        ]
    phenotype.write_text(
        json.dumps(
            {
                "geneReports": {
                    "CYP2C19": {
                        "recommendationDiplotypes": alternatives,
                    }
                }
            }
        )
    )
    _manifest(
        sample,
        "ambiguous",
        "vcf pharmcat",
        inputs=((sample / "HG002.vcf.gz", "input"),),
        outputs=((phenotype, "pharmcat_phenotypes"),),
    )
    report = pr.build_report(sample)
    section = report["sections"]["pharmacogenomics"]
    (gene,) = [f for f in section["findings"] if f["gene"] == "CYP2C19"]
    assert gene["pgx"]["status"] == status
    assert gene["pgx"]["phenotype_consistent"] is consistent
    assert gene["pgx"]["source"] == "PharmCAT"
    assert gene["evidence"]["file"] == phenotype.name
    assert gene["evidence"]["manifest"]
    assert gene["confidence"] == "low"
    assert len(gene["pgx"]["alternatives"]) == len(alternatives)
    if status == "ambiguous":
        assert "*1/*1 OR *1/*2" in gene["value"]
        assert "1 ambiguous gene(s)" in section["summary"]
        assert "Ambiguous:" in pr.render_markdown(report)
        assert "Ambiguous:" in pr.render_html(report)
    if not consistent:
        assert "no definitive guidance" in gene["detail"]
    jsonschema.validate(report, pr.load_schema())


def test_low_coverage_warning_lowers_a_haplogroup_label(sample):
    _manifest(
        sample,
        "haplogrep",
        "lineage mt-haplogroup",
        outputs=((sample / "haplogrep_results.txt", "haplogroup_report"),),
        metrics={"haplogroup": "H1a", "haplogroup_quality": 0.99},
        warnings=(("low_coverage", "Low coverage."),),
    )
    (mt,) = pr.build_report(sample)["sections"]["lineage"]["findings"]
    assert (mt["confidence"], mt["confidence_reasons"]) == ("low", ["low_coverage"])


def test_a_failed_run_marks_its_section_failed(sample):
    _manifest(
        sample,
        "HG002_microarray",
        "microarray",
        inputs=((sample / "HG002.cram", "input"),),
        outputs=((sample / "HG002_CombinedKit.txt", "combined_kit"),),
        status="failed",
    )
    microarray = pr.build_report(sample)["sections"]["microarray"]
    assert microarray["status"] == "failed"
    assert "tool crashed" in microarray["error"]
    assert microarray["missing"][0]["command"].startswith("wgsextract microarray")


def test_outside_a_workspace_the_overview_falls_back_to_info(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    cram = _touch(folder / "s.cram")
    _manifest(
        folder,
        "s.cram_chromosomes.tsv",
        "info",
        inputs=((cram, "input"),),
        metrics={"sex": "Male", "ard_mapped": 39.03, "sequencer": "NovaSeq"},
    )
    report = pr.build_report(folder)
    facts = {f["name"]: f for f in report["sections"]["overview"]["facts"]}
    assert facts["sex"]["value"] == "Male"
    assert facts["sex"]["source"] == "info" and facts["sex"]["confidence"] is None
    assert facts["sex"]["file"] == "s.cram"
    assert facts["mean_coverage"]["text"] == "39x"
    assert "| Sex | Male |" in pr.render_markdown(report)
    assert "not recorded" in pr.render_markdown(report)
    jsonschema.validate(report, pr.load_schema())


@needs_htslib
def test_838_cli_proband_inconclusive_warning_survives_selected_input_report(tmp_path):
    paths = {}
    for role, gt in (("child", "0/1"), ("mother", "./."), ("father", "./.")):
        path = tmp_path / f"{role}.vcf"
        path.write_text(
            "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n"
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{role}\n"
            f"chr1\t100\tcandidate\tA\tG\t50\tPASS\t.\tGT\t{gt}\n"
        )
        paths[role] = str(path)
    code, output = _cli(
        "vcf",
        "trio",
        "--proband",
        paths["child"],
        "--mother",
        paths["mother"],
        "--father",
        paths["father"],
        "--outdir",
        str(tmp_path),
    )
    assert code == 0, output
    reportdir = tmp_path / "report"
    code, output = _cli("report", "--input", paths["child"], "--outdir", str(reportdir))
    assert code == 0, output
    document = json.loads((reportdir / "child_personal_report.json").read_text())
    overview = document["sections"]["overview"]
    assert "inconclusive_parent_genotypes" in {w["code"] for w in overview["warnings"]}
    assert (
        "inconclusive inheritance"
        in (reportdir / "child_personal_report.md").read_text()
    )
    # Family members are not merged into the child's sample identity.
    for role in ("mother", "father"):
        report = pr.build_report(tmp_path, input_file=Path(paths[role]))
        assert not report["sections"]["overview"]["sources"]


def test_838_failed_trio_alone_does_not_fail_sample_overview(tmp_path):
    child = _touch(tmp_path / "child.vcf")
    _manifest(
        tmp_path, "trio", "vcf trio", inputs=((child, "proband"),), status="failed"
    )
    overview = pr.build_report(tmp_path, input_file=child)["sections"]["overview"]
    assert overview["status"] == "not_run"
    assert not overview.get("error")


def test_issue_846_failed_str_does_not_fail_sample_overview(tmp_path):
    alignment = _touch(tmp_path / "sample.bam")
    _manifest(
        tmp_path,
        "str",
        "typing str",
        inputs=((alignment, "alignment"),),
        status="failed",
    )
    overview = pr.build_report(tmp_path, input_file=alignment)["sections"]["overview"]
    assert overview["status"] == "not_run"
    assert not overview.get("error")
    assert "section_failed" not in {w["code"] for w in overview["warnings"]}


def test_report_shows_only_the_selected_files_contamination(tmp_path):
    """PR #631 review: in a shared folder that isn't one person's genome
    folder, Bob's FREEMIX must not be reported (or warned about) for Alice."""
    folder = tmp_path / "shared"
    folder.mkdir()
    alice = _touch(folder / "Alice.bam")
    bob = _touch(folder / "Bob.bam", "y")
    workspace = SampleWorkspace(folder)
    workspace.record("contamination", bob, 0.08, method="VB2", confidence="high")

    value, codes = _overview_contamination(pr.build_report(folder, input_file=alice))
    assert value is None
    assert "sample_contamination" not in codes

    value, codes = _overview_contamination(pr.build_report(folder, input_file=bob))
    assert value == 0.08
    assert "sample_contamination" in codes


def test_report_falls_back_to_the_contamination_run_without_a_workspace(tmp_path):
    """PR #631 review: without a sample workspace FREEMIX is only in the run's
    manifest; the report shows it and doesn't ask for the check again."""
    folder = tmp_path / "loose"
    folder.mkdir()
    bam = _touch(folder / "sample.bam")
    _manifest(
        folder,
        "sample.contamination",
        "qc contamination",
        inputs=((bam, "input"),),
        metrics={"freemix": 0.05, "freemix_confidence": "high"},
        warnings=(("sample_contamination", "About 5.0% ..."),),
    )
    report = pr.build_report(folder, input_file=bam)
    value, codes = _overview_contamination(report)
    assert value == 0.05
    assert "sample_contamination" in codes
    overview = report["sections"]["overview"]
    assert "qc contamination" in {s["command"] for s in overview["sources"]}
    assert not any("qc contamination" in m["command"] for m in overview["missing"])


@needs_htslib
def test_629_acmg_section_lists_listed_genes_plp_calls_as_research_only(sample):
    _acmg_run(sample)
    report = pr.build_report(sample)
    jsonschema.validate(json.loads(json.dumps(report)), pr.load_schema())

    section = report["sections"]["acmg_research"]
    assert section["disclaimer"].startswith("Research use only; not a diagnosis")
    # The P call is in a gene off the list; the BRCA2 LP isn't carried.
    assert section["counts"] == {
        "p": 1, "lp": 1, "vus": 1, "lb": 0, "b": 0,
        "in_gene_list": 1, "outside_gene_list": 1, "unplaced": 0,
    }  # fmt: skip
    (finding,) = section["findings"]
    assert (finding["gene"], finding["value"]) == (
        "MUTYH",
        "Likely pathogenic (automated)",
    )
    assert "biallelic" in finding["note"]  # the gene list's reporting rule
    assert finding["disclaimer"] == "Research use only; not a diagnosis."
    assert finding["confidence"] == "high"
    assert "PVS1" in finding["detail"]
    assert finding["evidence"]["file"] == "acmg_annotated.vcf.gz"
    assert section["criteria_not_evaluated"] == ["PS3", "BS3", "PS4", "PP1", "PP4"]

    for page in (pr.render_html(report), pr.render_markdown(report)):
        # At the top of the section, and on its finding.
        assert page.count("Research use only; not a diagnosis.") >= 2
        assert "without a clinical genetics professional" in page
        assert "fastVEP 0.5.0" in page


@needs_htslib
def test_1023_acmg_section_names_the_one_sample_of_a_multi_sample_vcf(sample):
    """PR #1023 review: a trio VCF with a parent first must not pass the
    parent's calls off silently as the sample's."""
    _acmg_run(sample, samples=("MOTHER", "HG002", "FATHER"))
    section = _acmg_section(sample)
    (warning,) = [
        w for w in section["warnings"] if w["code"] == "confidence_first_sample_only"
    ]
    assert "MOTHER" in warning["message"] and "3-sample" in warning["message"]
    assert "bcftools view -s" in warning["message"]


@needs_htslib
def test_1023_a_damaged_acmg_vcf_leaves_the_rest_of_the_report(sample):
    """PR #1023 review: a truncated acmg_annotated.vcf.gz aborted the whole
    report."""
    vcf = _acmg_run(sample)
    vcf.write_bytes(vcf.read_bytes()[:-60])  # cut off the BGZF EOF and more
    report = pr.build_report(sample)
    jsonschema.validate(json.loads(json.dumps(report)), pr.load_schema())
    section = report["sections"]["acmg_research"]
    assert "unreadable_evidence" in {w["code"] for w in section["warnings"]}
    assert section["findings"] == []
    assert report["sections"]["clinvar"]["findings"]


@needs_htslib
def test_1023_rare_imputed_acmg_plp_is_counted_but_not_listed(sample):
    """PR #1023 review: an imputed LP rarer than 1% in the panel was listed
    as a finding (docs/variant-confidence.md)."""
    _acmg_run(sample, mutyh_info="IMP;RAF=0.001")
    section = _acmg_section(sample)
    assert section["findings"] == []
    assert section["counts"]["lp"] == 1  # still counted among the carried
    assert "rare_imputed_not_listed" in {w["code"] for w in section["warnings"]}


@needs_htslib
def test_1023_contaminated_sample_caps_acmg_findings_at_moderate(sample):
    """PR #1023 review: recorded FREEMIX above qc_max_contamination left a
    well-supported P/LP call at high confidence."""
    _acmg_run(sample)
    SampleWorkspace(sample).record(
        "contamination", sample / "HG002.cram", 0.08, method="VB2", confidence="high"
    )
    (finding,) = _acmg_section(sample)["findings"]
    assert finding["confidence"] == "moderate"
    assert "sample_contaminated" in finding["confidence_reasons"]
