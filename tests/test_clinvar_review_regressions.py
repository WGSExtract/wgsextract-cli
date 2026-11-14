"""Observable regressions from the ClinVar Copilot review round."""

import json

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, write_fasta
from tests.test_clinvar_matching import SEQ, annotate, records, report, vcf
from wgsextract_cli.api.vcf import (
    VcfCaddParams,
    VcfChainAnnotateParams,
    cadd,
    chain_annotate,
)
from wgsextract_cli.core import variant_table
from wgsextract_cli.core.utils import WGSExtractError

pytestmark = needs_htslib
MNV = "11 VCVmnv CC AA . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1"
PARTS = ["11 . C A 99 PASS . GT:PS 0|1:11", "12 . C A 99 PASS . GT:PS 0|1:11"]


@pytest.mark.parametrize(
    "case",
    ["minimal_formats", "missing_end", "uncarried_mnv", "multiallelic", "uppercase"],
)
def test_837_review_phased_mnv_findings_survive_valid_input_representations(
    tmp_path, case
):
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": SEQ})
    rows = list(PARTS)
    extra = ()
    if case == "missing_end":
        rows.append("14 . T C 99 PASS END=. GT:PS 0|1:11")
        extra = ('##INFO=<ID=END,Number=1,Type=Integer,Description="End">',)
    if case == "uncarried_mnv":
        rows.insert(1, "11 . CC AA 99 PASS . GT:PS 0|0:11")
    if case == "multiallelic":
        rows = ["11 . C A,G 99 PASS . GT:PS 1|2:11", "12 . C A 99 PASS . GT:PS 1|0:11"]
    sample = vcf(tmp_path / "sample.vcf", rows, info_headers=extra)
    if case == "minimal_formats":
        sample.write_text(
            "".join(
                s
                for s in sample.read_text().splitlines(keepends=True)
                if not s.startswith(("##FORMAT=<ID=DP,", "##FORMAT=<ID=GQ,"))
            )
        )
    resource = vcf(tmp_path / "resource.vcf", [MNV], resource=True)
    if case == "uppercase":
        resource = type(resource)(bgzip_index(resource, tmp_path / "resource.VCF.gz"))
    out = tmp_path / "out"
    (finding,) = records(annotate(out, sample, resource, ref))
    assert finding[3:5] == ["CC", "AA"]
    if case == "multiallelic":
        assert finding[9].split(":")[0] == "1|0"
    if case == "missing_end":
        document = json.loads(next(out.glob("*.manifest.json")).read_text())
        assert document["metrics"]["clinvar_mnv_indexed_records"] == 2
        assert len(records(out / "clinvar_annotated.vcf.gz")) == 4


def test_837_variant_only_gap_does_not_establish_a_reference_base(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(tmp_path / "sample.vcf", [PARTS[0], "13 . G A 99 PASS . GT:PS 0|1:11"])
    resource = vcf(
        tmp_path / "resource.vcf",
        ["11 . CCG ACA . PASS CLNSIG=Pathogenic;CLNDN=MAP GT 0/1"],
        resource=True,
    )
    assert records(annotate(tmp_path / "out", sample, resource, ref)) == []


@pytest.mark.parametrize("geneinfo", ["", ";GENEINFO=BRCA2:675"])
def test_837_mnv_without_replacement_gene_evidence_keeps_component_carrier(
    tmp_path, geneinfo
):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(tmp_path / "sample.vcf", PARTS)
    resource = vcf(
        tmp_path / "resource.vcf",
        [
            "11 component C A . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
            f"11 combined CC AA . PASS CLNSIG=Pathogenic;CLNDN=MAP{geneinfo} GT 0/1",
        ],
        resource=True,
    )
    out = tmp_path / "out"
    annotate(out, sample, resource, ref)
    (gene,) = report(out)["sections"]["clinvar"]["genes"]
    assert (gene["gene"], gene["heterozygous"], gene["status"]) == (
        "MUTYH",
        1,
        "carrier",
    )


def test_837_two_mnvs_proven_on_one_haplotype_report_carrier(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [*PARTS, "15 . A G 99 PASS . GT:PS 0|1:11", "16 . C T 99 PASS . GT:PS 0|1:11"],
    )
    resource = vcf(
        tmp_path / "resource.vcf",
        [
            MNV,
            "15 VCVsecond AC GT . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
        ],
        resource=True,
    )
    out = tmp_path / "out"
    annotate(out, sample, resource, ref)
    (gene,) = report(out)["sections"]["clinvar"]["genes"]
    assert (gene["heterozygous"], gene["status"]) == (2, "carrier")


@pytest.mark.parametrize(
    ("genotype", "phase_set", "status", "phase"),
    [
        ("0|1", "11", "carrier", "cis"),
        ("1|0", "11", "biallelic", "trans"),
        ("0|1", "12", "possible_biallelic", "unknown"),
    ],
)
def test_837_composed_mnv_and_phased_snv_share_report_phase_rules(
    tmp_path, genotype, phase_set, status, phase
):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            *PARTS,
            f"16 . C T 99 PASS . GT:PS {genotype}:{phase_set}",
        ],
    )
    resource = vcf(
        tmp_path / "resource.vcf",
        [
            MNV,
            "16 VCVsnv C T . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
        ],
        resource=True,
    )
    out = tmp_path / "out"
    annotate(out, sample, resource, ref)
    (gene,) = report(out)["sections"]["clinvar"]["genes"]
    assert (gene["status"], gene["phase"]) == (status, phase)


def test_841_table_keeps_review_vectors_separate_for_each_alt(tmp_path):
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            "11 . C A,G 99 PASS CLNSIG=Pathogenic,Conflicting;CLNREVSTAT=no_assertion_criteria_provided,reviewed_by_expert_panel;WGSE_CLINVAR_ID=V1,V2;CLNSIGCONF=.,Likely_pathogenic_(2) GT 1/2"
        ],
        info_headers=tuple(
            f'##INFO=<ID={tag},Number=A,Type=String,Description="{tag}">'
            for tag in ("CLNSIG", "CLNREVSTAT", "WGSE_CLINVAR_ID", "CLNSIGCONF")
        ),
    )
    lines = sample.read_text().splitlines()
    header = variant_table.parse_header(s for s in lines if s.startswith("#"))
    rows = [
        dict(zip(variant_table.COLUMN_NAMES, row, strict=True))
        for row in variant_table.variant_rows(
            (s for s in lines if not s.startswith("#")), header
        )
    ]
    assert [
        (r["clinvar_id"], r["clinvar_review_stars"], r["clinvar_conflict_evidence"])
        for r in rows
    ] == [("V1", 0, None), ("V2", 3, "Likely_pathogenic_(2)")]


def test_841_transferred_review_stars_follow_each_resource_alt(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf", ["11 . C A,G 99 PASS . GT:DP:GQ:AD 1/2:30:99:0,15,15"]
    )
    resource = vcf(
        tmp_path / "resource.vcf",
        [
            "11 VCVsite C A,G . PASS CLNSIG=Pathogenic,Pathogenic;CLNREVSTAT=no_assertion_criteria_provided,reviewed_by_expert_panel;CLNDN=MAP GT 0/1",
        ],
        resource=True,
    )
    for tag in ("CLNSIG", "CLNREVSTAT"):
        resource.write_text(
            resource.read_text().replace(f"ID={tag},Number=.", f"ID={tag},Number=A")
        )
    out = tmp_path / "out"
    annotate(out, sample, resource, ref)
    from tests.test_clinvar_matching import table

    calls = table(out / "clinvar_pathogenic.confidence.tsv")
    assert [(c["alt"], c["WGSE_CLINVAR_STARS"]) for c in calls] == [
        ("A", "0"),
        ("G", "3"),
    ]


def test_841_conflict_confidence_never_changes_pathogenic_counts_or_warnings(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            "11 . C A 99 PASS . GT:DP:GQ:AD 0/1:3:99:1,2",
            "14 . T C 99 PASS . GT:DP:GQ:AD 0/1:3:99:1,2",
        ],
    )
    resource = vcf(
        tmp_path / "resource.vcf",
        [
            "11 . C A . PASS CLNSIG=Pathogenic;CLNDN=MAP GT 0/1",
            "14 . T C . PASS CLNSIG=Conflicting_classifications_of_pathogenicity;CLNSIGCONF=Likely_pathogenic_(2);CLNDN=MAP GT 0/1",
        ],
        resource=True,
    )
    out = tmp_path / "out"
    annotate(out, sample, resource, ref)
    document = json.loads(next(out.glob("*.manifest.json")).read_text())
    assert document["metrics"]["confidence_low"] == 1
    assert document["metrics"]["clinvar_conflict_confidence_low"] == 1
    assert [w["code"] for w in document["warnings"]].count(
        "low_confidence_findings"
    ) == 1
    assert [w["code"] for w in document["warnings"]].count(
        "low_confidence_conflicts"
    ) == 1


@pytest.mark.parametrize("mito_length", [16569, 16570])
def test_836_only_known_hg19_mito_resource_incompatibility_is_excluded(
    tmp_path, mito_length
):
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": SEQ, "chrM": "A" * 16571})
    sample = vcf(tmp_path / "sample.vcf", ["11 . C A 99 PASS . GT 0/1"])
    resource = vcf(
        tmp_path / "resource.vcf",
        ["11 . C A . PASS CLNSIG=Pathogenic;CLNDN=MAP GT 0/1"],
        resource=True,
    )
    resource.write_text(
        resource.read_text().replace(
            "#CHROM", f"##contig=<ID=MT,length={mito_length}>\n#CHROM"
        )
        + "MT\t1\t.\tA\tG\t.\tPASS\tCLNSIG=Pathogenic;CLNDN=MAP\tGT\t0/1\n"
    )
    out = tmp_path / "out"
    if mito_length == 16570:
        with pytest.raises(WGSExtractError, match="mismatch"):
            annotate(out, sample, resource, ref)
    else:
        assert len(records(annotate(out, sample, resource, ref))) == 1
        document = json.loads(next(out.glob("*.manifest.json")).read_text())
        assert [
            w["details"]["count"]
            for w in document["warnings"]
            if w["code"] == "annotation_mito_reference_incompatible"
        ] == [1]


def test_836_indexed_headerless_contigs_still_normalize_and_restore_names(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": SEQ})
    sample = vcf(tmp_path / "sample.vcf", ["6 . AA A 99 PASS . GT 0/1"])
    sample.write_text(
        "".join(
            s
            for s in sample.read_text().splitlines(keepends=True)
            if not s.startswith("##contig")
        )
    )
    sample = type(sample)(bgzip_index(sample))
    resource = vcf(
        tmp_path / "resource.vcf",
        ["5 . AAA AA . PASS CLNSIG=Pathogenic;CLNDN=MAP GT 0/1"],
        resource=True,
    )
    (finding,) = records(annotate(tmp_path / "out", sample, resource, ref))
    assert finding[:2] == ["1", "2"]


def test_836_cadd_snv_indexed_subset_avoids_unrelated_resource_records(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(tmp_path / "sample.vcf", ["11 . C A 99 PASS . GT 0/1"])
    scores = tmp_path / "cadd.tsv"
    scores.write_text(
        "#Chrom\tPos\tRef\tAlt\tRawScore\tPHRED\n"
        + "1\t1\tT\tG\t0.1\t10\n" * 10000
        + "1\t11\tC\tA\t0.9\t40\n"
    )
    out = tmp_path / "out"
    cadd(
        VcfCaddParams(
            vcf_input=sample, ref=ref, cadd_file=scores, outdir=out, threads=1
        )
    )
    (finding,) = records(out / "cadd_annotated.vcf.gz")
    assert "CADD_PHRED=40" in finding[7]


def test_836_normalized_resource_cache_invalidates_when_source_changes(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(tmp_path / "sample.vcf", ["11 . C A 99 PASS . GT 0/1"])
    resource = vcf(
        tmp_path / "resource.vcf",
        ["11 . C A . PASS CLNSIG=Pathogenic;CLNDN=MAP GT 0/1"],
        resource=True,
    )
    out = tmp_path / "out"
    annotate(out, sample, resource, ref)
    annotate(out, sample, resource, ref)
    document = json.loads(next(out.glob("*.manifest.json")).read_text())
    assert document["metrics"]["annotation_normalization_cache_hit"] is True
    resource.write_text(resource.read_text().replace("Pathogenic", "Benign"))
    assert records(annotate(out, sample, resource, ref)) == []


def test_chain_skips_clinvar_without_fasta_and_keeps_other_annotations(tmp_path):
    sample = vcf(tmp_path / "sample.vcf", ["11 . C A 99 PASS . GT 0/1"])
    sample.write_text(
        sample.read_text().replace("#CHROM", "##reference=GRCh38\n#CHROM")
    )
    library = tmp_path / "lib" / "ref"
    library.mkdir(parents=True)
    bgzip_index(
        vcf(
            library / "clinvar_hg38.vcf",
            ["11 . C A . PASS CLNSIG=Pathogenic;CLNDN=MAP GT 0/1"],
            resource=True,
        )
    )
    from tests.test_vcf_annotations import _tsv

    _tsv(
        library / "revel_hg38.tsv.gz",
        "#Chr\tStart\tEnd\tRef\tAlt\tScore",
        ["1 11 11 C A 0.9"],
    )
    result = chain_annotate(
        VcfChainAnnotateParams(
            vcf_input=sample,
            ref=tmp_path / "lib",
            outdir=tmp_path / "out",
            annotations="clinvar,revel",
        )
    )
    assert any(
        w.code == "annotation_skipped" and "--ref" in w.message for w in result.warnings
    )
    (finding,) = records(tmp_path / "out" / "chain_annotated.vcf.gz")
    assert "REVEL=0.9" in finding[7]
