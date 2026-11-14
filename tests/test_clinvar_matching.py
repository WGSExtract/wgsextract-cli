"""Regressions for equivalent indels, phased MNVs and assertion evidence."""

import csv
import gzip
import json
from pathlib import Path
from urllib.parse import unquote

import pytest

from tests.htslib_helpers import needs_htslib, write_fasta
from wgsextract_cli.api.vcf import VcfCaddParams, VcfClinvarParams, cadd, clinvar
from wgsextract_cli.core import personal_report, variant_table
from wgsextract_cli.core.utils import WGSExtractError

pytestmark = needs_htslib
SEQ = "ACAAAAAAGTCCGTACGTACGTACGTACGTACGT"


def vcf(
    path: Path,
    rows: list[str],
    *,
    resource: bool = False,
    chrom: str = "1",
    info_headers: tuple[str, ...] = (),
) -> Path:
    tags = (
        ("CLNSIG", "CLNDN", "GENEINFO", "CLNREVSTAT", "CLNSIGCONF") if resource else ()
    )
    header = (
        f"##fileformat=VCFv4.2\n##contig=<ID={chrom},length={len(SEQ)}>\n"
        + "".join(h + "\n" for h in info_headers)
        + "".join(
            f'##INFO=<ID={tag},Number=.,Type=String,Description="{tag}">\n'
            for tag in tags
        )
        + "".join(
            f'##FORMAT=<ID={tag},Number={number},Type={kind},Description="{tag}">\n'
            for tag, number, kind in (
                ("GT", "1", "String"),
                ("PS", "1", "Integer"),
                ("DP", "1", "Integer"),
                ("GQ", "1", "Integer"),
                ("AD", "R", "Integer"),
            )
        )
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
    )
    path.write_text(
        header + "".join(chrom + "\t" + "\t".join(r.split()) + "\n" for r in rows)
    )
    return path


def annotate(folder: Path, sample: Path, resource: Path, ref: Path) -> Path:
    result = clinvar(
        VcfClinvarParams(
            vcf_input=sample, clinvar_file=resource, ref=ref, outdir=folder, threads=1
        ),
        check=False,
    )
    if result.status != "ok":
        raise WGSExtractError(str(result.error))
    return folder / "clinvar_pathogenic.vcf.gz"


def records(path: Path) -> list[list[str]]:
    with gzip.open(path, "rt") as stream:
        return [
            line.rstrip().split("\t") for line in stream if not line.startswith("#")
        ]


def table(path: Path) -> list[dict[str, str]]:
    with path.open() as stream:
        return list(
            csv.DictReader((s for s in stream if not s.startswith("#")), delimiter="\t")
        )


def report(folder: Path) -> dict:
    genes = folder / "genes.tsv"
    genes.write_text("gene\tinheritance\tdisease\nMUTYH\tAR\tMAP\n")
    return personal_report.build_report(folder, gene_list=str(genes))


@pytest.mark.parametrize(
    ("pos", "ref_allele", "alt"), [(3, "AA", "A"), (6, "AA", "A"), (3, "AAA", "AA")]
)
def test_836_equivalent_indels_recover_identical_report_findings(
    tmp_path, pos, ref_allele, alt
):
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [f"{pos} sample {ref_allele} {alt} 99 PASS . GT:DP:GQ:AD 0/1:30:99:15,15"],
    )
    resource = vcf(
        tmp_path / "clinvar.vcf",
        ["5 VCVdel AA A . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1"],
        resource=True,
    )
    originals = (sample.read_bytes(), resource.read_bytes())
    out = tmp_path / "out"
    result = annotate(out, sample, resource, ref)
    (found,) = records(result)
    assert found[:5] == ["1", "2", "sample", "CA", "C"]
    (call,) = table(out / "clinvar_pathogenic.confidence.tsv")
    origin = json.loads(unquote(call["WGSE_ORIGINAL"]))
    assert (origin["pos"], origin["ref"], origin["alt"], origin["samples"]) == (
        pos,
        ref_allele,
        alt,
        ["0/1:30:99:15,15"],
    )
    (finding,) = report(out)["sections"]["clinvar"]["findings"]
    assert (finding["gene"], finding["variant"]["ref"], finding["variant"]["alt"]) == (
        "MUTYH",
        "CA",
        "C",
    )
    assert (sample.read_bytes(), resource.read_bytes()) == originals


@pytest.mark.parametrize(
    ("second_gt", "second_ps", "expected"),
    [
        ("0|1", "11", 1),
        ("1|0", "11", 0),
        ("0|1", "12", 0),
        ("0/1", "11", 0),
        ("0|1", ".", 0),
    ],
)
def test_837_phased_snvs_recover_mnv_only_with_cis_phase(
    tmp_path, second_gt, second_ps, expected
):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            "11 component1 C A 99 PASS . GT:PS:DP:GQ:AD 0|1:11:30:99:15,15",
            f"12 component2 C A 99 PASS . GT:PS:DP:GQ:AD {second_gt}:{second_ps}:30:99:15,15",
        ],
    )
    resource = vcf(
        tmp_path / "clinvar.vcf",
        [
            "11 VCVmnv CC AA . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1"
        ],
        resource=True,
    )
    out = tmp_path / "out"
    result = annotate(out, sample, resource, ref)
    found = records(result)
    assert len(found) == expected
    assert len(records(out / "clinvar_annotated.vcf.gz")) == 2 + expected
    if expected:
        assert found[0][3:5] == ["CC", "AA"]
        (call,) = table(out / "clinvar_pathogenic.confidence.tsv")
        components = json.loads(unquote(call["WGSE_MNV_COMPONENTS"]))
        assert [(c["pos"], c["sample"]) for c in components] == [
            (11, "0|1:11:30:99:15,15"),
            (12, "0|1:11:30:99:15,15"),
        ]
        (gene,) = report(out)["sections"]["clinvar"]["genes"]
        assert (gene["heterozygous"], gene["status"]) == (1, "carrier")


def test_841_review_stars_and_conflicts_survive_table_and_report(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            f"{p} . {r} {a} 99 PASS . GT:DP:GQ:AD 0/1:30:99:15,15"
            for p, r, a in [(11, "C", "A"), (14, "T", "C"), (15, "A", "G")]
        ],
    )
    resource = vcf(
        tmp_path / "clinvar.vcf",
        [
            "11 VCVunsupported C A . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595;CLNREVSTAT=no_assertion_criteria_provided GT 0/1",
            "14 VCVexpert T C . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595;CLNREVSTAT=reviewed_by_expert_panel GT 0/1",
            "15 VCVconflict A G . PASS CLNSIG=Conflicting_classifications_of_pathogenicity;CLNDN=MAP;GENEINFO=MUTYH:4595;CLNREVSTAT=criteria_provided,_conflicting_classifications;CLNSIGCONF=Likely_pathogenic_(2)|Uncertain_significance_(1) GT 0/1",
        ],
        resource=True,
    )
    out = tmp_path / "out"
    annotate(out, sample, resource, ref)
    calls = table(out / "clinvar_pathogenic.confidence.tsv")
    assert [
        (c["WGSE_CLINVAR_ID"], c["WGSE_CLINVAR_STARS"], c["confidence"]) for c in calls
    ] == [("VCVunsupported", "0", "high"), ("VCVexpert", "3", "high")]
    (conflict,) = table(out / "clinvar_conflicts.confidence.tsv")
    assert conflict["CLNSIGCONF"] == "Likely_pathogenic_(2)|Uncertain_significance_(1)"
    with gzip.open(out / "clinvar_annotated.vcf.gz", "rt") as stream:
        lines = list(stream)
    header = variant_table.parse_header(s for s in lines if s.startswith("#"))
    rows = [
        dict(zip(variant_table.COLUMN_NAMES, row, strict=True))
        for row in variant_table.variant_rows(
            (s for s in lines if not s.startswith("#")), header
        )
    ]
    assert [r["clinvar_review_stars"] for r in rows] == [0, 3, 1]
    document = report(out)
    section = document["sections"]["clinvar"]
    assert section["counts"]["carried"] == 2
    assert section["counts"]["conflicts_for_review"] == 1
    assert [
        (
            f["variant"]["clinical_evidence"]["id"],
            f["variant"]["clinical_evidence"]["stars"],
        )
        for f in section["findings"]
    ] == [("VCVunsupported", 0), ("VCVexpert", 3), ("VCVconflict", 1)]
    (gene,) = section["genes"]
    assert gene["heterozygous"] == 2
    for rendered in (
        personal_report.render_html(document),
        personal_report.render_markdown(document),
    ):
        assert "expert panel" in rendered and "0 stars" in rendered
        assert "not consensus P/LP" in rendered


@pytest.mark.parametrize("bad", ["ref", "build"])
def test_836_incompatible_reference_fails_without_a_partial_annotation(tmp_path, bad):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(tmp_path / "sample.vcf", ["11 . C A 99 PASS . GT 0/1"])
    resource = vcf(
        tmp_path / "clinvar.vcf",
        ["11 . G A . PASS CLNSIG=Pathogenic;CLNDN=MAP GT 0/1"],
        resource=True,
    )
    if bad == "build":
        resource.write_text(
            resource.read_text().replace(f"length={len(SEQ)}", "length=249250621")
        )
    with pytest.raises(WGSExtractError, match="mismatch|annotation failed"):
        annotate(tmp_path / "out", sample, resource, ref)
    assert not (tmp_path / "out" / "clinvar_annotated.vcf.gz").exists()


def test_836_normalization_preserves_multiallelic_genotype_provenance(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        ["11 original CC C,AA 99 PASS . GT:DP:GQ:AD 1/2:30:99:0,15,15"],
    )
    resource = vcf(
        tmp_path / "clinvar.vcf",
        [
            "11 VCVdel CC C . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
            "11 VCVmnv CC AA . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
        ],
        resource=True,
    )
    out = tmp_path / "out"
    annotate(out, sample, resource, ref)
    calls = table(out / "clinvar_pathogenic.confidence.tsv")
    assert {(c["ref"], c["alt"], c["genotype"]) for c in calls} == {
        ("TC", "T", "1/0"),
        ("CC", "AA", "0/1"),
    }
    for call in calls:
        original = json.loads(unquote(call["WGSE_ORIGINAL"]))
        assert (original["ref"], original["alt"], original["samples"]) == (
            "CC",
            "C,AA",
            ["1/2:30:99:0,15,15"],
        )
    (gene,) = report(out)["sections"]["clinvar"]["genes"]
    assert (gene["heterozygous"], gene["status"]) == (2, "biallelic")


def test_837_report_does_not_count_pathogenic_components_and_mnv_twice(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            "11 component1 C A 99 PASS . GT:PS:DP:GQ:AD 0|1:11:30:99:15,15",
            "12 component2 C A 99 PASS . GT:PS:DP:GQ:AD 0|1:11:30:99:15,15",
        ],
    )
    resource = vcf(
        tmp_path / "clinvar.vcf",
        [
            "11 VCVcomponent1 C A . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
            "11 VCVmnv CC AA . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
            "12 VCVcomponent2 C A . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
        ],
        resource=True,
    )
    out = tmp_path / "out"
    result = annotate(out, sample, resource, ref)
    assert len(records(result)) == 3
    section = report(out)["sections"]["clinvar"]
    assert section["counts"]["carried"] == 1
    (finding,) = section["findings"]
    assert finding["variant"]["clinical_evidence"]["id"] == "VCVmnv"
    (gene,) = section["genes"]
    assert (gene["heterozygous"], gene["status"]) == (1, "carrier")


@pytest.mark.parametrize(
    ("info", "header", "expected"),
    [
        (
            "MQ=10",
            '##INFO=<ID=MQ,Number=1,Type=Float,Description="RMS mapping quality">',
            "low",
        ),
        ("IMP", '##INFO=<ID=IMP,Number=0,Type=Flag,Description="Imputed">', None),
    ],
)
def test_837_composition_never_promotes_weak_or_imputed_component_calls(
    tmp_path, info, header, expected
):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            f"11 . C A 99 PASS {info} GT:PS:DP:GQ:AD 0|1:11:30:99:15,15",
            "12 . C A 99 PASS . GT:PS:DP:GQ:AD 0|1:11:30:99:15,15",
        ],
        info_headers=(header,),
    )
    resource = vcf(
        tmp_path / "clinvar.vcf",
        [
            "11 VCVmnv CC AA . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
        ],
        resource=True,
    )
    out = tmp_path / "out"
    annotate(out, sample, resource, ref)
    calls = table(out / "clinvar_pathogenic.confidence.tsv")
    assert [c["confidence"] for c in calls] == ([] if expected is None else [expected])
    if calls:
        assert "low_mapq" in calls[0]["reasons"]


def test_836_unavailable_resource_alt_contigs_do_not_block_primary_findings(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(tmp_path / "sample.vcf", ["11 . C A 99 PASS . GT 0/1"])
    resource = vcf(
        tmp_path / "clinvar.vcf",
        [
            "11 VCVprimary C A . PASS CLNSIG=Pathogenic;CLNDN=MAP GT 0/1",
        ],
        resource=True,
    )
    resource.write_text(
        resource.read_text().replace(
            "#CHROM", "##contig=<ID=NT_113889.1,length=100>\n#CHROM"
        )
        + "NT_113889.1\t10\tVCValt\tA\tG\t.\tPASS\tCLNSIG=Pathogenic;CLNDN=MAP\tGT\t0/1\n"
    )
    out = tmp_path / "out"
    result = annotate(out, sample, resource, ref)
    assert len(records(result)) == 1
    document = json.loads(next(out.glob("*.manifest.json")).read_text())
    (warning,) = [
        w
        for w in document["warnings"]
        if w["code"] == "annotation_contigs_not_in_reference"
    ]
    assert warning["details"]["count"] == 1


@pytest.mark.parametrize(("extra_gt", "expected"), [("0|1", 0), ("1|0", 1), ("0/1", 0)])
def test_837_intervening_variant_must_not_invent_a_different_pathogenic_mnv(
    tmp_path, extra_gt, expected
):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            "11 . C A 99 PASS . GT:PS 0|1:11",
            f"12 . C T 99 PASS . GT:PS {extra_gt}:11",
            "13 . G A 99 PASS . GT:PS 0|1:11",
        ],
    )
    resource = vcf(
        tmp_path / "clinvar.vcf",
        [
            "11 VCVgap CCG ACA . PASS CLNSIG=Pathogenic;CLNDN=MAP GT 0/1",
        ],
        resource=True,
    )
    assert len(records(annotate(tmp_path / "out", sample, resource, ref))) == expected


def test_837_overlapping_deletion_prevents_a_false_mnv_on_that_haplotype(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            "10 . TC T 99 PASS . GT:PS 0|1:11",
            "11 . C A 99 PASS . GT:PS 0|1:11",
            "12 . C A 99 PASS . GT:PS 0|1:11",
        ],
    )
    resource = vcf(
        tmp_path / "clinvar.vcf",
        [
            "11 VCVmnv CC AA . PASS CLNSIG=Pathogenic;CLNDN=MAP GT 0/1",
        ],
        resource=True,
    )
    assert records(annotate(tmp_path / "out", sample, resource, ref)) == []


def test_837_mnv_deduplication_preserves_the_pathogenic_copy_on_the_other_haplotype(
    tmp_path,
):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            "11 . C A 99 PASS . GT:PS 1|1:11",
            "12 . C A 99 PASS . GT:PS 0|1:11",
        ],
    )
    resource = vcf(
        tmp_path / "clinvar.vcf",
        [
            "11 VCVcomponent C A . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
            "11 VCVmnv CC AA . PASS CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1",
        ],
        resource=True,
    )
    out = tmp_path / "out"
    annotate(out, sample, resource, ref)
    section = report(out)["sections"]["clinvar"]
    (gene,) = section["genes"]
    assert (gene["heterozygous"], gene["homozygous"], gene["status"]) == (
        2,
        0,
        "biallelic",
    )
    (residual,) = [
        f for f in section["findings"] if f["variant"]["mnv_residual_copies"]
    ]
    assert residual["variant"]["genotype"] == "1|1"
    assert residual["variant"]["mnv_residual_copies"] == 1


def test_836_shared_normalization_preserves_cadd_snv_and_indel_scores(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"1": SEQ})
    sample = vcf(
        tmp_path / "sample.vcf",
        [
            "6 . AA A 99 PASS . GT 0/1",
            "11 . C A 99 PASS . GT 0/1",
        ],
    )
    snvs, indels = tmp_path / "snv.tsv", tmp_path / "indel.tsv"
    header = "#Chrom\tPos\tRef\tAlt\tRawScore\tPHRED\n"
    snvs.write_text(header + "1\t11\tC\tA\t0.8\t30\n")
    indels.write_text(header + "1\t5\tAA\tA\t0.9\t40\n")
    out = tmp_path / "out"
    result = cadd(
        VcfCaddParams(
            vcf_input=sample,
            ref=ref,
            cadd_file=snvs,
            cadd_indel_file=indels,
            outdir=out,
            threads=1,
        )
    )
    assert result.status == "ok"
    found = records(out / "cadd_annotated.vcf.gz")
    assert [(r[1], r[3], r[4]) for r in found] == [("2", "CA", "C"), ("11", "C", "A")]
    assert all(
        f"CADD_PHRED={score}" in row[7]
        for row, score in zip(found, [40, 30], strict=True)
    )
