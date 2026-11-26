"""Focused personal_report clinvar regressions."""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tests.htslib_helpers import needs_tools
from tests.test_1052_offline_defaults import blocked_network as blocked_network
from tests.test_personal_report import (
    ACMG_SF_TABLE_1 as ACMG_SF_TABLE_1,
)
from tests.test_personal_report import (
    MINI_LIST as MINI_LIST,
)
from tests.test_personal_report import (
    MUTYH_CALLS as MUTYH_CALLS,
)
from tests.test_personal_report import (
    TSV_HEADER as TSV_HEADER,
)
from tests.test_personal_report import (
    _bgzipped_vcf as _bgzipped_vcf,
)
from tests.test_personal_report import (
    _cli as _cli,
)
from tests.test_personal_report import (
    _clinvar_table as _clinvar_table,
)
from tests.test_personal_report import (
    _mid as _mid,
)
from tests.test_personal_report import (
    _Page as _Page,
)
from tests.test_personal_report import (
    _phased_mutyh_report as _phased_mutyh_report,
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
from wgsextract_cli.core import gene_lists
from wgsextract_cli.core import personal_report as pr
from wgsextract_cli.core.config import settings


def test_built_in_lists_match_the_cited_acmg_sf_tables():
    # The pinned manifest as shipped (the served_lists fixture replaces its lists).
    pinned = json.loads(
        Path(gene_lists.__file__)
        .parent.parent.joinpath("assets/report/gene_lists.json")
        .read_text()
    )
    lists = pinned["lists"]
    assert pinned["default"] == "acmg-sf-3.3"
    for meta in lists.values():
        by_category: dict[str, int] = {}
        for category in meta["genes"].values():
            by_category[category] = by_category.get(category, 0) + 1
        assert by_category == ACMG_SF_TABLE_1[meta["version"]]
    v32, v33 = (lists[f"acmg-sf-{v}"]["genes"] for v in ("3.2", "3.3"))
    # v3.3 adds three genes and moves HFE to inborn errors of metabolism.
    assert set(v33) - set(v32) == {"ABCD1", "CYP27A1", "PLN"}
    assert set(v32) < set(v33)
    assert (v32["HFE"], v33["HFE"]) == ("miscellaneous", "metabolism")


def test_built_in_list_is_downloaded_once_and_checked_on_each_use(sample, served_lists):
    first = pr.build_report(sample)["sections"]["clinvar"]
    assert first["gene_list"]["id"] == "acmg-sf-3.3"
    assert first["title"].endswith("in ACMG SF v3.3 genes")
    assert pr.build_report(sample)["sections"]["clinvar"]["counts"]["in_gene_list"] == 2
    assert served_lists.downloads == ["https://example.invalid/acmg-sf-3.3.tsv"]
    (stored,) = served_lists.library.joinpath("report", "gene_lists").glob("*.tsv")
    # A changed copy fails its pinned SHA-256 and is downloaded again.
    stored.write_text(MINI_LIST.replace("MUTYH", "MUTYX"))
    again = pr.build_report(sample)["sections"]["clinvar"]
    assert {f["gene"] for f in again["findings"]} == {"BRCA2", "MUTYH"}
    assert len(served_lists.downloads) == 2


def test_1052_report_missing_gene_list_requires_download(
    sample, served_lists, blocked_network, monkeypatch
):
    monkeypatch.setitem(settings, "allow_reference_downloads", False)
    refused = api.report.build(api.ReportParams(workspace=sample), check=False)
    assert refused.exit_code == 2
    assert refused.error and "gene list" in refused.error.message
    assert "--download" in refused.error.message
    assert not served_lists.downloads
    accepted = api.report.build(api.ReportParams(workspace=sample, download=True))
    assert accepted.exit_code == 0
    assert len(served_lists.downloads) == 1
    assert api.report.build(api.ReportParams(workspace=sample)).exit_code == 0
    assert not blocked_network


@pytest.mark.parametrize("problem", ["offline", "tampered"])
def test_without_a_verified_list_clinvar_calls_are_not_placed(
    sample, served_lists, problem
):
    if problem == "offline":
        served_lists.online = False
    else:
        served_lists.body = MINI_LIST.replace("BRCA2", "BRCA3").encode()
    _clinvar_table(
        sample,
        (*_mid("BRCA2"), "Pathogenic", "BRCA2:675"),
        (*_mid("MUTYH"), "Pathogenic", "MUTYH:4595"),
        ("chr6", 1000, "Likely_pathogenic", "OTHER:1"),
    )
    code, _ = _cli("report", "--workspace", str(sample))
    assert code == 0
    report = json.loads((sample / "HG002_personal_report.json").read_text())
    clinvar = report["sections"]["clinvar"]
    assert [w["code"] for w in clinvar["warnings"]] == ["gene_list_unavailable"]
    assert "--gene-list" in clinvar["warnings"][0]["message"]
    assert clinvar["findings"] == []
    # Unplaced calls are not claimed to lie outside the list.
    assert clinvar["counts"]["carried"] == 3
    assert clinvar["counts"]["outside_gene_list"] == 0
    assert clinvar["counts"]["unplaced"] == 3
    assert "not placed in genes" in clinvar["summary"]
    assert report["sections"]["lineage"]["findings"]


def test_a_report_without_clinvar_calls_downloads_no_list(sample, served_lists):
    served_lists.online = False
    _clinvar_table(sample, (*_mid("BRCA2"), "Benign", "BRCA2:675"))
    clinvar = pr.build_report(sample)["sections"]["clinvar"]
    assert clinvar["findings"] == []
    assert clinvar["gene_list"]["genes"] == 3
    assert served_lists.downloads == [] and clinvar["warnings"] == []


def test_geneinfo_names_the_gene_and_spans_are_the_fallback(sample):
    brca2, mutyh = _mid("BRCA2"), _mid("MUTYH")
    _clinvar_table(
        sample,
        (*brca2, "Pathogenic", "ZAR1L:345862"),  # in BRCA2's span, ClinVar: ZAR1L
        ("chr6", 1000, "Pathogenic", "PLN:5350|OTHER:1"),  # outside every span
        (*mutyh, "Likely_pathogenic", "."),  # no GENEINFO: placed by span
        ("chr6", 2000, "Pathogenic", "BRCA2:675|MUTYH:4595"),  # two listed genes
    )
    clinvar = pr.build_report(sample)["sections"]["clinvar"]
    findings = {f["evidence"]["region"]: f for f in clinvar["findings"]}
    assert {region: f["gene"] for region, f in findings.items()} == {
        f"chr1:{mutyh[1]}": "MUTYH",
        "chr6:1000": "PLN",
        "chr6:2000": "BRCA2/MUTYH",
    }
    assert findings["chr6:2000"]["note"].startswith("MUTYH: ")
    assert clinvar["counts"]["outside_gene_list"] == 1


@pytest.mark.parametrize(
    ("mutyh", "status"),
    [
        (("het",), "carrier"),
        (("het", "het"), "possible_biallelic"),
        (("hom",), "biallelic"),
        # Review of #482: neither shows the other copy.
        (("haploid",), "possible_biallelic"),
        (("partial",), "possible_biallelic"),
    ],
)
def test_recessive_genes_count_calls_by_zygosity_for_carrier_status(
    sample, mutyh, status
):
    # Issue #398: an AR gene is reportable only with two P/LP alleles.
    chrom, pos = _mid("MUTYH")
    _clinvar_table(
        sample,
        *((chrom, pos + i, "Pathogenic", "MUTYH:4595", z) for i, z in enumerate(mutyh)),
        (*_mid("BRCA2"), "Pathogenic", "BRCA2:675", "hom"),
    )
    report = pr.build_report(sample)
    jsonschema.validate(json.loads(json.dumps(report)), pr.load_schema())
    clinvar = report["sections"]["clinvar"]
    genes = {g["gene"]: g for g in clinvar["genes"]}
    assert genes["MUTYH"]["inheritance"] == "AR"
    assert genes["MUTYH"]["status"] == status
    columns = ("heterozygous", "homozygous", "hemizygous", "partial")
    counts = [genes["MUTYH"][c] for c in columns]
    assert counts == [mutyh.count(k) for k in ("het", "hom", "haploid", "partial")]
    # A dominant gene's calls are counted but get no carrier status.
    assert (genes["BRCA2"]["homozygous"], genes["BRCA2"]["status"]) == (1, None)
    shown = status.replace("_", " ")
    assert f"Recessive genes: MUTYH {shown}." in clinvar["summary"]
    assert ("phase is unknown" in genes["MUTYH"]["status_text"]) == (
        status == "possible_biallelic"
    )
    markdown = pr.render_markdown(report)
    assert f"| MUTYH | AR | {' | '.join(map(str, counts))} | high | **{shown}**: " \
        in markdown  # fmt: skip
    page = _Page()
    page.feed(pr.render_html(report))
    assert shown in page.text


@needs_tools("bcftools", "bgzip", "tabix")
@pytest.mark.parametrize(
    ("calls", "status"),
    [
        # Issue #483: annotation splits 1/2 into 1/0 and 0/1, but the two
        # ALTs are still on opposite copies.
        (["{pos} . A C,G 50 PASS . GT 1/2"], "biallelic"),
        # Two separate het calls: phase unknown.
        (["{pos} . A C 50 PASS . GT 0/1", "{next} . A G 50 PASS . GT 0/1"],
         "possible_biallelic"),
        # Review of #485: a triploid 0/1/2 keeps a reference copy.
        (["{pos} . A C,G 50 PASS . GT 0/1/2"], "possible_biallelic"),
    ],
)  # fmt: skip
def test_two_pathogenic_alts_of_one_call_are_biallelic(sample, calls, status):
    from tests.htslib_helpers import reference_for_vcf

    chrom, pos = _mid("MUTYH")
    fields = {"pos": pos, "next": pos + 5}
    vcf = _bgzipped_vcf(
        sample / "calls.vcf.gz", "", [c.format(**fields) for c in calls]
    )
    info = "".join(
        f'##INFO=<ID={tag},Number=.,Type=String,Description="{tag}">\n'
        for tag in ("CLNSIG", "CLNDN", "GENEINFO")
    )
    clinvar = _bgzipped_vcf(
        sample / "clinvar.vcf.gz",
        info,
        [f"{p} {i} A {alt} . . CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1"
         for i, (p, alt) in enumerate([(pos, "C"), (pos, "G"), (pos + 5, "G")])],
    )  # fmt: skip
    reference = reference_for_vcf(sample / "ref.fa.gz", vcf)
    code, _ = _cli("vcf", "clinvar", "--vcf-input", vcf, "--clinvar-file", clinvar,
                   "--outdir", str(sample), "--ref", str(reference))  # fmt: skip
    assert code == 0
    clinvar_section = pr.build_report(sample)["sections"]["clinvar"]
    (mutyh,) = clinvar_section["genes"]
    assert (mutyh["gene"], mutyh["heterozygous"], mutyh["status"]) == (
        "MUTYH",
        2,
        status,
    )
    assert f"MUTYH {status.replace('_', ' ')}" in clinvar_section["summary"]


@needs_tools("bcftools", "bgzip", "tabix")
@pytest.mark.parametrize(
    ("second", "status", "phase", "shown"),
    [
        ("0|1", "carrier", "cis", "on the same copy (cis)"),
        ("1|0", "biallelic", "trans", "on opposite copies (trans)"),
    ],
)
def test_known_cis_and_trans_mutyh_calls_get_different_statuses(
    tmp_path, second, status, phase, shown
):
    # Issue #840: both proven cis and proven trans MUTYH pairs showed
    # "Possible biallelic"; phasing in one PS block settles which it is.
    (first_pos, first_ref, first_alt), (pos, ref, alt) = MUTYH_CALLS
    mutyh, page = _phased_mutyh_report(
        tmp_path,
        [f"{first_pos} {first_ref} {first_alt} GT:PS 0|1:45329439",
         f"{pos} {ref} {alt} GT:PS {second}:45329439"],
    )  # fmt: skip
    assert (mutyh["heterozygous"], mutyh["status"], mutyh["phase"]) == (
        2,
        status,
        phase,
    )
    assert f"MUTYH {status} ({phase})" in mutyh["summary"]
    assert shown in mutyh["status_text"]
    assert shown in page
    assert "PS 45329439" in page


@needs_tools("bcftools", "bgzip", "tabix")
@pytest.mark.parametrize(
    ("second", "calls_from", "status", "phase"),
    [
        ("0|1", "manifest", "carrier", "cis"),
        ("1|0", "manifest", "biallelic", "trans"),
        # Review of #877: more calls could break a cis carrier, not a trans pair.
        ("0|1", "truncated", None, None),
        ("1|0", "truncated", "biallelic", "trans"),
    ],
)
def test_known_phase_survives_reading_the_manifest_findings(
    tmp_path, second, calls_from, status, phase
):
    # Issue #840: without the confidence table the report reads the calls'
    # phase sets from the run's manifest.
    (first_pos, first_ref, first_alt), (pos, ref, alt) = MUTYH_CALLS
    mutyh, _ = _phased_mutyh_report(
        tmp_path,
        [f"{first_pos} {first_ref} {first_alt} GT:PS 0|1:45329439",
         f"{pos} {ref} {alt} GT:PS {second}:45329439"],
        calls_from,
    )  # fmt: skip
    assert (mutyh["status"], mutyh["phase"]) == (status, phase)


@needs_tools("bcftools", "bgzip", "tabix")
@pytest.mark.parametrize(
    ("calls", "phase"),
    [
        # Issue #840: pipes in different phase sets were phased separately.
        (["45329439 T A GT:PS 0|1:100", "45330515 C A GT:PS 1|0:200"], "unknown"),
        # A missing PS doesn't say which pipes were phased together ...
        (["45329439 T A GT:PS 0|1:.", "45330515 C A GT:PS 1|0:."], "unknown"),
        # ... nor does a VCF phased without PS (e.g. statistical phasing).
        (["45329439 T A GT 0|1", "45330515 C A GT 1|0"], "unknown"),
        # A partly called genotype doesn't show its other copy.
        (["45329439 T A GT:PS 1|.:100", "45330515 C A GT:PS 0|1:100"], None),
        # Two ALTs of a triploid call share a PS, but it keeps a reference copy.
        (["45329439 T A,G GT:PS 0|1|2:100"], "unknown"),
    ],
)  # fmt: skip
def test_phase_that_is_not_known_stays_possible_biallelic(tmp_path, calls, phase):
    mutyh, _ = _phased_mutyh_report(tmp_path, calls)
    assert (mutyh["status"], mutyh["phase"]) == ("possible_biallelic", phase)
    assert "phase is unknown" in mutyh["status_text"]


def test_a_split_tag_without_alt_numbers_proves_nothing(sample):
    # Review of #485: an input normalized elsewhere may carry a four-field
    # OLD_MULTIALLELIC (no ALT number); two het calls sharing its record part
    # are still of unknown phase.
    chrom, pos = _mid("MUTYH")
    header = TSV_HEADER.replace("CLNDN\n", "CLNDN\tGENEINFO\tOLD_MULTIALLELIC\n")
    rows = [
        f"{chrom}\t{pos}\tA\t{alt}\t0/1\thet\thigh\t\t30\t99\t0.5\tPathogenic\t"
        f"MAP\tMUTYH:4595\t{chrom}|{pos}|A|{alt}\n"
        for alt in ("C", "G")
    ]
    (sample / "clinvar_pathogenic.confidence.tsv").write_text(header + "".join(rows))
    (mutyh,) = pr.build_report(sample)["sections"]["clinvar"]["genes"]
    assert (mutyh["heterozygous"], mutyh["status"]) == (2, "possible_biallelic")


def test_a_status_from_weak_calls_says_so(sample):
    # Review of #482: a gene's status is only as sure as its weakest call.
    chrom, pos = _mid("MUTYH")
    _clinvar_table(
        sample,
        (chrom, pos, "Pathogenic", "MUTYH:4595", "het", "high"),
        (chrom, pos + 10, "Pathogenic", "MUTYH:4595", "het", "low"),
        (*_mid("BRCA2"), "Pathogenic", "BRCA2:675", "het", "moderate"),
    )
    clinvar = pr.build_report(sample)["sections"]["clinvar"]
    genes = {g["gene"]: g["confidence"] for g in clinvar["genes"]}
    assert genes == {"BRCA2": "moderate", "MUTYH": "low"}
    assert "MUTYH possible biallelic (low-confidence calls)." in clinvar["summary"]


def test_statuses_from_a_truncated_call_list_are_only_the_certain_ones(sample):
    # Review of #482: without the confidence table the manifest lists only
    # the first 1,000 calls, so a carrier might have more.
    def finding(pos: int, zygosity: str) -> dict[str, Any]:
        genotype = {"het": "0/1", "hom": "1/1"}[zygosity]
        return {"chrom": "chr1", "pos": pos, "genotype": genotype,
                "zygosity": zygosity, "confidence": "high", "reasons": [],
                "CLNSIG": "Pathogenic", "GENEINFO": "MUTYH:4595"}  # fmt: skip

    manifest = sample / "manifests" / "clinvar_pathogenic.vcf.manifest.json"
    document = json.loads(manifest.read_text())
    document["outputs"] = [
        o for o in document["outputs"] if o["role"] != "confidence_table"
    ]
    document["details"] = {"confidence": [{"role": "clinvar_pathogenic",
                                           "findings": [finding(1000, "het")],
                                           "findings_truncated": True}]}  # fmt: skip
    manifest.write_text(json.dumps(document))
    clinvar = pr.build_report(sample)["sections"]["clinvar"]
    assert [w["code"] for w in clinvar["warnings"]] == ["calls_truncated"]
    (mutyh,) = clinvar["genes"]
    assert (mutyh["heterozygous"], mutyh["status"]) == (1, None)

    document["details"]["confidence"][0]["findings"].append(finding(2000, "hom"))
    manifest.write_text(json.dumps(document))
    (mutyh,) = pr.build_report(sample)["sections"]["clinvar"]["genes"]
    assert mutyh["status"] == "biallelic"


def test_a_list_without_inheritance_counts_calls_but_gives_no_status(sample, tmp_path):
    listed = tmp_path / "genes.tsv"
    listed.write_text("gene\nMUTYH\n")
    chrom, pos = _mid("MUTYH")
    _clinvar_table(sample, (chrom, pos, "Pathogenic", "MUTYH:4595", "hom"))
    clinvar = pr.build_report(sample, gene_list=listed)["sections"]["clinvar"]
    assert clinvar["genes"] == [
        {
            "gene": "MUTYH",
            "inheritance": None,
            "heterozygous": 0,
            "homozygous": 1,
            "hemizygous": 0,
            "partial": 0,
            "phase": None,
            "confidence": "high",
            "status": None,
            "status_text": "",
        }
    ]
    assert "Recessive genes" not in clinvar["summary"]


def test_a_call_in_two_listed_genes_counts_for_each(sample):
    _clinvar_table(
        sample,
        ("chr6", 2000, "Pathogenic", "BRCA2:675|MUTYH:4595"),
        ("chr6", 3000, "Pathogenic", "MUTYH:4595"),
    )
    clinvar = pr.build_report(sample)["sections"]["clinvar"]
    genes = {g["gene"]: (g["heterozygous"], g["status"]) for g in clinvar["genes"]}
    assert genes == {"BRCA2": (1, None), "MUTYH": (2, "possible_biallelic")}


def test_gene_list_from_the_flag_or_config_is_a_tsv_or_bed(
    sample, tmp_path, monkeypatch, served_lists
):
    brca2, mutyh = _mid("BRCA2"), _mid("MUTYH")
    # A BED file names no build: its spans are in the VCF's (0-based starts).
    bed = tmp_path / "my_panel.bed"
    bed.write_text(
        f"track name=mine\n{mutyh[0]}\t{mutyh[1] - 1}\t{mutyh[1]}\tMUTYH\n"
        f"{brca2[0]}\t{brca2[1]}\t{brca2[1] + 10}\tBRCA2\n"
    )
    code, _ = _cli("report", "--workspace", str(sample), "--gene-list", str(bed))
    assert code == 0
    report = json.loads((sample / "HG002_personal_report.json").read_text())
    jsonschema.validate(report, pr.load_schema())
    clinvar = report["sections"]["clinvar"]
    assert [f["gene"] for f in clinvar["findings"]] == ["MUTYH"]
    assert clinvar["title"].endswith("in my_panel.bed genes")
    assert clinvar["gene_list"] == {
        "id": "my_panel.bed",
        "name": "my_panel.bed",
        "version": "",
        "citation": "",
        "url": "",
        "genes": 2,
    }
    assert str(tmp_path) not in json.dumps(report)
    markdown = (sample / "HG002_personal_report.md").read_text()
    assert "Gene list: my\\_panel.bed (2 genes)." in markdown

    # A list of symbols alone relies on ClinVar's GENEINFO: a call without it
    # is unplaced, not outside the list.
    symbols = tmp_path / "symbols.tsv"
    symbols.write_text("# mine\ngene\nBRCA2\n")
    monkeypatch.setitem(settings, "report_gene_list", str(symbols))
    _clinvar_table(
        sample,
        (*brca2, "Pathogenic", "BRCA2:675"),
        (*mutyh, "Pathogenic", ""),
    )
    result = api.report.build(api.ReportParams(workspace=sample))
    clinvar = result.details["sections"]["clinvar"]
    assert [f["gene"] for f in clinvar["findings"]] == ["BRCA2"]
    assert (clinvar["counts"]["outside_gene_list"], clinvar["counts"]["unplaced"]) == (
        0,
        1,
    )
    assert [w["code"] for w in clinvar["warnings"]] == ["gene_annotation_missing"]
    assert "1 not placed" in clinvar["summary"]
    assert clinvar["gene_list"]["id"] == "symbols.tsv"
    assert served_lists.downloads == []


def test_a_bed_gene_keeps_every_interval(sample, tmp_path):
    # Review of #477: a PAR gene (on chrX and chrY) stopped the report, and a
    # symbol repeated far apart merged into one span over the genes between.
    bed = tmp_path / "genome_wide.bed"
    bed.write_text(
        "chrX\t600000\t650000\tSHOX\n"
        "chrY\t600000\t650000\tSHOX\n"
        "chr1\t1000\t2000\tSNORD3\n"
        "chr1\t9000000\t9001000\tSNORD3\n"
    )
    _clinvar_table(
        sample,
        ("chrY", 620000, "Pathogenic", "."),
        ("chr1", 9000500, "Pathogenic", "."),
        ("chr1", 5000000, "Pathogenic", "."),  # between SNORD3's copies
        ("chr2", 620000, "Pathogenic", "."),  # SHOX's position, other contig
    )
    clinvar = pr.build_report(sample, gene_list=str(bed))["sections"]["clinvar"]
    assert sorted(
        (f["gene"], f["evidence"]["region"]) for f in clinvar["findings"]
    ) == [
        ("SHOX", "chrY:620000"),
        ("SNORD3", "chr1:9000500"),
    ]
    assert clinvar["counts"]["outside_gene_list"] == 2
    assert clinvar["gene_list"]["genes"] == 2


def test_an_unknown_gene_list_is_a_usage_error(sample):
    code, _ = _cli("report", "--workspace", str(sample), "--gene-list", "acmg-sf-9")
    assert code == 2
    assert not list(sample.glob("*_personal_report*"))


@pytest.mark.parametrize(
    ("name", "text", "error"),
    [
        ("no_symbol.bed", "chr1\t10\t20\n", "column 4"),
        ("negative.bed", "chr1\t-5\t20\tBRCA2\n", "bad interval"),
        ("empty.bed", "chr1\t20\t20\tBRCA2\n", "bad interval"),
        ("no_end.tsv", "gene\tchrom\tstart\nBRCA2\tchr13\t5\n", "bad span"),
        ("inverted.tsv", "gene\thg38_chrom\thg38_start\thg38_end\nBRCA2\tchr13\t9\t5\n", "bad span"),
    ],
)  # fmt: skip
def test_a_malformed_gene_list_file_is_a_usage_error(
    sample, tmp_path, name, text, error
):
    bad = tmp_path / name
    bad.write_text(text)
    with pytest.raises(api.CommandFailed, match=error):
        api.report.build(api.ReportParams(workspace=sample, gene_list=str(bad)))
    assert not list(sample.glob("*_personal_report*"))


def test_a_reference_library_the_list_cannot_be_stored_in_keeps_the_report(
    sample, tmp_path, monkeypatch
):
    # A file, not a folder: the list cannot be downloaded into it.
    monkeypatch.setitem(settings, "reference_library", str(_touch(tmp_path / "lib")))
    report = pr.build_report(sample)
    clinvar = report["sections"]["clinvar"]
    assert [w["code"] for w in clinvar["warnings"]] == ["gene_list_unavailable"]
    assert clinvar["findings"] == [] and report["sections"]["lineage"]["findings"]
