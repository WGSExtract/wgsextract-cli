"""#1184: `--y-scope associated` keeps the reference's qualified Y-associated
contigs (GRCh38 chrY_KI270740v1_random), as WGS Extract v4's Y and Y+MT BAMs
did; the default primary scope keeps chrY (and chrM) only."""

from collections import Counter
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, sam_to_bam, vcf_calls
from wgsextract_cli import api
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib

KI_MD5 = "69e42252aead509bf56f1ea6fda91405"
# Names per dictionary: UCSC hg38, Ensembl/GATK GRCh38, RefSeq GRCh38.
UCSC = {"1": "chr1", "Y": "chrY", "MT": "chrM", "KI": "chrY_KI270740v1_random"}
ENSEMBL = {"1": "1", "Y": "Y", "MT": "MT", "KI": "KI270740.1"}
REFSEQ = {"1": "NC_000001.11", "Y": "NC_000024.10", "MT": "MT", "KI": "NT_187395.1"}
GRCH38 = {"1": 248_956_422, "Y": 57_227_415, "MT": 16_569, "KI": 37_240}


def _header(names, lengths=GRCH38, ki_md5=KI_MD5, unplaced="chrUn_KI270302v1"):
    """@SQ lines in GRCh38 analysis-set order: 1, Y, M, the Y random contig
    and an unrelated unplaced contig."""
    rows = ["@HD\tVN:1.6\tSO:coordinate"]
    for key in ("1", "Y", "MT", "KI"):
        if key in names:
            md5 = f"\tM5:{ki_md5}" if key == "KI" and ki_md5 else ""
            rows.append(f"@SQ\tSN:{names[key]}\tLN:{lengths[key]}{md5}")
    rows.append(f"@SQ\tSN:{unplaced}\tLN:2274")
    return rows


def _reads(names, unplaced="chrUn_KI270302v1"):
    """Records as (name, flag, contig) with: a Y pair; a pair split between Y
    and chr1; reads, a supplementary, a secondary and a placed-unmapped mate on
    the Y random contig; chrM, chr1, unplaced and unplaced-unmapped reads."""
    y, ki, mt, one = names["Y"], names.get("KI"), names["MT"], names["1"]
    seq, qual = "ACGT" * 5, "I" * 20
    rows = [
        ("ypair", 99, y, 1000, "=", 1100),
        ("ypair", 147, y, 1100, "=", 1000),
        ("cross", 97, y, 5000, one, 200),
        ("cross", 145, one, 200, y, 5000),
        ("split", 0, y, 3000, "*", 0),
        ("multi", 0, y, 4000, "*", 0),
        ("mito", 0, mt, 100, "*", 0),
        ("auto", 0, one, 100, "*", 0),
        ("unpl", 0, unplaced, 100, "*", 0),
        ("lost", 4, "*", 0, "*", 0),
    ]
    if ki:
        rows += [
            ("rand", 0, ki, 500, "*", 0),
            ("split", 2048, ki, 700, "*", 0),
            ("multi", 256, ki, 900, "*", 0),
            ("mate", 73, ki, 1000, "=", 1000),
            ("mate", 133, ki, 1000, "=", 1000),
        ]
    return [
        f"{q}\t{f}\t{c}\t{p}\t{0 if f & 4 else 60}\t{'*' if f & 4 else '20M'}"
        f"\t{mc}\t{mp}\t0\t{seq}\t{qual}"
        for q, f, c, p, mc, mp in rows
    ]


def _bam(tmp_path, names, **header):
    lines = _header(names, **header) + _reads(
        names, header.get("unplaced", "chrUn_KI270302v1")
    )
    return sam_to_bam(lines, tmp_path / "sample.bam")


def _records(bam) -> Counter:
    text = run_command(["samtools", "view", str(bam)], capture_output=True).stdout
    return Counter(
        (row[0], int(row[1]), row[2])
        for row in (line.split("\t") for line in text.splitlines())
    )


def _expected(bam, contigs) -> Counter:
    return Counter({r: n for r, n in _records(bam).items() if r[2] in contigs})


_COMMANDS = {
    "ydna-bam": (api.extract.ydna_bam, api.YdnaBamParams, {}, "Y"),
    "y-mt-extract": (api.extract.y_mt_extract, api.YMtExtractParams, {}, "Y_MT"),
    "region-y": (
        api.extract.region,
        api.ExtractRegionParams,
        {"target": "y", "formats": "bam"},
        "Y",
    ),
    "region-ymt": (
        api.extract.region,
        api.ExtractRegionParams,
        {"target": "ymt", "formats": "bam"},
        "Y_MT",
    ),
}


def _run(command, bam, outdir, scope, **extra):
    run, params, fields, _ = _COMMANDS[command]
    return run(
        params(input=bam, outdir=outdir, y_scope=scope, **fields, **extra),
    )


@pytest.mark.parametrize("command", sorted(_COMMANDS))
@pytest.mark.parametrize("scope", ["primary", "associated"])
def test_1184_scopes_keep_exact_records_once_in_header_order(tmp_path, command, scope):
    bam = _bam(tmp_path, UCSC)
    with_mt = "MT" in _COMMANDS[command][3]
    contigs = ["chrY", *(["chrM"] if with_mt else [])]
    if scope == "associated":
        contigs.append("chrY_KI270740v1_random")
    result = _run(command, bam, tmp_path / "o", scope)

    suffix = "" if scope == "primary" else "_associated"
    out = tmp_path / "o" / f"sample_{_COMMANDS[command][3]}{suffix}.bam"
    assert [Path(o.path) for o in result.outputs] == [out]
    # Every record on the chosen contigs (secondary, supplementary and
    # placed-unmapped included) exactly once; none from elsewhere.
    assert _records(out) == _expected(bam, contigs)
    if scope == "primary":
        assert not {r for r in _records(out) if r[2] == "chrY_KI270740v1_random"}
    # Coordinate-sorted by the header's order, so the index takes it.
    order = run_command(["samtools", "view", str(out)], capture_output=True).stdout
    seen = list(dict.fromkeys(line.split("\t")[2] for line in order.splitlines()))
    assert seen == contigs
    assert Path(f"{out}.bai").exists()
    assert result.outputs[0].metrics["contigs"] == ",".join(contigs)
    rows = {row["name"]: row for row in result.details["contigs"]}
    assert result.details["y_scope"] == scope
    ki = rows["chrY_KI270740v1_random"]
    assert ki["included"] is (scope == "associated")
    if scope == "associated":
        # rand, split's supplementary and multi's secondary map; mate's mate
        # is placed beside it unmapped.
        assert (ki["mapped_records"], ki["placed_unmapped_records"]) == (4, 1)
    else:
        assert "--y-scope associated" in ki["reason"]
    assert "chrUn_KI270302v1" not in rows


@pytest.mark.parametrize("names", [ENSEMBL, REFSEQ], ids=["ensembl", "refseq"])
def test_1184_associated_contig_is_found_under_other_dictionaries_names(
    tmp_path, names
):
    bam = _bam(tmp_path, names)
    result = _run("ydna-bam", bam, tmp_path / "o", "associated")
    out = tmp_path / "o" / "sample_Y_associated.bam"
    assert _records(out) == _expected(bam, {names["Y"], names["KI"]})
    assert result.outputs[0].metrics["contigs"] == f"{names['Y']},{names['KI']}"


@pytest.mark.parametrize(
    ("header", "reason"),
    [
        ({"lengths": {**GRCH38, "KI": 37_000}}, "length 37000"),
        ({"ki_md5": "0" * 32}, "M5 0000"),
        ({"lengths": {**GRCH38, "Y": 59_373_566}}, "primary Y is 59373566"),
    ],
    ids=["length", "md5", "hg19-y"],
)
def test_1184_a_same_named_contig_from_another_sequence_is_not_read(
    tmp_path, header, reason
):
    bam = _bam(tmp_path, UCSC, **header)
    result = _run("ydna-bam", bam, tmp_path / "o", "associated")
    assert _records(tmp_path / "o" / "sample_Y_associated.bam") == _expected(
        bam, {"chrY"}
    )
    (ki,) = [r for r in result.details["contigs"] if r["role"] == "y_associated"]
    assert ki["included"] is False and reason in ki["reason"]


@pytest.mark.parametrize(
    ("y", "length"), [("chrY", 59_373_566), ("chrY", 62_460_029), ("Y", 57_227_415)]
)
def test_1184_without_associated_contigs_only_the_primary_y_is_read(
    tmp_path, y, length
):
    names = {"1": "chr1", "Y": y, "MT": "chrM"}
    bam = _bam(tmp_path, names, lengths={**GRCH38, "Y": length}, unplaced="chrY_alt9")
    result = _run("ydna-bam", bam, tmp_path / "o", "associated")
    assert _records(tmp_path / "o" / "sample_Y_associated.bam") == _expected(bam, {y})
    rows = {row["name"]: row for row in result.details["contigs"]}
    # A Y-like contig no supported assembly assigns to Y is named, not read.
    assert rows["chrY_alt9"]["included"] is False
    assert "not a recognised" in rows["chrY_alt9"]["reason"]


def test_1184_associated_scope_needs_a_y_in_the_header(tmp_path):
    names = {"1": "chr1", "Y": "chrX", "MT": "chrM"}
    bam = _bam(tmp_path, names)
    with pytest.raises(api.CommandFailed, match="names no Y chromosome"):
        _run("ydna-bam", bam, tmp_path / "o", "associated")
    assert not list((tmp_path / "o").glob("*.bam"))


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"target": "mt", "formats": "bam", "y_scope": "associated"}, "--y-scope"),
        (
            {"target": "y", "formats": "bam", "poz": True, "y_scope": "associated"},
            "--poz",
        ),
        ({"target": "y", "formats": "bam", "y_scope": "all"}, "primary, associated"),
        (
            {"target": "y", "formats": "vcf", "ybrowse": True, "y_scope": "associated"},
            "--ybrowse",
        ),
    ],
)
def test_1184_scope_conflicts_are_usage_errors(tmp_path, fields, error):
    with pytest.raises(api.UsageError, match=error):
        api.ExtractRegionParams(input=tmp_path / "x.bam", outdir=tmp_path, **fields)


@pytest.fixture(scope="module")
def chromosome_scale_ref(tmp_path_factory):
    """A GRCh38-length reference of A's for chrY and the Y random contig
    (written in chunks, once per module), and one without the random contig."""
    tmp_path = tmp_path_factory.mktemp("refs")

    def write(path, contigs):
        with open(path, "w") as fasta:
            for name, length in contigs:
                fasta.write(f">{name}\n")
                chunk = "A" * 60 + "\n"
                for _ in range(length // 60):
                    fasta.write(chunk)
                if length % 60:
                    fasta.write("A" * (length % 60) + "\n")
        run_command(["samtools", "faidx", str(path)])
        return str(path)

    full = [
        ("chr1", 1000),
        ("chrY", GRCH38["Y"]),
        ("chrM", 16_569),
        ("chrY_KI270740v1_random", GRCH38["KI"]),
    ]
    return {
        "full": write(tmp_path / "full.fa", full),
        "no_ki": write(tmp_path / "no_ki.fa", full[:3]),
    }


def _snp_bam(tmp_path):
    """Twelve reads carrying C over position 300 of chrY and of the Y random
    contig, against an all-A reference."""
    header = [
        "@SQ\tSN:chr1\tLN:1000",
        f"@SQ\tSN:chrY\tLN:{GRCH38['Y']}",
        "@SQ\tSN:chrM\tLN:16569",
        f"@SQ\tSN:chrY_KI270740v1_random\tLN:{GRCH38['KI']}",
    ]
    reads = [
        f"{c}_{s}\t0\t{c}\t{s}\t60\t100M\t*\t0\t0\t"
        f"{'A' * (300 - s)}C{'A' * (s - 201)}\t{'I' * 100}"
        for c in ("chrY", "chrY_KI270740v1_random")
        for s in range(251, 300, 4)
    ]
    return sam_to_bam(header + reads, tmp_path / "snp.bam")


def test_1184_associated_region_calls_on_the_y_random_contig(
    tmp_path, chromosome_scale_ref
):
    bam = _snp_bam(tmp_path)
    for scope in ("primary", "associated"):
        api.extract.region(
            api.ExtractRegionParams(
                input=bam,
                outdir=tmp_path / scope,
                ref=chromosome_scale_ref["full"],
                target="y",
                formats="vcf",
                y_scope=scope,
            )
        )
    assert [c[:4] for c in vcf_calls(tmp_path / "primary" / "snp_Y.vcf.gz")] == [
        ("chrY", "300", "A", "C")
    ]
    calls = vcf_calls(tmp_path / "associated" / "snp_Y_associated.vcf.gz")
    assert [c[:4] for c in calls] == [
        ("chrY", "300", "A", "C"),
        ("chrY_KI270740v1_random", "300", "A", "C"),
    ]


def test_1184_reference_without_the_associated_contig_is_a_local_error(
    tmp_path, chromosome_scale_ref
):
    bam = _snp_bam(tmp_path)
    with pytest.raises(api.CommandFailed, match="has no chrY_KI270740v1_random"):
        api.extract.region(
            api.ExtractRegionParams(
                input=bam,
                outdir=tmp_path / "o",
                ref=chromosome_scale_ref["no_ki"],
                target="y",
                formats="vcf",
                y_scope="associated",
            )
        )
    assert not list((tmp_path / "o").glob("*.vcf.gz"))


def test_1184_unindexed_reference_is_checked_not_silently_skipped(
    tmp_path, chromosome_scale_ref
):
    # Review of #1369: with no .fai the check was skipped, and bcftools then
    # wrote a VCF with no calls on the missing contig and exit status 0.
    bam = _snp_bam(tmp_path)
    unindexed = tmp_path / "unindexed.fa"
    unindexed.symlink_to(chromosome_scale_ref["no_ki"])
    with pytest.raises(api.CommandFailed, match="no readable .fai or .dict"):
        api.extract.region(
            api.ExtractRegionParams(
                input=bam,
                outdir=tmp_path / "o",
                ref=unindexed,
                target="y",
                formats="vcf",
                y_scope="associated",
            )
        )
    assert not list((tmp_path / "o").glob("*.vcf.gz"))
