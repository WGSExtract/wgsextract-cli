import json
import logging
import os
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, run_wgsextract, write_fasta
from wgsextract_cli import api
from wgsextract_cli.commands.vcf import pharmcat
from wgsextract_cli.commands.vcf.pharmcat import (
    REFERENCE_FASTA,
    merge_outside_calls,
    pharmcat_directory,
    prepare_input,
    summarize,
)
from wgsextract_cli.core import runtime
from wgsextract_cli.core.reference_resolver import ReferenceLibrary
from wgsextract_cli.core.utils import WGSExtractError, run_command


def test_a_missing_pharmcat_install_says_where_to_get_it(tmp_path):
    lib = ReferenceLibrary(str(tmp_path))
    with pytest.raises(WGSExtractError) as error:
        pharmcat_directory(Namespace(pharmcat_dir=None), lib)
    message = str(error.value)
    assert str(tmp_path / "pharmcat") in message
    assert "github.com/PharmGKB/PharmCAT/releases" in message
    assert "pharmcat_directory" in message and "--pharmcat-dir" in message


def test_an_extracted_release_in_the_library_is_found(tmp_path):
    release = tmp_path / "pharmcat"
    release.mkdir()
    for name in ("pharmcat_pipeline", "pharmcat.jar", "pharmcat_regions.bed"):
        (release / name).write_text("x")
    lib = ReferenceLibrary(str(tmp_path))
    assert pharmcat_directory(Namespace(pharmcat_dir=None), lib) == str(release)


def test_summary_lists_called_genes_and_preserves_unknown(tmp_path):
    phenotype = tmp_path / "s.phenotype.json"
    phenotype.write_text(
        json.dumps(
            {
                "geneReports": {
                    "CYP2C19": {
                        "recommendationDiplotypes": [
                            {
                                "allele1": {"name": "*17"},
                                "allele2": {"name": "*17"},
                                "phenotypes": ["Ultrarapid Metabolizer"],
                            }
                        ]
                    },
                    "CYP2D6": {
                        "recommendationDiplotypes": [
                            {
                                "allele1": {"name": "Unknown"},
                                "allele2": {"name": "Unknown"},
                            }
                        ]
                    },
                }
            }
        )
    )
    called, unknown = summarize(str(phenotype))
    assert (called.gene, called.diplotype, called.phenotype) == (
        "CYP2C19",
        "*17/*17",
        "Ultrarapid Metabolizer",
    )
    assert unknown.status == "no_call"
    assert unknown.alternatives == [{"diplotype": "Unknown/Unknown", "phenotype": "-"}]


def test_843_haploid_pharmcat_summary_keeps_a_known_g6pd_call(tmp_path):
    phenotype = tmp_path / "haploid.phenotype.json"
    phenotype.write_text(
        json.dumps(
            {
                "geneReports": {
                    "G6PD": {
                        "recommendationDiplotypes": [
                            {
                                "allele1": {"name": "B"},
                                "allele2": None,
                                "phenotypes": ["Normal"],
                            }
                        ],
                    }
                }
            }
        )
    )
    (call,) = summarize(str(phenotype))
    assert call.status == "resolved"
    assert call.diplotype == "B"
    assert call.phenotype == "Normal"
    assert call.phenotype_consistent


def test_outside_calls_merge_and_reject_a_gene_called_twice(tmp_path):
    cyp2d6 = tmp_path / "s.cyp2d6.pharmcat.tsv"
    cyp2d6.write_text("CYP2D6\t*1/*2≥3\n", encoding="utf-8")
    hla = tmp_path / "s.hla.pharmcat.tsv"
    hla.write_text("# T1K\nHLA-A\t*02:01/*24:02\nHLA-B\t*07:02/*57:01", "utf-8")
    merged = tmp_path / "merged.tsv"

    merge_outside_calls([str(cyp2d6), str(hla)], str(merged))
    assert merged.read_text(encoding="utf-8") == (
        "CYP2D6\t*1/*2≥3\nHLA-A\t*02:01/*24:02\nHLA-B\t*07:02/*57:01\n"
    )
    with pytest.raises(WGSExtractError, match="CYP2D6 is called in both"):
        merge_outside_calls([str(cyp2d6), str(cyp2d6)], str(merged))


def test_an_outside_call_file_that_does_not_exist_is_named(tmp_path):
    with pytest.raises(WGSExtractError, match="Outside-call file not found"):
        merge_outside_calls([str(tmp_path / "nope.tsv")], str(tmp_path / "m.tsv"))
    assert not (tmp_path / "m.tsv").exists()


# --- the command, with a stand-in PharmCAT release --------------------------------

# Records how it was run, the VCF it was given and the outside calls beside its
# output, then writes the outputs the real pipeline does (or exits with the
# code in an `exit` file beside it).
FAKE_PIPELINE = """\
import json, os, shutil, sys
from pathlib import Path

here = Path(__file__).parent
args = sys.argv[1:]
out, bf = Path(args[args.index("-o") + 1]), args[args.index("-bf") + 1]
outside = out / f"{bf}.outside.tsv"
shutil.copy(args[0], here / "seen.vcf.gz")
(here / "run.json").write_text(json.dumps({
    "argv": args, "cwd": os.getcwd(),
    "outside": outside.read_text() if outside.exists() else None,
}))
if (here / "exit").exists():
    sys.exit(int((here / "exit").read_text()))
(out / f"{bf}.report.html").write_text("<html></html>")
if (here / "omit-phenotypes").exists():
    sys.exit(0)
genes = {"CYP2C19": {
    "recommendationDiplotypes": [{"allele1": {"name": "*1"}, "allele2": {
        "name": "*17"}, "phenotypes": ["Rapid Metabolizer"]}]}}
if outside.exists():
    for line in outside.read_text().splitlines():
        gene, genotype = line.split("\\t")[:2]
        a, b = genotype.split("/")
        genes[gene] = {"recommendationDiplotypes": [
            {"allele1": {"name": a}, "allele2": {"name": b}, "phenotypes": ["Outside call"]}]}
(out / f"{bf}.phenotype.json").write_text(json.dumps({"geneReports": genes}))
"""

REF_SEQS = {"chr1": "ACGT" * 100, "chr2": "TTGCA" * 80}


def _release(directory: Path) -> Path:
    """A PharmCAT release whose pipeline is FAKE_PIPELINE, with gene regions
    chr1:101-200 and chr2:11-50 and its GRCh38 reference already fetched."""
    directory.mkdir(parents=True)
    (directory / "pharmcat_pipeline").write_text(FAKE_PIPELINE)
    (directory / "pharmcat.jar").write_text("")
    (directory / "pharmcat_regions.bed").write_text(
        "chr1\t100\t200\tPX=CYP2C19\nchr2\t10\t50\tPX=DPYD\n"
    )
    (directory / "pharmcat_positions.vcf").write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=400>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )
    plain = write_fasta(directory / "reference.fna", REF_SEQS)
    with open(directory / REFERENCE_FASTA, "wb") as f:
        run_command(["bgzip", "-c", str(plain)], stdout=f)
    run_command(["samtools", "faidx", str(directory / REFERENCE_FASTA)])
    return directory


def _vcf(
    path: Path,
    body: str,
    build: str = "GRCh38",
    contig: str = "1",
    header: str = "",
    length: int | None = 400,
) -> str:
    """A bgzipped, indexed single-sample VCF on ``contig`` (body uses {c})."""
    plain = path.with_suffix("")
    plain.write_text(
        "##fileformat=VCFv4.2\n"
        f"##reference={build}\n"
        + header
        + f"##contig=<ID={contig}"
        + (f",length={length}" if length is not None else "")
        + ">\n"
        '##INFO=<ID=DP,Number=1,Type=Integer,Description="d">\n'
        '##INFO=<ID=END,Number=1,Type=Integer,Description="e">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="g">\n'
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="a">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG002\n"
        + body.format(c=contig)
    )
    return bgzip_index(plain, path)


def _base(pos: int) -> str:
    return REF_SEQS["chr1"][pos - 1]


def _view(vcf: str | Path, *options: str) -> list[str]:
    out = run_command(["bcftools", "view", *options, str(vcf)], capture_output=True)
    return out.stdout.splitlines()


# A SNP inside chr1's gene region, and one outside every region.
SNPS = (
    f"{{c}}\t150\t.\t{_base(150)}\tT\t50\tPASS\tDP=20\tGT:AD\t0/1:10,10\n"
    f"{{c}}\t300\t.\t{_base(300)}\tT\t50\tPASS\tDP=20\tGT:AD\t1/1:0,20\n"
)


@pytest.fixture
def pharmcat_env(monkeypatch):
    """PharmCAT's Pixi environment, as the running Python."""
    monkeypatch.setattr(pharmcat, "pixi_env_tool", lambda _tool: sys.executable)


@needs_htslib
def test_pharmcat_runs_its_pipeline_on_the_gene_regions_in_its_names(
    tmp_path, pharmcat_env, monkeypatch, caplog
):
    monkeypatch.setattr(runtime, "is_windows_host", lambda: False)
    release = _release(tmp_path / "pharmcat")
    vcf = _vcf(tmp_path / "HG002.hard-filtered.vcf.gz", SNPS)
    out = tmp_path / "out"
    caplog.set_level(logging.INFO)
    run_wgsextract("vcf", "pharmcat", "--vcf-input", vcf, "--pharmcat-dir",
                   str(release), "--outdir", str(out), "--absent-to-ref")  # fmt: skip

    run = json.loads((release / "run.json").read_text())
    prepared = Path(run["argv"][0])
    assert prepared.name == "prepared.vcf.gz"
    assert prepared.parent.parent == out
    assert run["argv"][1:] == ["-o", str(prepared.parent / "outputs"), "-bf", "HG002.hard-filtered", "-reporterHtml",
                               "-reporterJson", "--absent-to-ref"]  # fmt: skip
    assert run["cwd"] == str(out)
    assert run["outside"] is None
    # Only the region's SNP, renamed to chr1, without INFO and with only GT.
    assert _view(release / "seen.vcf.gz", "-H") == [
        f"chr1\t150\t.\t{_base(150)}\tT\t50\tPASS\t.\tGT\t0/1"
    ]
    assert sorted(p.name for p in out.iterdir()) == [
        "HG002.hard-filtered.confidence.tsv", "HG002.hard-filtered.phenotype.json",
        "HG002.hard-filtered.report.html",
    ]  # fmt: skip
    # The calls the diplotypes rest on, in gene regions only, with their support:
    # 20 reads, but no genotype quality to judge by.
    table = (out / "HG002.hard-filtered.confidence.tsv").read_text().splitlines()
    rows = [line.split("\t") for line in table if not line.startswith("#")]
    columns = rows[0]
    assert [dict(zip(columns, row, strict=True)) for row in rows[1:]] == [
        {"chrom": "chr1", "pos": "150", "ref": _base(150), "alt": "T",
         "genotype": "0/1", "zygosity": "het", "confidence": "moderate",
         "reasons": "gq_unknown", "depth": "20", "genotype_quality": "",
         "alt_fraction": "0.500", "phase_set": "", "gene": "CYP2C19", "PGX_STATE": ""}
    ]  # fmt: skip
    assert "CYP2C19   *1/*17" in caplog.text and "Rapid Metabolizer" in caplog.text
    assert f"PharmCAT report: {out / 'HG002.hard-filtered.report.html'}" in caplog.text
    assert "pass them with --outside-call" in caplog.text


@needs_htslib
def test_732_rare_imputed_allele_remains_low_confidence_pgx_evidence(
    tmp_path, pharmcat_env, monkeypatch
):
    monkeypatch.setattr(runtime, "is_windows_host", lambda: False)
    release = _release(tmp_path / "pharmcat")
    vcf = _vcf(
        tmp_path / "imputed.vcf.gz",
        f"{{c}}\t150\t.\t{_base(150)}\tT\t.\tPASS\tIMP;RAF=0.002\tGT\t0/1\n",
        header=(
            '##INFO=<ID=IMP,Number=0,Type=Flag,Description="Imputed">\n'
            '##INFO=<ID=RAF,Number=A,Type=Float,Description="Panel AF">\n'
        ),
    )
    result = api.vcf.pharmcat(
        api.VcfPharmcatParams(
            vcf_input=Path(vcf), pharmcat_dir=release, outdir=tmp_path / "out"
        )
    )
    summary = next(
        entry for entry in result.details["confidence"] if entry["role"] == "pgx_calls"
    )
    assert summary["counts"]["low"] == 1
    (finding,) = summary["findings"]
    assert finding["confidence"] == "low"
    assert "imputed" in finding["reasons"]
    assert finding["gene"] == "CYP2C19"


@needs_htslib
def test_issue_622_windows_report_says_cyp2d6_not_called_with_spaced_paths(
    tmp_path, pharmcat_env, monkeypatch, caplog
):
    # Windows has no `typing cyp2d6`, so without outside calls the run must say
    # CYP2D6 wasn't called; paths with spaces reach the pipeline intact.
    monkeypatch.setattr(runtime, "is_windows_host", lambda: True)
    release = _release(tmp_path / "Pharm CAT" / "release 3.4")
    vcf = _vcf(tmp_path / "my genome.vcf.gz", SNPS)
    out = tmp_path / "My Results"
    caplog.set_level(logging.INFO)
    result = api.vcf.pharmcat(
        api.VcfPharmcatParams(vcf_input=Path(vcf), pharmcat_dir=release, outdir=out)
    )
    assert (out / "my genome.phenotype.json").is_file()
    argv = json.loads((release / "run.json").read_text())["argv"]
    assert argv[1:3] == [
        "-o",
        str(Path(argv[0]).parent / "outputs"),
    ]
    assert "CYP2D6 not called" in caplog.text
    assert [w.details["gene"] for w in result.warnings if w.code == "no_call"] == [
        "CYP2D6"
    ]


@needs_htslib
def test_issue_622_windows_process_count_stays_within_61_on_many_cores(
    tmp_path, pharmcat_env, monkeypatch
):
    monkeypatch.setattr(runtime, "is_windows_host", lambda: True)
    monkeypatch.setattr(os, "cpu_count", lambda: 128)
    release = _release(tmp_path / "pharmcat")
    vcf = _vcf(tmp_path / "s.vcf.gz", SNPS)
    run_wgsextract("vcf", "pharmcat", "--vcf-input", vcf, "--pharmcat-dir",
                   str(release))  # fmt: skip
    argv = json.loads((release / "run.json").read_text())["argv"]
    # contract: Python's ProcessPoolExecutor on Windows allows at most 61 workers.
    assert argv[argv.index("-cp") + 1] == "61"


@needs_htslib
def test_pharmcat_takes_an_uncompressed_vcf(tmp_path, pharmcat_env):
    release = _release(tmp_path / "pharmcat")
    plain = tmp_path / "s.vcf"
    plain.write_text("\n".join(_view(_vcf(tmp_path / "gz.vcf.gz", SNPS))) + "\n")
    run_wgsextract("vcf", "pharmcat", "--vcf-input", str(plain), "--pharmcat-dir",
                   str(release))  # fmt: skip
    assert len(_view(release / "seen.vcf.gz", "-H")) == 1


@needs_htslib
def test_pharmcat_input_expands_gvcf_blocks_to_reference_positions(tmp_path):
    release = _release(tmp_path / "pharmcat")
    work = tmp_path / "work"
    work.mkdir()
    vcf = _vcf(
        tmp_path / "s.g.vcf.gz",
        f"chr1\t95\t.\t{_base(95)}\t<*>\t.\t.\tEND=103\tGT\t0/0\n"
        f"chr1\t104\t.\t{_base(104)}\tT,<*>\t50\t.\tDP=9\tGT:AD\t0/1:5,4,0\n",
        contig="chr1",
    )
    prepared = prepare_input(vcf, str(release), str(release / REFERENCE_FASTA),
                             str(work))  # fmt: skip
    rows = [line.split("\t") for line in _view(prepared, "-H")]
    assert [(r[1], r[3], r[4], r[9]) for r in rows] == [
        *((str(p), _base(p), ".", "./.") for p in range(101, 104)),
        ("104", _base(104), "T,<*>", "0/1"),
    ]


@needs_htslib
def test_pharmcat_input_from_a_gatk_gvcf_is_no_longer_a_gvcf(tmp_path):
    release = _release(tmp_path / "pharmcat")
    work = tmp_path / "work"
    work.mkdir()
    vcf = _vcf(
        tmp_path / "s.g.vcf.gz",
        f"chr1\t95\t.\t{_base(95)}\t<NON_REF>\t.\t.\tEND=120\tGT\t0/0\n",
        contig="chr1",
        header="##GVCFBlock0-20=minGQ=0(inclusive),maxGQ=20(exclusive)\n",
    )
    prepared = prepare_input(vcf, str(release), str(release / REFERENCE_FASTA),
                             str(work))  # fmt: skip
    assert not [line for line in _view(prepared, "-h") if "GVCFBlock" in line]


@needs_htslib
def test_pharmcat_merges_outside_calls_where_its_pipeline_looks(
    tmp_path, pharmcat_env, caplog
):
    release = _release(tmp_path / "pharmcat")
    vcf = _vcf(tmp_path / "s.vcf.gz", SNPS)
    cyp2d6 = tmp_path / "s.cyp2d6.pharmcat.tsv"
    cyp2d6.write_text("CYP2D6\t*1/*4\n")
    hla = tmp_path / "s.hla.pharmcat.tsv"
    hla.write_text("HLA-B\t*57:01/*07:02\n")
    caplog.set_level(logging.INFO)
    run_wgsextract("vcf", "pharmcat", "--vcf-input", vcf, "--pharmcat-dir",
                   str(release), "--outside-call", str(cyp2d6), "--outside-call",
                   str(hla))  # fmt: skip

    run = json.loads((release / "run.json").read_text())
    assert run["outside"] == "CYP2D6\t*1/*4\nHLA-B\t*57:01/*07:02\n"
    assert "--absent-to-ref" not in run["argv"]
    assert "pass them with --outside-call" not in caplog.text


@needs_htslib
def test_845_stale_outside_calls_do_not_change_rerun_phenotypes_or_provenance(
    tmp_path, pharmcat_env, caplog
):
    release = _release(tmp_path / "pharmcat")
    vcf = _vcf(tmp_path / "s.vcf.gz", SNPS)
    outside = tmp_path / "calls.tsv"
    outside.write_text("CYP2D6\t*1/*1\n")
    first = api.vcf.pharmcat(
        api.VcfPharmcatParams(
            vcf_input=Path(vcf),
            pharmcat_dir=release,
            outdir=tmp_path,
            outside_call=(outside,),
        )
    )
    assert (
        json.loads((release / "run.json").read_text())["outside"] == outside.read_text()
    )
    consumed = [f for f in first.inputs if f.role == "pharmcat_outside_call"]
    assert len(consumed) == 1 and consumed[0].fingerprint
    assert Path(consumed[0].path) == outside
    assert "CYP2D6" in {g["gene"] for g in first.details["pharmcat_genes"]}
    # A different input retaining the output basename must not inherit the call.
    _vcf(tmp_path / "s.vcf.gz", SNPS.replace("0/1", "1/1"))
    second = api.vcf.pharmcat(
        api.VcfPharmcatParams(
            vcf_input=Path(vcf),
            pharmcat_dir=release,
            outdir=tmp_path,
        )
    )
    run = json.loads((release / "run.json").read_text())
    assert run["outside"] is None
    assert not [f for f in second.inputs if f.role == "pharmcat_outside_call"]
    assert not [f for f in second.outputs if f.role == "pharmcat_outside_calls"]
    assert second.metrics["pharmcat_genes_resolved"] == 1
    assert "CYP2D6" not in {g["gene"] for g in second.details["pharmcat_genes"]}


@needs_htslib
def test_845_missing_new_phenotypes_cannot_publish_a_stale_outside_call_result(
    tmp_path, pharmcat_env
):
    release = _release(tmp_path / "pharmcat")
    vcf = _vcf(tmp_path / "s.vcf.gz", SNPS)
    previous = tmp_path / "s.phenotype.json"
    previous.write_text('{"geneReports":{"CYP2D6":{"old":true}}}')
    old_report = tmp_path / "s.report.html"
    old_report.write_text("previous report")
    (release / "omit-phenotypes").write_text("")
    with pytest.raises(WGSExtractError, match="readable phenotypes"):
        run_wgsextract(
            "vcf", "pharmcat", "--vcf-input", vcf, "--pharmcat-dir", str(release)
        )
    assert json.loads(previous.read_text())["geneReports"]["CYP2D6"]["old"]
    assert old_report.read_text() == "previous report"
    assert not list(tmp_path.glob(".wgse_pharmcat_*"))


QUALITY_HEADER = (
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
)


def _span_definition(release):
    (release / "pharmcat_regions.bed").write_text("chr1\t100\t110\tPX=CYP2C19\n")
    with (release / "pharmcat_positions.vcf").open("a") as stream:
        stream.write(
            f"chr1\t105\t.\t{_base(105)}{_base(106)}\t{_base(105)}\t.\tPASS\t.\n"
        )


@needs_htslib
def test_842_contig_without_length_retains_reference_calls_and_confidence_gene(
    tmp_path, pharmcat_env
):
    release = _release(tmp_path / "pharmcat")
    _span_definition(release)
    vcf = _vcf(
        tmp_path / "no-length.vcf.gz",
        f"{{c}}\t100\t.\t{_base(100)}\t<NON_REF>\t.\tPASS\tEND=110\tGT:DP:GQ\t0/0:40:60\n",
        header=QUALITY_HEADER,
        length=None,
    )
    result = api.vcf.pharmcat(
        api.VcfPharmcatParams(
            vcf_input=Path(vcf),
            pharmcat_dir=release,
            outdir=tmp_path / "out",
        )
    )
    multi = next(
        row.split("\t")
        for row in _view(release / "seen.vcf.gz", "-H")
        if len(row.split("\t")[3]) == 2
    )
    assert multi[0] == "chr1" and multi[9] == "0/0"
    summary = next(c for c in result.details["confidence"] if c["role"] == "pgx_calls")
    assert summary["findings"]
    assert all(f["gene"] == "CYP2C19" for f in summary["findings"])
    assert all(f["PGX_STATE"] == "reference" for f in summary["findings"])
    assert all(f["confidence"] == "high" for f in summary["findings"])


@needs_htslib
@pytest.mark.parametrize(
    ("fmt", "value", "extra", "reason", "depth"),
    [
        ("DP:GQ", "40:60", "", "no_call", 40),
        (
            "GT:DP:MIN_DP:GQ",
            "0/0:40:2:60",
            '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum block depth">\n',
            "low_depth",
            2,
        ),
    ],
)
def test_842_missing_genotype_and_minimum_block_depth_never_become_confident_reference(
    tmp_path, fmt, value, extra, reason, depth
):
    release = _release(tmp_path / "pharmcat")
    _span_definition(release)
    vcf = _vcf(
        tmp_path / "weak-block.vcf.gz",
        f"{{c}}\t100\t.\t{_base(100)}\t<NON_REF>\t.\tPASS\tEND=110\t{fmt}\t{value}\n",
        header=QUALITY_HEADER + extra,
    )
    work = tmp_path / "work"
    work.mkdir()
    prepared = prepare_input(
        vcf, str(release), str(release / REFERENCE_FASTA), str(work)
    )
    assert all(row.split("\t")[9] == "./." for row in _view(prepared, "-H"))
    confidence = pharmcat.label_pgx_calls(vcf, str(work), api.VcfPharmcatParams())
    (multi,) = [f.manifest() for f in confidence.findings if len(f.ref) == 2]
    assert multi["confidence"] == "low"
    assert multi["depth"] == depth and multi["genotype_quality"] == 60
    assert reason in multi["reasons"]


@needs_htslib
def test_842_structural_reference_does_not_establish_sequence_confidence_in_end_span(
    tmp_path,
):
    release = _release(tmp_path / "pharmcat")
    _span_definition(release)
    vcf = _vcf(
        tmp_path / "sv.vcf.gz",
        f"{{c}}\t101\t.\t{_base(101)}\t<DEL>\t.\tPASS\tEND=110\tGT:DP:GQ\t0/0:40:60\n",
        header=QUALITY_HEADER,
    )
    work = tmp_path / "work"
    work.mkdir()
    prepared = prepare_input(
        vcf, str(release), str(release / REFERENCE_FASTA), str(work)
    )
    assert not [r for r in _view(prepared, "-H") if len(r.split("\t")[3]) == 2]
    confidence = pharmcat.label_pgx_calls(vcf, str(work), api.VcfPharmcatParams())
    (finding,) = [f.manifest() for f in confidence.findings if f.pos == 105]
    assert finding["PGX_STATE"] == "absent"
    assert finding["confidence"] == "low"
    assert finding["depth"] is None and finding["genotype_quality"] is None


@needs_htslib
@pytest.mark.parametrize("no_call_first", [False, True])
def test_842_overlapping_no_call_keeps_worst_quality_for_single_and_multibase_definitions(
    tmp_path, no_call_first
):
    release = _release(tmp_path / "pharmcat")
    _span_definition(release)
    with (release / "pharmcat_positions.vcf").open("a") as stream:
        stream.write(f"chr1\t105\t.\t{_base(105)}\tT\t.\tPASS\t.\n")
    rows = [
        f"{{c}}\t105\t.\t{_base(105)}\t<NON_REF>\t.\tPASS\tEND=110\tGT:DP:GQ\t0/0:40:60\n",
        f"{{c}}\t105\t.\t{_base(105)}\tT\t.\tPASS\t.\tGT:DP:GQ\t./.:2:1\n",
    ]
    if no_call_first:
        rows.reverse()
    vcf = _vcf(tmp_path / "overlap.vcf.gz", "".join(rows), header=QUALITY_HEADER)
    work = tmp_path / "work"
    work.mkdir()
    prepared = prepare_input(
        vcf, str(release), str(release / REFERENCE_FASTA), str(work)
    )
    assert all(
        r.split("\t")[9] == "./."
        for r in _view(prepared, "-H")
        if r.split("\t")[1] == "105"
    )
    confidence = pharmcat.label_pgx_calls(vcf, str(work), api.VcfPharmcatParams())
    findings = [f.manifest() for f in confidence.findings if f.pos == 105]
    assert {len(f["ref"]) for f in findings} == {1, 2}
    for finding in findings:
        assert finding["confidence"] == "low"
        assert finding["PGX_STATE"] == "overlap_conflict"
        assert finding["genotype"] == "./."
        assert finding["depth"] == 2 and finding["genotype_quality"] == 1
        assert {"no_call", "low_depth", "low_gq", "reference_overlap"} <= set(
            finding["reasons"]
        )


@needs_htslib
def test_842_weak_reference_block_keeps_low_gq_in_user_confidence(
    tmp_path, pharmcat_env
):
    release = _release(tmp_path / "pharmcat")
    _span_definition(release)
    vcf = _vcf(
        tmp_path / "weak.vcf.gz",
        f"{{c}}\t100\t.\t{_base(100)}\t<NON_REF>\t.\tPASS\tEND=110\tGT:DP:GQ\t0/0:30:2\n",
        header=QUALITY_HEADER,
    )
    result = api.vcf.pharmcat(
        api.VcfPharmcatParams(
            vcf_input=Path(vcf),
            pharmcat_dir=release,
            outdir=tmp_path / "out",
        )
    )
    prepared = [row.split("\t") for row in _view(release / "seen.vcf.gz", "-H")]
    assert all(row[9] == "./." for row in prepared)
    summary = next(c for c in result.details["confidence"] if c["role"] == "pgx_calls")
    single = next(
        f for f in summary["findings"] if f["pos"] == 105 and len(f["ref"]) == 1
    )
    assert single["confidence"] == "low"
    assert single["genotype"] == "0/0"
    assert single["genotype_quality"] == 2
    assert "low_gq" in single["reasons"]
    assert single["PGX_STATE"] == "low_quality_reference"
    assert "low_gq" in (tmp_path / "out" / "weak.confidence.tsv").read_text()


@needs_htslib
@pytest.mark.parametrize(
    ("body", "genotype", "state", "gq", "depth"),
    [
        ("100\t110\t0/0:40:60", "0/0", "reference", 60, 40),
        ("100\t105\t0/0:40:60|106\t110\t0/0:20:25", "0/0", "reference", 25, 20),
        ("100\t105\t0/0:40:60|107\t110\t0/0:40:60", "./.", "reference_gap", 60, 40),
        ("100\t110\t0/0:40:60|ALT", "./.", "overlap_conflict", 60, 40),
        ("100\t110\t0:10:60", "0", "reference", 60, 10),
        ("100\t110\t0/0:40:.", "./.", "low_quality_reference", None, 40),
        ("100\t110\t./.:40:60", "./.", "explicit_no_call", 60, 40),
        (
            "100\t105\t0/0:40:60|106\t110\t0/0:40:.",
            "./.",
            "low_quality_reference",
            None,
            40,
        ),
        ("100\t110\t0/0:.:60", "./.", "low_quality_reference", 60, None),
        (
            "100\t105\t0/0:40:60|105\t110\t0/0:40:60",
            "./.",
            "overlap_conflict",
            60,
            40,
        ),
        (
            "100\t105\t0/0:40:60|106\t110\t0:40:60",
            "./.",
            "overlap_conflict",
            60,
            40,
        ),
        ("100\t110\t0/0:.:.", "./.", "low_quality_reference", None, None),
        ("", "./.", "absent", None, None),
    ],
)
def test_842_missing_multibase_fill_requires_conflict_free_whole_span(
    tmp_path, body, genotype, state, gq, depth
):
    release = _release(tmp_path / "pharmcat")
    _span_definition(release)
    lines = []
    for block in body.split("|") if body else []:
        if block == "ALT":
            lines.append(
                f"{{c}}\t106\t.\t{_base(106)}\tT\t50\tPASS\t.\tGT:DP:GQ\t0/1:40:60\n"
            )
        else:
            start, end, gt = block.split("\t")
            lines.append(
                f"{{c}}\t{start}\t.\t{_base(int(start))}\t<NON_REF>\t.\tPASS\tEND={end}\tGT:DP:GQ\t{gt}\n"
            )
    vcf = _vcf(tmp_path / "span.vcf.gz", "".join(lines), header=QUALITY_HEADER)
    work = tmp_path / "work"
    work.mkdir()
    prepared = prepare_input(
        vcf, str(release), str(release / REFERENCE_FASTA), str(work)
    )
    rows = [r.split("\t") for r in _view(prepared, "-H")]
    multi_rows = [r for r in rows if r[1] == "105" and len(r[3]) == 2]
    if state == "absent":
        assert not multi_rows
    else:
        (multi,) = multi_rows
        assert (multi[3], multi[4], multi[9]) == (
            _base(105) + _base(106),
            _base(105),
            genotype,
        )
    confidence = pharmcat.label_pgx_calls(vcf, str(work), api.VcfPharmcatParams())
    finding = next(
        f.manifest() for f in confidence.findings if f.pos == 105 and len(f.ref) == 2
    )
    assert finding["PGX_STATE"] == state
    assert finding["genotype_quality"] == gq and finding["depth"] == depth
    if genotype == "./.":
        assert finding["confidence"] == "low"
    else:
        assert finding["confidence"] in {"high", "moderate"}


@needs_htslib
def test_a_failed_pharmcat_run_is_an_error_and_leaves_no_work_files(
    tmp_path, pharmcat_env
):
    release = _release(tmp_path / "pharmcat")
    (release / "exit").write_text("3")
    vcf = _vcf(tmp_path / "s.vcf.gz", SNPS)
    out = tmp_path / "out"
    with pytest.raises(WGSExtractError, match=r"PharmCAT failed \(exit 3\)"):
        run_wgsextract("vcf", "pharmcat", "--vcf-input", vcf, "--pharmcat-dir",
                       str(release), "--outdir", str(out))  # fmt: skip
    assert list(out.iterdir()) == []


@needs_htslib
def test_pharmcat_refuses_a_grch37_vcf(tmp_path, pharmcat_env):
    _release(tmp_path / "lib" / "pharmcat")
    vcf = _vcf(tmp_path / "s.vcf.gz", SNPS, build="GRCh37")
    with pytest.raises(WGSExtractError, match="needs a GRCh38 VCF; .* is hg19"):
        run_wgsextract("vcf", "pharmcat", "--vcf-input", vcf, "--ref",
                       str(tmp_path / "lib"))  # fmt: skip
    assert not (tmp_path / "lib" / "pharmcat" / "run.json").exists()


@needs_htslib
def test_pharmcat_refuses_a_grch37_vcf_without_a_reference_library(
    tmp_path, pharmcat_env
):
    release = _release(tmp_path / "pharmcat")
    vcf = _vcf(tmp_path / "s.vcf.gz", SNPS, build="GRCh37")
    with pytest.raises(WGSExtractError, match="needs a GRCh38 VCF"):
        run_wgsextract("vcf", "pharmcat", "--vcf-input", vcf, "--pharmcat-dir",
                       str(release))  # fmt: skip


@needs_htslib
def test_pharmcat_without_its_environment_says_how_to_install_it(tmp_path, monkeypatch):
    monkeypatch.setattr(pharmcat, "pixi_env_tool", lambda _tool: None)
    release = _release(tmp_path / "pharmcat")
    vcf = _vcf(tmp_path / "s.vcf.gz", SNPS)
    with pytest.raises(WGSExtractError, match="pixi install --locked -e pharmcat"):
        run_wgsextract("vcf", "pharmcat", "--vcf-input", vcf, "--pharmcat-dir",
                       str(release))  # fmt: skip


def test_pharmcats_reference_is_fetched_once_with_its_own_downloader(tmp_path):
    (tmp_path / "pcat").mkdir()
    (tmp_path / "pcat" / "__init__.py").write_text(
        "from pathlib import Path\n"
        "def download_reference_fasta_and_index(d):\n"
        f"    Path(d, {REFERENCE_FASTA!r}).write_text('fetched ' + d)\n"
    )
    reference = pharmcat._ensure_reference([sys.executable], str(tmp_path))
    assert reference == str(tmp_path / REFERENCE_FASTA)
    assert Path(reference).read_text() == f"fetched {tmp_path}"
    (tmp_path / "pcat" / "__init__.py").write_text("raise SystemExit(1)\n")
    assert pharmcat._ensure_reference([sys.executable], str(tmp_path)) == reference
