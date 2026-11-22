"""Exon callability (#1177): `vcf callability` and the report's not-reported /
not-readable / not-run results, on real samtools and bcftools output."""

import csv
import gzip
import io
import json
import random
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, sam_to_bam, write_fasta
from wgsextract_cli import api
from wgsextract_cli.core import exon_callability as ec
from wgsextract_cli.core import gene_lists
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.main import main

LENGTH = 6000
SNP = 1100
# GENEE: one exon, read throughout. GENEA: four exons, the third without a
# read although over 90% of the gene body has reads. GENEB: a low-depth
# exon, one in a paralog region, one half read and one the caller never
# assessed (it called chr7:1-5500 only). GENED lies wholly outside that
# region; GENEC has no exon model.
EXONS = {
    "GENEE": [(401, 600, 451, 550)],
    "GENEA": [
        (1001, 1200, 1051, 1200),
        (1801, 1950, 1801, 1950),
        (2601, 2700, 2601, 2700),
        (3401, 3800, 3401, 3700),
    ],
    "GENEB": [
        (4201, 4300, 4201, 4300),
        (4601, 4700, 4601, 4700),
        (5101, 5200, 5101, 5200),
        (5801, 5900, 5801, 5900),
    ],
    "GENED": [(5851, 5950, 5851, 5950)],
}
PARALOG = "segmental_duplication:4650-4720"
GENES = ("GENEA", "GENEB", "GENEC", "GENED", "GENEE")


def _model(path: Path, length: int = LENGTH) -> Path:
    lines = [
        "# Test exons.",
        f"#contig\tchr7\t{length}",
        "\t".join(ec.MODEL_COLUMNS),
    ]
    for gene, exons in EXONS.items():
        for number, (start, end, cds_start, cds_end) in enumerate(exons, start=1):
            limits = PARALOG if (gene, number) == ("GENEB", 2) else ""
            lines.append(
                f"{gene}\tNM_{gene}.1\tMANE Select\t+\t{number}\t{len(exons)}\tchr7"
                f"\t{start}\t{end}\t{cds_start}\t{cds_end}\t{limits}"
            )
    path.write_text("\n".join(lines) + "\n")
    return path


def _starts() -> list[int]:
    covered = [*range(301, 601, 3), *range(901, 2451, 3), *range(2751, 3801, 3)]
    low = list(range(4101, 4301, 34))  # about 3x
    return sorted([*covered, *low, *range(4501, 4701, 3), *range(5001, 5052, 2)])


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A sample folder: BAM, bcftools SNP calls with their gVCF (chr7:1-5500),
    ClinVar calls (a pathogenic SNP in GENEA), the exon model and gene list."""
    folder = tmp_path_factory.mktemp("callability sample")
    rng = random.Random(1177)
    seq = "".join(rng.choice("ACGT") for _ in range(LENGTH))
    alt = "C" if seq[SNP - 1] != "C" else "G"
    ref = write_fasta(folder / "ref.fa", {"chr7": seq})
    lines = [f"@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr7\tLN:{LENGTH}\n@RG\tID:r\tSM:S"]
    for n, start in enumerate(_starts()):
        bases = seq[start - 1 : start + 99]
        if start <= SNP < start + 100:
            bases = bases[: SNP - start] + alt + bases[SNP - start + 1 :]
        lines.append(
            f"r{n}\t0\tchr7\t{start}\t60\t{len(bases)}M\t*\t0\t0\t{bases}"
            f"\t{'I' * len(bases)}\tRG:Z:r"
        )
    bam = Path(sam_to_bam(lines, folder / "S.bam"))
    api.vcf.snp(
        api.VcfSnpParams(
            input=bam, ref=ref, outdir=folder, threads=1, region="chr7:1-5500",
            ploidy="GRCh38",
            reference_evidence="gvcf",
        )
    )  # fmt: skip
    clinvar = folder / "clinvar.vcf"
    clinvar.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=7,length=6000>\n"
        '##INFO=<ID=CLNSIG,Number=.,Type=String,Description="CLNSIG">\n'
        '##INFO=<ID=GENEINFO,Number=1,Type=String,Description="GENEINFO">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        f"7\t500\t2\t{seq[499]}\t{'C' if seq[499] != 'C' else 'G'}\t.\t.\t"
        "CLNSIG=Pathogenic;GENEINFO=GENEE:5\n"
        f"7\t{SNP}\t1\t{seq[SNP - 1]}\t{alt}\t.\t.\tCLNSIG=Pathogenic;GENEINFO=GENEA:1\n"
    )
    api.vcf.clinvar(
        api.VcfClinvarParams(
            vcf_input=folder / "snps.vcf.gz", clinvar_file=Path(bgzip_index(clinvar)),
            ref=ref, outdir=folder, threads=1,
        )
    )  # fmt: skip
    (folder / "genes.tsv").write_text("gene\n" + "\n".join(GENES) + "\n")
    _model(folder / "exons.tsv")
    return folder


def _callability(folder: Path, **params: object) -> api.Result:
    return api.vcf.callability(
        api.VcfCallabilityParams(
            vcf_input=folder / "snps.vcf.gz",
            gene_list=str(folder / "genes.tsv"),
            exons=folder / "exons.tsv",
            outdir=folder,
            **params,  # type: ignore[arg-type]
        ),
        check=False,
    )


def _exons(folder: Path) -> dict[tuple[str, int], dict[str, str]]:
    with open(folder / "exon_callability.tsv", encoding="utf-8") as stream:
        rows = csv.DictReader(
            (line for line in stream if not line.startswith("#")), delimiter="\t"
        )
        return {(r["gene"], int(r["exon"])): r for r in rows}


def _error(result: api.Result) -> str:
    return result.error.message if result.error else ""


def _body_read_fraction(bam: Path, region: str) -> float:
    depths = run_command(
        ["samtools", "depth", "-a", "-r", region, str(bam)], capture_output=True
    ).stdout.splitlines()
    return sum(int(line.split("\t")[2]) > 0 for line in depths) / len(depths)


@needs_htslib
def test_1177_an_unread_exon_keeps_a_gene_with_reads_over_90_percent_of_its_body_from_passing(
    sample: Path,
) -> None:
    """#1177: a gene-body >=90% rule passes GENEA although its third exon has
    no read; exon by exon it is partial, with that exon not readable."""
    assert _body_read_fraction(sample / "S.bam", "chr7:1001-3800") >= 0.9
    result = _callability(sample)
    assert result.exit_code == 0
    genes = {g["gene"]: g for g in json.loads((sample / "exon_callability.json").read_text())["genes"]}  # fmt: skip
    genea = genes["GENEA"]
    assert genea["state"] == "partial"
    assert genea["not_callable"] == {"not_readable": [3]}
    assert (genea["coding_exons"], genea["exons"]["callable"]) == (4, 3)
    exon = _exons(sample)[("GENEA", 3)]
    assert (exon["status"], exon["reasons"]) == ("not_readable", "uncovered")
    # contract: CDS +/- 2 bp is the assessed region and denominator.
    assert int(exon["bases"]) == int(exon["uncovered_bases"]) == 104
    assert genea["bases"] == sum(e[3] - e[2] + 1 + 4 for e in EXONS["GENEA"])
    assert genea["readable_bases"] == genea["bases"] - 104
    assert any(w.code == "exons_not_callable" for w in result.warnings)


@needs_htslib
def test_1177_each_evidence_state_is_a_distinct_exon_status_with_its_denominators(
    sample: Path,
) -> None:
    _callability(sample)
    exons = _exons(sample)
    expected = {
        ("GENEE", 1): ("callable", ""),
        ("GENEA", 1): ("callable", ""),
        ("GENEB", 1): ("not_readable", "low_quality_reference"),
        ("GENEB", 2): ("specialist_required", "segmental_duplication"),
        ("GENEB", 3): ("partial", "low_quality_reference|uncovered"),
        ("GENEB", 4): ("not_run", "not_assessed"),
    }
    assert {k: (exons[k]["status"], exons[k]["reasons"]) for k in expected} == expected
    snp_exon = exons[("GENEA", 1)]
    assert int(snp_exon["variant_bases"]) == 1  # a call reads its base
    assert int(snp_exon["readable_bases"]) == int(snp_exon["bases"])
    paralog = exons[("GENEB", 2)]
    # The gVCF calls the paralog bases callable; they still aren't read.
    assert int(paralog["callable_reference_bases"]) == int(paralog["bases"])
    assert int(paralog["limited_bases"]) == 4702 - 4650 + 1
    assert int(paralog["readable_bases"]) == 4649 - 4599 + 1
    partial = exons[("GENEB", 3)]
    assert 0 < int(partial["readable_bases"]) < int(partial["bases"])
    document = json.loads((sample / "exon_callability.json").read_text())
    states = {g["gene"]: (g["state"], g["reasons"]) for g in document["genes"]}
    assert states["GENEB"][0] == "partial"
    assert states["GENEC"] == ("not_run", ["no_exon_model"])
    assert states["GENED"] == ("not_run", ["not_assessed"])
    assert states["GENEE"] == ("callable", [])


@needs_htslib
def test_1177_records_model_reference_evidence_and_thresholds(sample: Path) -> None:
    result = _callability(sample, flank=5, confidence_depth=(15.0, 20.0))
    document = json.loads((sample / "exon_callability.json").read_text())
    assert document["exon_model"]["name"] == "exons.tsv"
    assert len(document["exon_model"]["sha256"]) == 64
    assert document["reference"]["contigs_checked"] == ["chr7"]
    assert document["reference"]["fasta"] == "ref.fa"
    assert document["evidence"]["engine"] == "bcftools"
    assert document["evidence"]["quality_model"] == "depth_only"
    assert document["evidence"]["region"] == "chr7:1-5500"
    assert (document["thresholds"]["min_depth"], document["thresholds"]["flank"]) == (
        15.0,
        5,
    )
    assert set(document["unassessed_variant_classes"]) >= {
        "copy_number_and_structural",
        "paralog_regions",
    }
    assert int(_exons(sample)[("GENEA", 3)]["bases"]) == 100 + 10
    roles = {o.role for o in result.outputs}
    assert {"exon_callability", "exon_callability_table"} <= roles
    assert any(i.role == "reference_evidence" for i in result.inputs)


@needs_htslib
def test_1177_refuses_an_exon_model_of_another_build(
    sample: Path, tmp_path: Path
) -> None:
    other = _model(tmp_path / "other.tsv", length=LENGTH + 1)
    result = api.vcf.callability(
        api.VcfCallabilityParams(
            vcf_input=sample / "snps.vcf.gz", gene_list=str(sample / "genes.tsv"),
            exons=other, outdir=tmp_path,
        ),
        check=False,
    )  # fmt: skip
    assert result.exit_code != 0
    assert "another build" in _error(result)
    bundled = api.vcf.callability(
        api.VcfCallabilityParams(vcf_input=sample / "snps.vcf.gz", outdir=tmp_path),
        check=False,
    )
    assert bundled.exit_code != 0
    assert "neither the GRCh38 nor the GRCh37" in _error(bundled)
    assert not (tmp_path / "exon_callability.json").exists()


@needs_htslib
def test_1177_a_vcf_without_reference_evidence_says_how_to_get_it(
    tmp_path: Path,
) -> None:
    vcf = tmp_path / "calls.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr7,length=6000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        "chr7\t10\t.\tA\tC\t50\t.\t.\tGT\t0/1\n"
    )
    result = api.vcf.callability(
        api.VcfCallabilityParams(vcf_input=Path(bgzip_index(vcf)), outdir=tmp_path),
        check=False,
    )
    assert result.exit_code != 0
    assert "--reference-evidence gvcf" in _error(result)


_GQ_GVCF = """##fileformat=VCFv4.2
##contig=<ID=chr7,length=6000>
##INFO=<ID=END,Number=1,Type=Integer,Description="End">
##FORMAT=<ID=GT,Number=1,Type=String,Description="GT">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="GQ">
##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum DP">
##GVCFBlock0-20=minGQ=0(inclusive),maxGQ=20(exclusive)
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS
chr7\t1\t.\tA\t<NON_REF>\t.\t.\tEND=1000\tGT:GQ:MIN_DP\t0/0:50:30
chr7\t1001\t.\tA\t<NON_REF>\t.\t.\tEND=6000\tGT:GQ:MIN_DP\t0/0:5:30
"""


@needs_htslib
def test_1177_low_genotype_quality_blocks_are_not_read(tmp_path: Path) -> None:
    """A GQ gVCF (GATK-style blocks): depth 30 everywhere, but GQ 5 from 1001."""
    gvcf = tmp_path / "sample.g.vcf"
    gvcf.write_text(_GQ_GVCF)
    result = api.vcf.callability(
        api.VcfCallabilityParams(
            vcf_input=Path(bgzip_index(gvcf)), exons=_model(tmp_path / "exons.tsv"),
            gene_list=str(_genes(tmp_path)), outdir=tmp_path,
        )
    )  # fmt: skip
    assert result.exit_code == 0
    exons = _exons(tmp_path)
    assert exons[("GENEE", 1)]["status"] == "callable"
    assert (exons[("GENEA", 1)]["status"], exons[("GENEA", 1)]["reasons"]) == (
        "not_readable",
        "low_quality_reference",
    )


def _genes(folder: Path) -> Path:
    path = folder / "genes.tsv"
    path.write_text("gene\n" + "\n".join(GENES) + "\n")
    return path


def _report(folder: Path, outdir: Path, *select: str) -> tuple[int, str, dict]:
    out = io.StringIO()
    code = 0
    argv = ["wgsextract", "report", *(select or ("--workspace", str(folder))),
            "--outdir", str(outdir), "--gene-list", str(folder / "genes.tsv")]  # fmt: skip
    with redirect_stdout(out), patch.object(sys, "argv", argv):
        try:
            main()
        except SystemExit as error:
            code = int(error.code or 0)
    report = json.loads((outdir / "S_personal_report.json").read_text())
    return code, out.getvalue(), report


@needs_htslib
def test_1177_report_separates_not_reported_not_readable_and_not_run(
    sample: Path, tmp_path: Path
) -> None:
    _callability(sample)
    code, _, report = _report(sample, tmp_path)
    assert code == 0
    section = report["sections"]["exon_callability"]
    assert section["status"] == "ok"
    results = {g["gene"]: (g["state"], g["result"]) for g in section["genes"]}
    assert results == {
        "GENEA": ("partial", "reported"),  # the pathogenic SNP
        "GENEE": ("callable", "not_reported"),
        "GENEB": ("partial", "not_readable"),
        "GENEC": ("not_run", "not_run"),
        "GENED": ("not_run", "not_run"),
    }
    genea = next(g for g in section["genes"] if g["gene"] == "GENEA")
    assert genea["exons_not_callable"] == "exon 3 not readable"
    assert section["counts"]["result_not_reported"] == 1
    assert section["all_exons"] == "exon_callability.tsv"
    assert section["evidence"] == "bcftools.g.vcf.gz"
    assert section["sources"][0]["command"] == "vcf callability"
    assert "does not rule out disease" in section["note"]
    markdown = (tmp_path / "S_personal_report.md").read_text()
    html = (tmp_path / "S_personal_report.html").read_text()
    for text in ("not reported", "not readable", "not run"):
        assert f"**{text}**" in markdown and f"<b>{text}</b>" in html


def _copy_with_callability(sample: Path, tmp_path: Path) -> Path:
    _callability(sample)
    folder = tmp_path / "S"
    shutil.copytree(sample, folder)
    return folder


def _results(folder: Path, tmp_path: Path) -> tuple[dict[str, str], set[str]]:
    _, _, report = _report(folder, tmp_path / "report")
    section = report["sections"]["exon_callability"]
    return (
        {g["gene"]: g["result"] for g in section["genes"]},
        {w["code"] for w in section["warnings"]},
    )


@needs_htslib
def test_1177_unplaced_clinvar_calls_leave_no_gene_not_reported(
    sample: Path, tmp_path: Path
) -> None:
    """A P/LP call ClinVar names no gene for can't be placed: no gene may
    then read as not reported, although every exon of GENEE was read."""
    folder = _copy_with_callability(sample, tmp_path)
    clinvar = folder / "clinvar.vcf"
    text = gzip.decompress((folder / "clinvar.vcf.gz").read_bytes()).decode()
    clinvar.write_text(text.replace(";GENEINFO=GENEA:1", ""))
    api.vcf.clinvar(
        api.VcfClinvarParams(
            vcf_input=folder / "snps.vcf.gz", ref=folder / "ref.fa", threads=1,
            clinvar_file=Path(bgzip_index(clinvar, folder / "unnamed.vcf.gz")),
            outdir=folder,
        )
    )  # fmt: skip
    results, warnings = _results(folder, tmp_path)
    assert results["GENEE"] == "not_run"
    assert "not_reported" not in results.values()
    assert "clinvar_not_combined" in warnings


@needs_htslib
def test_1177_clinvar_on_another_call_set_is_not_combined(
    sample: Path, tmp_path: Path
) -> None:
    """ClinVar annotated bcftools' indel calls, which have no gVCF: the
    SNP gVCF's callable exons say nothing about them."""
    folder = _copy_with_callability(sample, tmp_path)
    api.vcf.indel(
        api.VcfIndelParams(
            input=folder / "S.bam", ref=folder / "ref.fa", outdir=folder,
            threads=1, region="chr7:1-5500", ploidy="GRCh38",
        )
    )  # fmt: skip
    api.vcf.clinvar(
        api.VcfClinvarParams(
            vcf_input=folder / "indels.vcf.gz", ref=folder / "ref.fa", threads=1,
            clinvar_file=folder / "clinvar.vcf.gz", outdir=folder,
        )
    )  # fmt: skip
    results, warnings = _results(folder, tmp_path)
    assert results["GENEE"] == "not_run"
    assert "clinvar_not_combined" in warnings


@needs_htslib
def test_1177_report_without_a_callability_run_says_how_to_run_it(
    sample: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "S"
    folder.mkdir()
    for name in ("S.bam", "S.bam.bai", "genes.tsv"):
        (folder / name).write_bytes((sample / name).read_bytes())
    _, _, report = _report(
        folder, tmp_path / "report", "--input", str(folder / "S.bam")
    )
    section = report["sections"]["exon_callability"]
    assert section["status"] == "not_run"
    commands = [m["command"] for m in section["missing"]]
    assert commands[0].endswith("--reference-evidence gvcf")
    assert commands[-1].startswith("wgsextract vcf callability")


def test_1177_bundled_models_cover_every_built_in_gene_and_flag_pms2_paralog_exons() -> (
    None
):
    """The bundled hg19/hg38 models give each built-in list gene its select
    transcript's coding exons; PMS2's 3' exons, shared with PMS2CL, are
    flagged as short-read limitations in both builds."""
    symbols = {
        s for i in gene_lists.builtin_ids() for s in gene_lists.builtin_symbols(i)
    }
    for build in ec.BUILDS:
        model = ec.bundled_model(build)
        assert {s.upper() for s in symbols} <= set(model.genes)
        limited = {e.number for e in model.genes["PMS2"] if e.limitations}
        assert {11, 12, 13, 14, 15} <= limited


@needs_htslib
def test_1177_a_grch37_gvcf_is_assessed_with_the_hg19_model(tmp_path: Path) -> None:
    """The bundled model is chosen by the gVCF's contig lengths; GRCh37's "1"
    naming matches the model's chr1."""
    model = ec.bundled_model("hg19")
    contigs = "".join(
        f"##contig=<ID={c.removeprefix('chr')},length={n}>\n"
        for c, n in model.contigs.items()
    )
    gvcf = tmp_path / "b37.g.vcf"
    gvcf.write_text(
        _GQ_GVCF.replace("##contig=<ID=chr7,length=6000>\n", contigs).split(
            "chr7\t1\t"
        )[0]
        + "1\t1\t.\tN\t<NON_REF>\t.\t.\tEND=1000\tGT:GQ:MIN_DP\t0/0:50:30\n"
    )
    result = api.vcf.callability(
        api.VcfCallabilityParams(vcf_input=Path(bgzip_index(gvcf)), outdir=tmp_path)
    )
    document = json.loads((tmp_path / "exon_callability.json").read_text())
    assert document["build"] == "hg19"
    assert document["exon_model"]["transcripts"] == "RefSeq Select"
    assert "1" in document["reference"]["contigs_checked"]
    # No record covers an exon: every coding exon was assessed and unread.
    assert result.details["counts"]["not_readable"] == 84
