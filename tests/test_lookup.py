"""``lookup`` (#1178): a sample's genotype, evidence state and report
findings at an rsID, locus or gene, from local files only."""

import gzip
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, snp_sample
from tests.server_helpers import Mcp
from tests.test_1052_offline_defaults import blocked_network as blocked_network
from tests.test_personal_report import _cli
from tests.test_personal_report import _phased_mutyh_report as _phased_mutyh_report
from tests.test_personal_report import served_lists as served_lists
from wgsextract_cli import api
from wgsextract_cli.core import read_scope
from wgsextract_cli.core.reference_evidence import POLICY_HEADER_KEY, Provenance

pytestmark = needs_htslib

_HEADER = """##fileformat=VCFv4.2
##contig=<ID=chr1,length=248956422>
##INFO=<ID=END,Number=1,Type=Integer,Description="End">
##FILTER=<ID=LowQual,Description="Low quality">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">
##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">
##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">
##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">
"""
_COLUMNS = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT"
# A variants-only call set (GRCh38 chr1 by its length).
_CALLS = [
    "chr1\t1000\trs1\tA\tG\t50\tPASS\t.\tGT:DP:GQ:AD\t0/1:30:60:15,15",
    "chr1\t1100\t.\tACT\tA,ACTT\t50\tPASS\t.\tGT:DP:GQ:AD\t1/2:30:50:1,14,15",
    "chr1\t1200\t.\tC\tT\t5\tPASS\t.\tGT:DP:GQ\t./.:.:.",
    "chr1\t1300\t.\tG\tA\t50\tPASS\t.\tGT:DP:GQ:AD:PS\t0|1:30:60:15,15:1300",
    "chr1\t1310\t.\tT\tC\t50\tPASS\t.\tGT:DP:GQ:AD:PS\t1|0:30:60:15,15:1300",
    "chr1\t1400\t.\tA\tC\t3\tLowQual\t.\tGT:DP:GQ:AD\t0/1:30:60:15,15",
]
# Its gVCF: the caller assessed chr1:900-1900; no record over 1600-1699.
_BLOCKS = [
    "chr1\t900\t.\tA\t<*>\t0\tPASS\tEND=999\tGT:GQ:MIN_DP\t0/0:60:30",
    *_CALLS,
    "chr1\t1500\t.\tA\t<*>\t0\tPASS\tEND=1599\tGT:GQ:MIN_DP\t0/0:5:3",
    "chr1\t1700\t.\tA\t<*>\t0\tPASS\tEND=1900\tGT:GQ:MIN_DP\t0/0:60:30",
]


def _vcf(path: Path, rows: list[str], header: str = "", samples: str = "S") -> str:
    path.write_text(
        _HEADER + header + f"{_COLUMNS}\t{samples}\n" + "".join(f"{r}\n" for r in rows)
    )
    return bgzip_index(path)


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("lookup")
    gvcf_header = Provenance("deepvariant", "1.9", "gq", region="chr1:900-1900")
    return {
        "vcf": Path(_vcf(root / "s.vcf", _CALLS)),
        "gvcf": Path(
            _vcf(
                root / "s.g.vcf",
                _BLOCKS,
                gvcf_header.header() + '\n##ALT=<ID=*,Description="Any">\n',
            )
        ),
    }


def _lookup(**values: Any) -> api.Result:
    return api.lookup.lookup(api.LookupParams(**values))


@pytest.mark.parametrize(
    ("locus", "status", "state", "genotype"),
    [
        ("GRCh38:chr1:1000", "confident_call", "variant", "0/1"),
        ("GRCh38:chr1:1200", "no_call", "no_call", "./."),
        ("GRCh38:chr1:1400", "weak_evidence", "filtered", "0/1"),
        ("GRCh38:chr1:950", "confident_reference", "callable_reference", "0/0"),
        ("GRCh38:chr1:1550", "weak_evidence", "low_quality_reference", "0/0"),
        ("GRCh38:chr1:1650", "not_assessed", "uncovered", None),
        ("GRCh38:chr1:1950", "not_assessed", "not_assessed", None),
    ],
)
def test_1178_each_evidence_state_answers_the_site(
    sample, locus, status, state, genotype
):
    site = _lookup(input=sample["vcf"], gvcf=sample["gvcf"], locus=locus).details[
        "site"
    ]
    assert (site["status"], site["evidence_state"], site["genotype"]) == (
        status,
        state,
        genotype,
    )


def test_1178_multiallelic_indel_alleles_are_normalised(sample):
    site = _lookup(
        input=sample["vcf"], gvcf=sample["gvcf"], locus="GRCh38:chr1:1100:ACT>A"
    ).details["site"]
    assert site["alleles"] == ["A", "ACTT"]
    (call,) = site["calls"]
    assert [(v["pos"], v["ref"], v["alt"], v["type"]) for v in call["variants"]] == [
        (1100, "ACT", "A", "deletion"),
        (1101, "C", "CT", "insertion"),
    ]
    assert site["query_allele_copies"] == 1


def test_1178_absent_site_of_a_variants_only_vcf_is_not_assessed(sample):
    # 950 is confident reference in the gVCF; the call set alone can't say so.
    result = _lookup(input=sample["vcf"], locus="GRCh38:chr1:950:A>G")
    site = result.details["site"]
    assert (site["status"], site["genotype"], site["query_allele_copies"]) == (
        "not_assessed",
        None,
        None,
    )
    assert "lookup_no_reference_evidence" in {w.code for w in result.warnings}


def test_1178_region_groups_phased_alleles_and_pages_calls(sample):
    first = _lookup(
        input=sample["vcf"], gvcf=sample["gvcf"], locus="GRCh38:chr1:900-1900", limit=4
    ).details["region"]
    assert [c["pos"] for c in first["calls"]] == [1000, 1100, 1200, 1300]
    assert first["phase_sets"] == [
        {
            "chrom": "chr1",
            "phase_set": "1300",
            "positions": [1300, 1310],
            "haplotypes": [["G", "C"], ["A", "T"]],
        }
    ]
    assert first["evidence"]["bases"]["uncovered"] > 0
    rest = _lookup(
        input=sample["vcf"],
        gvcf=sample["gvcf"],
        locus="GRCh38:chr1:900-1900",
        limit=4,
        cursor=first["next_cursor"],
    ).details["region"]
    assert [c["pos"] for c in rest["calls"]] == [1310, 1400]
    assert rest["next_cursor"] is None


def test_1178_gvcf_of_a_real_bcftools_run_is_found_through_its_manifest(tmp_path):
    reads = snp_sample(tmp_path, snps=(1550,))
    run = api.vcf.snp(
        api.VcfSnpParams(
            input=Path(reads.bam), ref=Path(reads.ref), outdir=tmp_path / "out",
            ploidy="GRCh38", reference_evidence="gvcf",
        )
    )  # fmt: skip
    (vcf,) = (o.path for o in run.outputs if o.role == "snps")

    def site(position: int) -> dict[str, Any]:
        details = _lookup(
            input=Path(vcf), build="GRCh38", locus=f"GRCh38:chr1:{position}"
        ).details
        assert details["reference_evidence"]["origin"] == "manifest"
        return details["site"]

    assert (site(1550)["status"], site(1550)["genotype"]) == ("confident_call", "1/1")
    assert site(2000)["status"] == "confident_reference"
    assert (site(100)["status"], site(100)["evidence_state"]) == (
        "not_assessed",
        "uncovered",
    )


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A reference library whose rsID placements put rs5327 (which dbSNP
    merged rs5328 into) at 1000, rs9618 at both 1300 and 1310, and rs7412
    at 1900, the last base the gVCF assessed."""
    table = tmp_path / "library" / "microarray" / "All_SNPs_hg38_ref.tab.gz"
    table.parent.mkdir(parents=True)
    rows = (
        "chr1\t1000\trs5327\nchr1\t1300\trs9618\nchr1\t1310\trs9618\n"
        "chr1\t1900\trs7412\n"
    )
    table.write_bytes(gzip.compress(f"#CHROM\tPOS\tID\n{rows}".encode()))
    return table.parents[1]


def test_1178_merged_rsid_resolves_to_its_current_placement(
    sample, library, blocked_network
):
    details = _lookup(
        input=sample["vcf"], gvcf=sample["gvcf"], rsid="rs5328", ref=library
    ).details
    assert (details["resolution"]["current"], details["resolution"]["history"]) == (
        "rs5327",
        "merged",
    )
    assert details["site"]["locus"]["text"] == "GRCh38:chr1:1000"
    assert details["site"]["status"] == "confident_call"
    assert {r["name"] for r in details["resources"]} >= {
        "dbsnp_rsid_history",
        "rsid_placements",
    }


def test_1178_rsid_with_several_placements_needs_an_explicit_choice(sample, library):
    result = _lookup(input=sample["vcf"], rsid="rs9618", ref=library)
    resolution = result.details["resolution"]
    assert resolution["status"] == "ambiguous"
    assert [c["locus"] for c in resolution["candidates"]] == [
        "GRCh38:chr1:1300",
        "GRCh38:chr1:1310",
    ]
    assert "site" not in result.details
    assert "lookup_ambiguous" in {w.code for w in result.warnings}
    chosen = _lookup(
        input=sample["vcf"], rsid="rs9618", ref=library, locus="GRCh38:chr1:1310"
    ).details["site"]
    assert (chosen["genotype"], chosen["calls"][0]["haplotypes"]) == ("1|0", ["C", "T"])


def test_1178_missing_rsid_placements_is_an_actionable_offline_error(
    sample, tmp_path, blocked_network
):
    code, _ = _cli(
        "lookup", "--input", str(sample["vcf"]), "--rsid", "rs429358",
        "--ref", str(tmp_path), "--json",
    )  # fmt: skip
    assert code == 1
    with pytest.raises(api.CommandFailed, match="ref microarray-targets"):
        _lookup(input=sample["vcf"], rsid="rs429358", ref=tmp_path)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ({"locus": "GRCh37:chr1:1000"}, "never converts coordinates"),
        ({"locus": "GRCh38:chr1:1000", "build": "GRCh37"}, "doesn't match"),
        ({"rsid": "rs1704"}, "withdrawn"),
    ],
)
def test_1178_mismatched_builds_and_retired_ids_are_refused(sample, query, message):
    with pytest.raises(api.CommandFailed, match=message):
        _lookup(input=sample["vcf"], **query)


@pytest.mark.parametrize(
    ("gene", "candidates"),
    [
        ("OR7-21", ["OR4F16", "OR4F29"]),  # an alias of two genes
        ("PLCXD1", ["PLCXD1", "PLCXD1"]),  # PAR: on chrX and chrY
    ],
)
def test_1178_ambiguous_gene_lists_candidates_not_a_first_hit(sample, gene, candidates):
    resolution = _lookup(input=sample["vcf"], gene=gene).details["resolution"]
    assert resolution["status"] == "ambiguous"
    assert [c["symbol"] for c in resolution["candidates"]] == candidates


def test_1178_multi_sample_vcf_needs_the_sample_named(tmp_path):
    rows = ["chr1\t1000\t.\tA\tG\t50\tPASS\t.\tGT:DP:GQ\t0/0:30:60\t1/1:30:60"]
    vcf = Path(_vcf(tmp_path / "two.vcf", rows, samples="A\tB"))
    with pytest.raises(api.CommandFailed, match="2 samples"):
        _lookup(input=vcf, locus="GRCh38:chr1:1000")
    site = _lookup(input=vcf, locus="GRCh38:chr1:1000", sample="B").details["site"]
    assert (site["genotype"], site["status"]) == ("1/1", "confident_call")


@pytest.fixture
def reported(tmp_path: Path, served_lists) -> tuple[Path, Path]:
    """Real vcf clinvar and report runs on two phased MUTYH calls."""
    _phased_mutyh_report(
        tmp_path,
        ["45329439 T A GT:PS 0|1:45329439", "45330515 C A GT:PS 1|0:45329439"],
    )
    return tmp_path / "HG002.vcf.gz", tmp_path / "report"


def test_1178_report_findings_and_unrun_analyses_are_attributed(reported):
    vcf, report = reported
    details = _lookup(input=vcf, report=report, locus="GRCh38:1:45329439:T>A").details
    found = details["interpretation"]
    assert found["status"] == "joined"
    assert found["report"]["path"].startswith(str(report))
    (finding,) = found["findings"]
    assert (finding["section"], finding["gene"], finding["match"]) == (
        "clinvar",
        "MUTYH",
        "site",
    )
    assert finding["confidence"] in ("high", "moderate", "low")
    assert finding["disclaimer"] == "Technical result, not diagnosis."
    assert finding["evidence"]["file"]
    sections = {s["section"]: s for s in found["sections"]}
    assert sections["clinvar"]["status"] == "ok"
    pharmacogenomics = sections["pharmacogenomics"]
    assert pharmacogenomics["status"] == "not_run"
    assert any("pharmcat" in m["command"] for m in pharmacogenomics["missing"])
    gene = _lookup(input=vcf, report=report, gene="MUTYH").details
    assert gene["region"]["phase_sets"][0]["positions"] == [45329439, 45330515]


def test_1178_report_of_another_input_or_build_joins_nothing(reported, tmp_path):
    # Review of #1178: a report beside the input but made from another VCF,
    # or a GRCh37 section, must not put findings at this GRCh38 site.
    vcf, report = reported
    (document,) = report.glob("*_personal_report.json")
    other = tmp_path / "other"
    other.mkdir()
    copy = Path(shutil.copy(vcf, other / "renamed.vcf.gz"))
    shutil.copy(f"{vcf}.tbi", other / "renamed.vcf.gz.tbi")
    shutil.copy(document, other)
    found = _lookup(input=copy, locus="GRCh38:1:45329439").details["interpretation"]
    assert (found["status"], found.get("findings")) == ("report_for_other_input", None)
    content = json.loads(document.read_text())
    content["sections"]["clinvar"]["build"] = "hg19"
    grch37 = tmp_path / "grch37_personal_report.json"
    grch37.write_text(json.dumps(content))
    found = _lookup(input=vcf, report=grch37, locus="GRCh38:1:45329439").details[
        "interpretation"
    ]
    sections = {s["section"]: s["status"] for s in found["sections"]}
    assert (found["findings"], sections["clinvar"]) == ([], "build_mismatch")


def test_1178_multi_sample_gvcf_is_read_for_the_chosen_sample(tmp_path):
    # #1178: evidence of a multi-sample gVCF is the chosen sample's.
    blocks = [
        "chr1\t900\t.\tA\t<*>\t0\tPASS\tEND=999\tGT:GQ:MIN_DP\t0/0:60:2\t0/0:60:30",
    ]
    calls = ["chr1\t1000\t.\tA\tG\t50\tPASS\t.\tGT:DP:GQ\t0/1:30:60\t0/0:30:60"]
    header = Provenance("deepvariant", "1.9", "gq", region="chr1:900-999").header()
    gvcf = _vcf(
        tmp_path / "two.g.vcf",
        blocks,
        header + '\n##ALT=<ID=*,Description="Any">\n',
        samples="A\tB",
    )
    vcf = _vcf(tmp_path / "two.vcf", calls, samples="A\tB")

    def status(name: str) -> str:
        return _lookup(
            input=Path(vcf), gvcf=Path(gvcf), sample=name, locus="GRCh38:chr1:950"
        ).details["site"]["status"]

    assert (status("A"), status("B")) == ("weak_evidence", "confident_reference")


def test_1178_without_a_report_says_how_to_make_one(sample):
    found = _lookup(input=sample["vcf"], locus="GRCh38:chr1:1000").details[
        "interpretation"
    ]
    assert found["status"] == "no_report"
    assert found["command"].startswith("wgsextract report --input")


def test_1178_cli_json_api_and_mcp_agree(reported, tmp_path):
    vcf, report = reported
    query = {"input": str(vcf), "report": str(report), "locus": "GRCh38:1:45329439"}
    from_api = _lookup(**{k: Path(v) if k != "locus" else v for k, v in query.items()})
    code, out = _cli(
        "lookup", "--input", query["input"], "--report", query["report"],
        "--locus", query["locus"], "--json",
    )  # fmt: skip
    assert code == 0
    mcp = Mcp("--allow", str(tmp_path))
    try:
        tools = {t["name"]: t for t in mcp.request("tools/list", {})["result"]["tools"]}
        assert tools["lookup"]["annotations"]["readOnlyHint"] is True
        reply = mcp.call("lookup", **query)
    finally:
        mcp.close()
    from_mcp = reply["structuredContent"]["result"]["details"]
    assert json.loads(out)["details"] == from_api.details == from_mcp
    assert from_api.details["site"]["status"] == "confident_call"


# Copilot review of PR #1362 (#1178) ---------------------------------------


def test_1178_rsid_with_multibase_alleles_checks_every_ref_base(sample, library):
    # ACT at 1900 covers 1901-1902, past the assessed region: not reference.
    site = _lookup(
        input=sample["vcf"],
        gvcf=sample["gvcf"],
        rsid="rs7412",
        ref=library,
        locus="GRCh38:chr1:1900:ACT>A",
    ).details["site"]
    assert (site["locus"]["end"], site["status"]) == (1902, "not_assessed")


_CHR1_UNSIZED = (
    "##contig=<ID=chr2,length=242193529>\n##contig=<ID=chr1>\n"
    + _HEADER.split("\n", 2)[2]
)


def _unsized(path: Path, rows: list[str], extra: str = "") -> Path:
    path.write_text(
        "##fileformat=VCFv4.2\n" + _CHR1_UNSIZED + extra + f"{_COLUMNS}\tS\n"
        + "\n".join(rows) + "\n"
    )  # fmt: skip
    return Path(bgzip_index(path))


def test_1178_contig_declared_without_length_keeps_its_calls(tmp_path):
    vcf = _unsized(tmp_path / "s.vcf", [_CALLS[0]])
    site = _lookup(input=vcf, build="GRCh38", locus="GRCh38:chr1:1000").details["site"]
    assert site["status"] == "confident_call"


def test_1178_gvcf_on_another_build_is_refused_without_a_chr1_length(tmp_path):
    vcf = _unsized(tmp_path / "s.vcf", [_CALLS[0]])
    grch37 = tmp_path / "grch37.g.vcf"
    grch37.write_text(
        _HEADER.replace("248956422", "249250621")
        + f"{_COLUMNS}\tS\n"
        + _BLOCKS[0]
        + "\n"
    )
    with pytest.raises(api.CommandFailed, match="another build"):
        _lookup(
            input=vcf,
            build="GRCh38",
            gvcf=Path(bgzip_index(grch37)),
            locus="GRCh38:chr1:950",
        )


def test_1178_multi_sample_pooled_depth_is_not_the_samples_evidence(tmp_path):
    # INFO/DP and DP4 pool both samples; B's own FORMAT has only GT.
    info = (
        '##INFO=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        '##INFO=<ID=DP4,Number=4,Type=Integer,Description="DP4">\n'
    )
    row = "chr1\t1000\t.\tA\tG\t50\tPASS\tDP=60;DP4=15,15,15,15\tGT\t0/0\t0/1"
    vcf = Path(_vcf(tmp_path / "two.vcf", [row], info, samples="A\tB"))
    site = _lookup(input=vcf, sample="B", locus="GRCh38:chr1:1000").details["site"]
    assert (site["status"], site["calls"][0]["depth"]) == ("weak_evidence", None)


def test_1178_partly_missing_genotype_is_not_a_complete_call(tmp_path):
    row = "chr1\t1000\t.\tA\tG\t50\tPASS\t.\tGT:DP:GQ:AD\t1/.:30:60:15,15"
    vcf = Path(_vcf(tmp_path / "s.vcf", [row]))
    site = _lookup(input=vcf, locus="GRCh38:chr1:1000:A>G").details["site"]
    assert (
        site["status"],
        site["evidence_state"],
        site["query_allele_copies"],
    ) == ("no_call", "partial_call", None)


def test_1178_reference_genotype_comes_from_the_gvcf_block(tmp_path):
    # A filtered no-call block isn't reference; a haploid block stays haploid.
    header = Provenance("deepvariant", "1.9", "gq", region="chr1:100-299").header()
    blocks = [
        "chr1\t100\t.\tA\t<*>\t0\tRefCall\tEND=199\tGT:GQ:MIN_DP\t./.:60:30",
        "chr1\t200\t.\tA\t<*>\t0\tPASS\tEND=299\tGT:GQ:MIN_DP\t0:60:30",
    ]
    filters = '##FILTER=<ID=RefCall,Description="Reference call">\n'
    gvcf = Path(
        _vcf(
            tmp_path / "s.g.vcf",
            blocks,
            header + '\n##ALT=<ID=*,Description="Any">\n' + filters,
        )
    )
    vcf = Path(_vcf(tmp_path / "s.vcf", []))

    def site(pos: int) -> dict[str, Any]:
        return _lookup(input=vcf, gvcf=gvcf, locus=f"GRCh38:chr1:{pos}:A>G").details[
            "site"
        ]

    assert (site(150)["genotype"], site(150)["query_allele_copies"]) == (None, None)
    assert (site(250)["status"], site(250)["genotype"]) == ("confident_reference", "0")


def test_1178_unindexed_gvcf_block_starting_far_before_the_site(tmp_path):
    gvcf = tmp_path / "long.g.vcf"
    gvcf.write_text(
        _HEADER
        + '##ALT=<ID=*,Description="Any">\n'
        + f"{_COLUMNS}\tS\n"
        + "chr1\t1\t.\tA\t<*>\t0\tPASS\tEND=10000\tGT:GQ:MIN_DP\t0/0:60:30\n"
    )
    vcf = Path(_vcf(tmp_path / "s.vcf", []))
    site = _lookup(input=vcf, gvcf=gvcf, locus="GRCh38:chr1:5000").details["site"]
    assert site["status"] == "confident_reference"


def test_1178_haploid_policy_sets_the_depth_thresholds(tmp_path):
    # 7 reads are moderate for a haploid chrX call (thresholds halved).
    policy = Provenance(
        "deepvariant", "1.9", "gq", ploidy="chrX:10001-2781479=1"
    ).header(key=POLICY_HEADER_KEY)
    row = "chrX\t50000\t.\tA\tG\t50\tPASS\t.\tGT:DP:GQ:AD\t1/1:7:30:0,7"
    vcf = Path(
        _vcf(
            tmp_path / "x.vcf",
            [row],
            "##contig=<ID=chrX,length=156040895>\n" + policy + "\n",
        )
    )
    site = _lookup(input=vcf, locus="GRCh38:chrX:50000").details["site"]
    assert (site["status"], site["confidence"]) == ("confident_call", "moderate")


def test_1178_server_scope_hides_a_report_link_outside_it(reported, tmp_path):
    # A report link beside the input pointing outside a server job's allowed
    # folders is not read.
    vcf, report = reported
    (document,) = report.glob("*_personal_report.json")
    (vcf.parent / document.name).symlink_to(document)
    with read_scope.limited_to(lambda path: str(report) not in path):
        found = _lookup(input=vcf, locus="GRCh38:1:45329439").details
    assert found["interpretation"]["status"] == "no_report"


def test_1178_server_scope_hides_a_manifest_gvcf_outside_it(tmp_path):
    reads = snp_sample(tmp_path, snps=(1550,))
    run = api.vcf.snp(
        api.VcfSnpParams(
            input=Path(reads.bam), ref=Path(reads.ref), outdir=tmp_path / "out",
            ploidy="GRCh38", reference_evidence="gvcf",
        )
    )  # fmt: skip
    (vcf,) = (o.path for o in run.outputs if o.role == "snps")
    with read_scope.limited_to(lambda path: not path.endswith(".g.vcf.gz")):
        details = _lookup(
            input=Path(vcf), build="GRCh38", locus="GRCh38:chr1:2000"
        ).details
    assert (details["reference_evidence"], details["site"]["status"]) == (
        None,
        "not_assessed",
    )


def test_1178_structural_variant_finding_matches_inside_its_interval(
    reported, tmp_path
):
    vcf, report = reported
    (document,) = report.glob("*_personal_report.json")
    content = json.loads(document.read_text())
    finding = dict(content["sections"]["clinvar"]["findings"][0])
    finding.update(
        section="structural_variants",
        label="DEL 1:45320000-45340000",
        evidence={**finding["evidence"], "region": "1:45320000-45340000"},
        variant={**finding["variant"], "pos": 45320000, "ref": None, "alt": "<DEL>"},
    )
    content["sections"]["structural_variants"]["findings"] = [finding]
    edited = tmp_path / "sv_personal_report.json"
    edited.write_text(json.dumps(content))
    found = _lookup(input=vcf, report=edited, locus="GRCh38:1:45330515").details[
        "interpretation"
    ]
    assert "structural_variants" in {f["section"] for f in found["findings"]}
