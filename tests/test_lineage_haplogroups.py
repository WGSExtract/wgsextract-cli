"""Native Yleaf and Haplogrep integration using real bcftools/samtools."""

import csv
import gzip
import json
import logging
import random
import sys
import textwrap
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.haplogrep_helpers import mini_tree as mini_tree
from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    needs_tools,
    run_wgsextract,
    sam_to_bam,
    write_fasta,
)
from tests.test_1052_offline_defaults import blocked_network as blocked_network
from tests.yleaf_helpers import native_tables as native_tables
from wgsextract_cli.api import CommandFailed
from wgsextract_cli.commands._lineage_ydna import _yleaf_build
from wgsextract_cli.core.ref_library.mitochondrial import (
    CHM13_CHRM,
    CHM13_CHRM_MD5,
    RCRS_MD5,
    pinned_sequences,
)
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.ydna.yleaf_native.trees import MANIFEST

# Haplogrep's trees are downloaded; serve a tiny one instead.
pytestmark = [needs_htslib, pytest.mark.usefixtures("mini_tree")]

_HAPLOGREP = '"SampleID"\t"Range"\t"Haplogroup"\t"Rank"\t"Quality"\n'


def _fake_tool(path: Path, body: str) -> str:
    """A Python stand-in that logs its argv, env and input records to calls.jsonl."""
    path.write_text(
        textwrap.dedent(
            """\
            import gzip, json, os, sys
            from pathlib import Path
            argv = sys.argv[1:]
            here = Path(__file__).parent

            def records(vcf):
                opener = gzip.open if vcf.endswith(".gz") else open
                with opener(vcf, "rt") as f:
                    lines = [l.rstrip("\\n").split("\\t") for l in f]
                return [l[:2] + l[3:5] + l[9:10] for l in lines if l[0][:1] != "#"]

            def log(**extra):
                entry = {"argv": argv, "env": os.environ.get("YLEAF_DATA_DIR"), **extra}
                with open(here / "calls.jsonl", "a") as f:
                    f.write(json.dumps(entry) + "\\n")

            """
        )
        + textwrap.dedent(body)
    )
    return str(path)


def _haplogrep(
    tmp_path: Path, result=_HAPLOGREP + '"kit"\t"1-16569"\t"H1c"\t"1"\t"0.93"\n', code=0
) -> str:
    return _fake_tool(
        tmp_path / "haplogrep.py",
        f"""\
        if not argv:
            sys.exit(print("Haplogrep 3 3.2.2"))
        given = argv[argv.index("--in") + 1]
        with gzip.open(given, "rt") as f:
            contigs = [l for l in f if l.startswith("##contig")]
        log(records=records(given), contigs=contigs)
        Path(argv[argv.index("--out") + 1]).write_text({result!r})
        sys.exit({code})
        """,
    )


def _calls(tmp_path: Path) -> list[dict]:
    lines = (tmp_path / "calls.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def _vcf(path: Path, contigs: dict[str, int], body: str, meta: str = "") -> str:
    text = (
        "##fileformat=VCFv4.2\n" + meta
        + "".join(f"##contig=<ID={c},length={n}>\n" for c, n in contigs.items())
        + '##FORMAT=<ID=GT,Number=1,Type=String,Description="g">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tkit\n" + body
    )  # fmt: skip
    plain = path.with_suffix("")
    plain.write_text(text)
    return bgzip_index(plain, path)


_HG38 = {"chr1": 248_956_422, "chrX": 156_040_895, "chrY": 57_227_415, "chrM": 16_569}
_KIT_BODY = (
    "chr1\t1000\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n"
    "chrX\t5000\t.\tC\tT\t50\tPASS\t.\tGT\t1\n"
    "chrY\t2781480\t.\tC\tT\t50\tPASS\t.\tGT\t1\n"
    "chrY\t15000000\t.\tG\tA\t50\tPASS\t.\tGT\t1\n"
    "chrM\t73\t.\tA\tG\t50\tPASS\t.\tGT\t1\n"
    "chrM\t263\t.\tA\tG\t50\tPASS\t.\tGT\t1\n"
)


# --- y-haplogroup ------------------------------------------------------------


def _y_rows(output: Path, suffix: str) -> list[dict[str, str]]:
    with (output / f"yleaf/kit/kit.{suffix}").open() as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def test_855_unfiltered_caller_vcf_needs_explicit_quality_assessment(
    tmp_path, native_tables
):
    meta = (
        '##INFO=<ID=END,Number=1,Type=Integer,Description="Block end">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Block minimum">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    )
    body = (
        "chrY\t100\t.\tA\tG\t60\t.\t.\tGT:DP:GQ\t1:20:60\n"
        "chrY\t110\t.\tA\tG\t60\t.\t.\tGT:DP:GQ\t1:20:60\n"
        "chrY\t120\t.\tA\tG\t5\t.\t.\tGT:DP:GQ\t1:20:60\n"
        "chrY\t135\t.\tA\t<NON_REF>\t60\t.\tEND=160\tGT:MIN_DP:GQ\t0:20:60\n"
    )
    raw = _vcf(tmp_path / "raw.vcf.gz", {"chrY": 1000}, body, meta)
    reference = write_fasta(tmp_path / "reference.fa", {"chrY": "A" * 1000})
    with pytest.raises(WGSExtractError, match="could not predict"):
        run_wgsextract(
            "lineage",
            "y-haplogroup",
            "--input",
            raw,
            "--build",
            "hg38",
            "--ref",
            str(reference),
            "--outdir",
            str(tmp_path / "unassessed"),
        )
    assessed = tmp_path / "kit.vcf.gz"
    run_command(
        [
            "bcftools",
            "filter",
            "--exclude",
            "QUAL<20",
            "--soft-filter",
            "LowQual",
            "--mode",
            "+",
            "-Oz",
            "-o",
            str(assessed),
            raw,
        ]
    )
    run_command(["bcftools", "index", str(assessed)])
    output = tmp_path / "assessed"
    run_wgsextract(
        "lineage",
        "y-haplogroup",
        "--input",
        str(assessed),
        "--build",
        "hg38",
        "--ref",
        str(reference),
        "--outdir",
        str(output),
    )
    assert Counter(row["state"] for row in _y_rows(output, "out")) == {"D": 2, "A": 5}
    assert {row["pos"] for row in _y_rows(output, "fmf")} == {"120", "130"}


@pytest.mark.parametrize("heterozygote", ["0/1", "1/0", "0|1", "1|0"])
@pytest.mark.parametrize("with_ad", [False, True])
def test_855_y_no_call_filtered_and_gt_order_are_unknown(
    tmp_path, native_tables, heterozygote, with_ad
):
    meta = (
        '##FILTER=<ID=LowQual,Description="Failed quality">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    )
    if with_ad:
        meta += '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
    records = [
        (100, "PASS", "1", "20", "60", "0,20"),
        (110, "PASS", "1/1", "20", "60", "0,20"),
        (120, "LowQual", "1/1", "20", "60", "0,20"),
        (130, "PASS", "./.", "20", "60", "0,20"),
        (140, "PASS", heterozygote, "20", "60", "0,20"),
        (150, "PASS", ".", "20", "60", "20,0"),
        (160, "PASS", "1", "20", "5", "0,20"),
    ]
    body = "".join(
        f"chrY\t{pos}\t.\tA\tG\t60\t{flt}\t.\tGT:DP:GQ"
        + (":AD" if with_ad else "")
        + f"\t{gt}:{dp}:{gq}"
        + (f":{ad}" if with_ad else "")
        + "\n"
        for pos, flt, gt, dp, gq, ad in records
    )
    vcf = _vcf(tmp_path / "kit.vcf.gz", {"chrY": 1000}, body, meta)
    reference = write_fasta(tmp_path / "reference.fa", {"chrY": "A" * 1000})
    panel = tmp_path / "positions.txt"
    panel.write_text(
        (native_tables / "hg38/new_positions.txt")
        .read_text()
        .replace("150\tA>G\tA\tG", "150\tG>A\tG\tA")
        .replace("151\tA>G\tA\tG", "151\tG>A\tG\tA")
    )
    output = tmp_path / "output"
    result = CommandResult(command="lineage y-haplogroup")
    with collecting(result):
        run_wgsextract(
            "lineage",
            "y-haplogroup",
            "--input",
            vcf,
            "--build",
            "hg38",
            "--ref",
            str(reference),
            "--outdir",
            str(output),
            "--pos-file",
            str(panel),
        )
    passing = _y_rows(output, "out")
    failed = _y_rows(output, "fmf")
    assert [(r["pos"], r["state"]) for r in passing] == [("100", "D"), ("110", "D")]
    assert len(failed) == 7
    assert {r["state"] for r in failed} == {"NA"}
    assert (
        next(r for r in failed if r["pos"] == "151")["Description"]
        == "Unassessed position"
    )
    assert any(w.code == "y_evidence_rejected" for w in result.warnings)


@pytest.mark.parametrize("build", ["hg19", "hg38", "t2t"])
def test_855_y_callable_reference_block_not_fasta_absence(
    tmp_path, native_tables, build
):
    meta = (
        '##INFO=<ID=END,Number=1,Type=Integer,Description="Block end">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    )
    body = (
        "chrY\t100\t.\tA\tG\t60\tPASS\t.\tGT:DP:GQ\t1:20:60\n"
        "chrY\t110\t.\tA\tG\t60\tPASS\t.\tGT:DP:GQ\t1/1:20:60\n"
        "chrY\t135\t.\tA\t<NON_REF>\t60\tPASS\tEND=160\tGT:MIN_DP:GQ\t0/0:20:60\n"
        "chrY\t150\t.\tA\tG\t60\tPASS\t.\tGT:DP:GQ\t./.:20:60\n"
    )
    vcf = _vcf(tmp_path / "kit.vcf.gz", {"chrY": 1000}, body, meta)
    reference = write_fasta(tmp_path / "reference.fa", {"chrY": "A" * 1000})
    output = tmp_path / "output"
    run_wgsextract(
        "lineage",
        "y-haplogroup",
        "--input",
        vcf,
        "--build",
        build,
        "--ref",
        str(reference),
        "--outdir",
        str(output),
    )
    passing = _y_rows(output, "out")
    assert Counter(r["state"] for r in passing) == {"D": 2, "A": 4}
    assert {r["pos"] for r in _y_rows(output, "fmf")} == {"120", "130", "150"}
    assert all(int(r["reads"]) == 20 for r in passing)


@pytest.mark.parametrize(
    ("minimum", "expected_ancestral"),
    [("", 0), (";MIN_DP=3", 0), (";MIN_DP=20", 5)],
)
@pytest.mark.parametrize(
    "depth_format,depth_sample", [("GT:DP:GQ", "0:20:60"), ("GT:GQ", "0:60")]
)
def test_855_y_reference_block_point_depth_is_not_interior_evidence(
    tmp_path, native_tables, minimum, expected_ancestral, depth_format, depth_sample
):
    meta = (
        '##INFO=<ID=END,Number=1,Type=Integer,Description="Block end">\n'
        '##INFO=<ID=MIN_DP,Number=1,Type=Integer,Description="Block minimum depth">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Point or average depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    )
    body = (
        "chrY\t100\t.\tA\tG\t60\tPASS\t.\tGT:DP:GQ\t1:20:60\n"
        "chrY\t110\t.\tA\tG\t60\tPASS\t.\tGT:DP:GQ\t1:20:60\n"
        f"chrY\t135\t.\tA\t<NON_REF>\t60\tPASS\tEND=160{minimum}\t{depth_format}\t{depth_sample}\n"
    )
    vcf = _vcf(tmp_path / "kit.vcf.gz", {"chrY": 1000}, body, meta)
    reference = write_fasta(tmp_path / "reference.fa", {"chrY": "A" * 1000})
    output = tmp_path / "output"
    run_wgsextract(
        "lineage",
        "y-haplogroup",
        "--input",
        vcf,
        "--build",
        "hg38",
        "--ref",
        str(reference),
        "--outdir",
        str(output),
    )
    states = Counter(r["state"] for r in _y_rows(output, "out"))
    assert states["D"] == 2
    assert states["A"] == expected_ancestral


@pytest.mark.parametrize(
    ("format_field", "sample"),
    [
        ("GT:DP", "1:."),
        ("GT:DP", "1:0"),
        ("GT:DP", "1:9"),
        ("GT:AD:DP", "0/0:0,20:20"),
        ("GT:AD:DP", "1:.:."),
    ],
)
def test_855_y_missing_depth_and_ad_gt_disagreement_cannot_predict(
    tmp_path, native_tables, format_field, sample
):
    meta = (
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
    )
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        {"chrY": 1000},
        f"chrY\t100\t.\tA\tG\t60\tPASS\t.\t{format_field}\t{sample}\n",
        meta,
    )
    output = tmp_path / "output"
    with pytest.raises(WGSExtractError, match="could not predict"):
        run_wgsextract(
            "lineage",
            "y-haplogroup",
            "--input",
            vcf,
            "--build",
            "hg38",
            "--no-ref-inference",
            "--outdir",
            str(output),
        )
    assert not _y_rows(output, "out")


@pytest.mark.parametrize("tree_links", [False, True])
def test_y_haplogroup_from_vcf_uses_native_markers_only_on_chry(
    tmp_path,
    capsys,
    tree_links,
    native_tables,
):
    body = "chr1\t100\t.\tA\tT\t50\tPASS\t.\tGT\t1\n" + "".join(
        f"chrY\t{pos}\t.\tA\tG\t50\tPASS\t.\tGT\t{gt}\n"
        for pos, gt in ((100, 1), (110, 1), (120, 1), (130, 1), (140, 0), (150, 0))
    )
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        _HG38,
        body.replace("\tGT\t", "\tGT:DP\t").replace("\n", ":20\n"),
        '##reference=GRCh38.fa\n##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n',
    )
    run_wgsextract(
        "lineage", "y-haplogroup", "--input", vcf, "--outdir", str(tmp_path / "o"),
        "--no-ref-inference",
        *(["--tree-links"] if tree_links else []),
    )  # fmt: skip
    assert sorted(p.name for p in (tmp_path / "o").iterdir()) == ["yleaf"]
    output = capsys.readouterr().out
    assert "R1b1\tM-leaf\tVCF\t6\t1.0\t1.0\t1.0\t1.0" in output
    assert "Ralf et al." in output
    # Issue #359: the result names the tree release it was called against.
    yfull = MANIFEST["trees"]["yfull"]
    assert (
        f"tree YFull YTree {yfull['version']} ({yfull['date']}, {yfull['license']})"
        in output
    )
    assert ("https://www.yfull.com/tree/M-leaf/" in output) is tree_links
    assert (
        "https://discover.familytreedna.com/y-dna/M-leaf/tree" in output
    ) is tree_links


@pytest.mark.parametrize(
    "prediction",
    [
        "Sample_name\tHg\tHg_marker\n",
        "Sample_name\tHg\tHg_marker\tValid_markers\nkit\tNA\t\t0\n",
        "Sample_name\tHg\tHg_marker\tValid_markers\nkit\tNA\t\t3\n",
    ],
)
def test_y_haplogroup_without_a_prediction_is_an_error(
    tmp_path, monkeypatch, prediction
):
    from wgsextract_cli.ydna.yleaf_native import analysis

    def no_call(_input, output, **_kwargs):
        output.mkdir(parents=True, exist_ok=True)
        (output / "hg_prediction.hg").write_text(prediction)
        return output / "hg_prediction.hg"

    # Yleaf's analysis is the boundary: what it writes when nothing matched.
    monkeypatch.setattr(analysis, "analyze", no_call)
    vcf = _vcf(tmp_path / "kit.vcf.gz", _HG38, _KIT_BODY)
    with pytest.raises(WGSExtractError, match="could not predict a Y haplogroup"):
        run_wgsextract(
            "lineage", "y-haplogroup", "--input", vcf, "--no-ref-inference",
        )  # fmt: skip


def test_mitochondrial_tree_links_are_opt_in(tmp_path, capsys):
    from wgsextract_cli.commands.lineage import _print_haplogrep_result

    output = tmp_path / "haplogrep.txt"
    output.write_text(_HAPLOGREP + '"kit"\t"1-16569"\t"H1c"\t"1"\t"0.93"\n')
    _print_haplogrep_result(str(output), None)
    assert "https://" not in capsys.readouterr().out
    _print_haplogrep_result(str(output), None, tree_links=True)
    text = capsys.readouterr().out
    assert "https://www.yfull.com/mtree/H1c/" in text
    assert "https://discover.familytreedna.com/mtdna/H1c/tree" in text


def test_y_haplogroup_uses_the_vcf_build(tmp_path, monkeypatch):
    from wgsextract_cli.ydna.yleaf_native import analysis

    seen = []

    def stop(*args, **kwargs):
        seen.append(kwargs["build"])
        raise WGSExtractError("stop")

    monkeypatch.setattr(analysis, "analyze", stop)
    hg19 = {"chr1": 249_250_621, "chrY": 59_373_566}
    body = "chrY\t7000000\t.\tG\tA\t50\tPASS\t.\tGT\t1\n"
    vcf = _vcf(tmp_path / "kit.vcf.gz", hg19, body, "##reference=hg19.fa\n")
    with pytest.raises(WGSExtractError, match="stop"):
        run_wgsextract("lineage", "y-haplogroup", "--input", vcf)
    assert seen == ["hg19"]


@pytest.mark.parametrize(
    ("reference_build", "no_inference", "mismatch"),
    [("hg19", False, True), ("hg38", False, False), ("hg19", True, False)],
)
def test_y_build_override_cannot_infer_against_an_incompatible_fasta(
    tmp_path, monkeypatch, reference_build, no_inference, mismatch
):
    from wgsextract_cli.commands import _lineage_ydna
    from wgsextract_cli.ydna.yleaf_native import analysis

    reference = write_fasta(tmp_path / f"{reference_build}.fa", {"chrY": "A" * 400})
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        {"chrY": 400},
        "chrY\t130\t.\tA\tG\t50\tPASS\t.\tGT\t1\n",
    )
    monkeypatch.setattr(
        _lineage_ydna,
        "ReferenceLibrary",
        lambda *a, **k: SimpleNamespace(fasta=str(reference), build="hg19"),
    )

    def stop(*args, **kwargs):
        assert kwargs["build"] == "hg38"
        assert kwargs["reference"] == reference
        raise WGSExtractError("analysis reached")

    monkeypatch.setattr(analysis, "analyze", stop)
    with pytest.raises(
        WGSExtractError,
        match="selected reference FASTA is hg19" if mismatch else "analysis reached",
    ):
        run_wgsextract(
            "lineage", "y-haplogroup", "--input", vcf, "--build", "hg38",
            *(["--no-ref-inference"] if no_inference else []),
        )  # fmt: skip


@pytest.mark.parametrize(
    ("build", "yleaf"),
    [("hg38", "hg38"), ("hs37d5", "hg19"), ("GRCh37", "hg19"), ("t2t", "t2t")],
)
def test_yleaf_build_names(build, yleaf):
    assert _yleaf_build(build) == yleaf


def test_yleaf_build_defaults_to_hg38_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert _yleaf_build(None) == "hg38"
    assert "not supported by Yleaf" in caplog.text


def test_y_haplogroup_vcf_without_chry_fails(tmp_path, native_tables):
    body = _KIT_BODY.replace("chrY", "chrX")
    vcf = _vcf(tmp_path / "f.vcf.gz", _HG38, body, "##reference=GRCh38.fa\n")
    with pytest.raises(WGSExtractError, match="no records on chromosome Y"):
        run_wgsextract("lineage", "y-haplogroup", "--input", vcf, "--no-ref-inference")
    assert not (tmp_path / "calls.jsonl").exists()


def test_y_haplogroup_reports_no_matching_markers(tmp_path, native_tables):
    vcf = _vcf(tmp_path / "kit.vcf.gz", _HG38, _KIT_BODY, "##reference=GRCh38.fa\n")
    with pytest.raises(WGSExtractError, match="could not predict"):
        run_wgsextract(
            "lineage", "y-haplogroup", "--input", vcf,
            "--no-ref-inference",
        )  # fmt: skip


def test_y_haplogroup_fastq_requires_explicit_alignment(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"chrY": "ACGT" * 100})
    fq = tmp_path / "kit.fastq.gz"
    fq.write_bytes(gzip.compress(b"@r1\nACGT\n+\nIIII\n"))
    argv = ["lineage", "y-haplogroup", "--input", str(fq), "--ref", str(ref)]
    with pytest.raises(WGSExtractError, match="Align FASTQ"):
        run_wgsextract(*argv)


def _reads(
    chrom: str,
    genome: str,
    n: int,
    start: int = 0,
    snp: int | None = None,
    alt: str = "C",
):
    """``n`` 50 bp reads at ``start``, with ``alt`` at 0-based ``snp`` if given."""
    seq = genome[start : start + 50]
    if snp is not None:
        seq = seq[: snp - start] + alt + seq[snp - start + 1 :]
    return [
        f"{chrom}_{i}\t0\t{chrom}\t{start + 1}\t60\t50M\t*\t0\t0\t{seq}\t{'I' * 50}"
        for i in range(n)
    ]


def test_y_haplogroup_from_cram_decodes_with_the_reference(tmp_path, native_tables):
    rng = random.Random(7)
    genome = "".join(rng.choice("AG") for _ in range(400))
    ref = write_fasta(tmp_path / "ref.fa", {"chrX": genome, "chrY": genome})
    header = ["@SQ\tSN:chrX\tLN:400", "@SQ\tSN:chrY\tLN:400"]
    # A derived marker at position 130 (A>G).
    genome = "".join(
        "G" if pos in (100, 110, 120, 130) else "A" for pos in range(1, 401)
    )
    ref = write_fasta(tmp_path / "ref.fa", {"chrX": genome, "chrY": genome})
    bam = sam_to_bam(
        header + _reads("chrY", genome, 20, start=95), tmp_path / "kit.bam"
    )
    run_wgsextract(
        "lineage", "y-haplogroup", "--input", bam, "--ref", str(ref),
    )  # fmt: skip
    bam_prediction = (tmp_path / "wgse" / "yleaf/hg_prediction.hg").read_text()
    assert _y_rows(tmp_path / "wgse", "fmf") == []

    from wgsextract_cli.core.utils import run_command

    cram = str(tmp_path / "kit.cram")
    run_command(["samtools", "view", "-C", "-T", str(ref), "-o", cram, bam])
    run_command(["samtools", "index", cram])
    run_wgsextract(
        "lineage", "y-haplogroup", "--input", cram, "--ref", str(ref),
    )  # fmt: skip
    assert (tmp_path / "wgse" / "yleaf/hg_prediction.hg").read_text() == bam_prediction
    assert _y_rows(tmp_path / "wgse", "fmf") == []


def test_y_haplogroup_from_cram_without_a_reference_fails(
    tmp_path, monkeypatch, native_tables
):
    genome = "".join(random.Random(7).choices("ACGT", k=400))
    encoder = tmp_path / "encoder"
    encoder.mkdir()
    ref = write_fasta(encoder / "ref.fa", {"chrX": genome, "chrY": genome})
    header = ["@SQ\tSN:chrX\tLN:400", "@SQ\tSN:chrY\tLN:400"]
    bam = sam_to_bam(header + _reads("chrY", genome, 20, start=95), encoder / "k.bam")
    cram = str(tmp_path / "kit.cram")

    from wgsextract_cli.core.utils import run_command

    run_command(["samtools", "view", "-C", "-T", str(ref), "-o", cram, bam])
    run_command(["samtools", "index", cram])
    # Nothing can find the encoding reference: not beside the CRAM, not in a
    # library, and not from htslib's MD5 lookup (which defaults to the network).
    for path in encoder.iterdir():
        path.unlink()
    monkeypatch.setenv("REF_PATH", str(tmp_path / "no-ref-cache"))
    monkeypatch.delenv("REF_CACHE", raising=False)

    with pytest.raises(WGSExtractError, match="reference FASTA.*CRAM"):
        run_wgsextract("lineage", "y-haplogroup", "--input", cram)
    assert not list(tmp_path.rglob("hg_prediction.hg"))


def test_y_haplogroup_rejects_unsupported_input(tmp_path):
    sam = tmp_path / "kit.sam"
    sam.write_text("@SQ\tSN:chrY\tLN:10\n")
    with pytest.raises(WGSExtractError, match="Native Yleaf accepts"):
        run_wgsextract(
            "lineage", "y-haplogroup", "--input", str(sam),
        )  # fmt: skip


@pytest.mark.parametrize(
    ("meta", "contigs", "expected"),
    [
        (
            "##reference=chm13v2.0.fa\n",
            {"chr1": 248_387_328, "chrY": 62_460_029},
            "t2t",
        ),
        (
            "##reference=human_g1k_v37.fasta\n",
            {"1": 249_250_621, "Y": 59_373_566},
            "hg19",
        ),
    ],
)
def test_y_haplogroup_detects_build_from_vcf_contig_lengths(
    tmp_path,
    meta,
    contigs,
    expected,
    monkeypatch,
):
    y = "chrY" if "chrY" in contigs else "Y"
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        contigs,
        f"{y}\t100\t.\tA\tG\t50\tPASS\t.\tGT\t1\n",
        meta,
    )
    from wgsextract_cli.ydna.yleaf_native import analysis

    seen = []

    def stop(*args, **kwargs):
        seen.append(kwargs["build"])
        raise WGSExtractError("stop")

    monkeypatch.setattr(analysis, "analyze", stop)
    with pytest.raises(WGSExtractError, match="stop"):
        run_wgsextract("lineage", "y-haplogroup", "--input", vcf)
    assert seen == [expected]


# --- mt-haplogroup -----------------------------------------------------------


@pytest.mark.parametrize("mt", ["chrM", "MT"])
def test_mt_haplogroup_from_vcf_hands_haplogrep_only_mtdna(tmp_path, capsys, mt):
    contigs = {(mt if c == "chrM" else c): n for c, n in _HG38.items()}
    vcf = _vcf(tmp_path / "kit.vcf.gz", contigs, _KIT_BODY.replace("chrM", mt))
    run_wgsextract(
        "lineage", "mt-haplogroup", "--input", vcf, "--outdir", str(tmp_path / "o"),
        "--haplogrep-path", _haplogrep(tmp_path),
        *(["--tree-links"] if mt == "chrM" else []),
    )  # fmt: skip
    (call,) = _calls(tmp_path)
    # contract: the pinned default tree, named as Haplogrep's id@version.
    assert call["argv"][call["argv"].index("--tree") + 1] == "phylotree-fu-rcrs@1.0"
    assert call["records"] == [[mt, "73", "A", "G", "1"], [mt, "263", "A", "G", "1"]]
    assert sorted(p.name for p in (tmp_path / "o").iterdir()) == [
        "haplogrep_results.txt"
    ]
    out = capsys.readouterr().out
    assert "Haplogroup      : H1c" in out and "Quality         : 0.93" in out
    assert ("https://www.yfull.com/mtree/H1c/" in out) is (mt == "chrM")
    assert ("https://discover.familytreedna.com/mtdna/H1c/tree" in out) is (
        mt == "chrM"
    )


def test_mt_haplogroup_vcf_without_mtdna_fails(tmp_path):
    body = "".join(line for line in _KIT_BODY.splitlines(True) if "chrM" not in line)
    vcf = _vcf(tmp_path / "kit.vcf.gz", _HG38, body)
    with pytest.raises(WGSExtractError, match="no variants on chromosome (chrM|MT)"):
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", vcf,
            "--haplogrep-path", _haplogrep(tmp_path),
        )  # fmt: skip
    assert not (tmp_path / "temp_haplogrep_input.vcf.gz").exists()


def test_mt_haplogroup_haplogrep_failure_cleans_up(tmp_path):
    vcf = _vcf(tmp_path / "kit.vcf.gz", _HG38, _KIT_BODY)
    with pytest.raises(WGSExtractError, match="Haplogrep failed"):
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", vcf, "--outdir", str(tmp_path / "o"),
            "--haplogrep-path", _haplogrep(tmp_path, code=2),
        )  # fmt: skip
    assert sorted(p.name for p in (tmp_path / "o").iterdir()) == [
        "haplogrep_results.txt"
    ]


def _mito_bam(
    tmp_path: Path, mt_length: int, read_groups: tuple[str, ...] = ()
) -> tuple[str, Path]:
    """A BAM with 12 reads carrying ALT C at chrM:150 (and none elsewhere)."""
    rng = random.Random(3)
    mito = "".join(rng.choice("AGT") for _ in range(mt_length))
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": mito[:500], "chrM": mito})
    header = ["@SQ\tSN:chr1\tLN:500", f"@SQ\tSN:chrM\tLN:{mt_length}"]
    header += [f"@RG\tID:{sample}\tSM:{sample}" for sample in read_groups]
    reads = _reads("chrM", mito, 12, start=120, snp=149) + _reads("chr1", mito, 5)
    return sam_to_bam(header + reads, tmp_path / "kit.bam"), ref


def test_mt_haplogroup_from_bam_calls_haploid_mtdna_variants(tmp_path):
    bam, ref = _mito_bam(tmp_path, 16_569)
    run_wgsextract(
        "lineage", "mt-haplogroup", "--input", bam, "--ref", str(ref),
        "--haplogrep-path", _haplogrep(tmp_path),
    )  # fmt: skip
    (call,) = _calls(tmp_path)
    assert [(*r[:2], r[3], r[4].split(":")[0]) for r in call["records"]] == [
        ("chrM", "150", "C", "1")
    ]
    assert not (tmp_path / "temp_haplogrep_input.vcf.gz").exists()


def test_mt_haplogroup_refuses_or_warns_on_yoruba_mtdna(tmp_path, caplog):
    bam, ref = _mito_bam(tmp_path, 16_571)
    with caplog.at_level(logging.WARNING):
        try:
            run_wgsextract(
                "lineage", "mt-haplogroup", "--input", bam, "--ref", str(ref),
                "--haplogrep-path", _haplogrep(tmp_path),
            )  # fmt: skip
        except WGSExtractError as e:
            assert "Yoruba" in str(e)
            return
    assert "Yoruba" in caplog.text


def _mito_on(tmp_path: Path, mito: str, m5: str = "") -> tuple[str, Path]:
    """A BAM on a chrM-only reference, its reads carrying ALT C at 1-based 2011."""
    assert mito[2010] != "C"
    ref = write_fasta(tmp_path / "ref.fa", {"chrM": mito})
    sq = f"@SQ\tSN:chrM\tLN:{len(mito)}" + (f"\tM5:{m5}" if m5 else "")
    reads = _reads("chrM", mito, 12, start=1990, snp=2010)
    return sam_to_bam([sq, *reads], tmp_path / "kit.bam"), ref


@pytest.mark.parametrize("chrm", ["rotated", "origin split", "M5"])
def test_mt_haplogroup_refuses_a_chrm_that_is_not_rcrs_positions_827(tmp_path, chrm):
    """#827: CHM13's chrM is the rCRS's length but rotated 576 bp, so it passed
    the length check and Haplogrep classified shifted positions. Since #832
    CHM13's own chrM is mapped onto the rCRS; other rotations are refused."""
    rcrs = pinned_sequences()[0]
    mito = {
        "rotated": rcrs[576:] + rcrs[:576],
        "origin split": rcrs[1:] + rcrs[:1],  # its first 40 bp cross the end
        "M5": rcrs,
    }[chrm]
    # M5: the header says CHM13 even though --ref is (wrongly) an rCRS FASTA.
    bam, ref = _mito_on(tmp_path, mito, m5=CHM13_CHRM_MD5 if chrm == "M5" else "")
    with pytest.raises(WGSExtractError) as error:
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", bam, "--ref", str(ref),
            "--haplogrep-path", _haplogrep(tmp_path),
        )  # fmt: skip
    if chrm == "M5":
        assert "CHM13v2.0's chrM in the alignment's header" in str(error.value)
        assert "--ref" in str(error.value)
    else:
        assert "not the rCRS" in str(error.value) and "realign" in str(error.value)
    assert not (tmp_path / "calls.jsonl").exists()  # Haplogrep never ran


@pytest.mark.parametrize("chrm", ["rCRS", "unrecognised"])
def test_mt_haplogroup_warns_unless_the_chrm_is_confirmed_rcrs_827(tmp_path, chrm):
    rcrs = pinned_sequences()[0]
    mito = rcrs if chrm == "rCRS" else rcrs[::-1].replace("C", "G")
    bam, ref = _mito_on(tmp_path, mito)
    result = _classify(
        bam, tmp_path / "o", ref=ref, haplogrep_path=_haplogrep(tmp_path)
    )
    unconfirmed = "mt_reference_unconfirmed" in {w.code for w in result.warnings}
    assert unconfirmed is (chrm == "unrecognised")
    (call,) = _calls(tmp_path)
    assert [r[:2] + r[3:4] for r in call["records"]] == [["chrM", "2011", "C"]]


@pytest.mark.parametrize(
    "evidence",
    ["REF alleles", "stale rCRS md5", "contig md5", "quoted md5", "plain VCF md5"],
)
def test_mt_haplogroup_refuses_a_vcf_called_against_chm13_827(tmp_path, evidence):
    """#827: a VCF called against CHM13 reads as rCRS positions to Haplogrep."""
    rcrs, chm13 = pinned_sequences()
    sites = [73, 263, 750, 2706, 7028]
    if evidence in ("REF alleles", "stale rCRS md5"):
        md5 = RCRS_MD5 if evidence == "stale rCRS md5" else ""
        meta = f"##contig=<ID=chrM,length=16569,md5={md5}>\n" if md5 else ""
        refs = chm13
    else:
        md5 = f'"{CHM13_CHRM_MD5}"' if evidence == "quoted md5" else CHM13_CHRM_MD5
        meta, refs = f"##contig=<ID=chrM,length=16569,md5={md5}>\n", rcrs
    body = "".join(
        f"chrM\t{p}\t.\t{refs[p - 1]}\t{'T' if refs[p - 1] != 'T' else 'G'}"
        "\t50\tPASS\t.\tGT\t1\n"
        for p in sites
    )
    contigs = {} if meta else {"chrM": 16_569}
    vcf = _vcf(tmp_path / "kit.vcf.gz", contigs, body, meta)
    if evidence == "plain VCF md5":
        plain = tmp_path / "plain.vcf"  # can't be indexed, so its chrM is a guess
        plain.write_bytes(gzip.decompress(Path(vcf).read_bytes()))
        vcf = str(plain)
    # #832: a variants-only VCF can't be mapped onto the rCRS (10208).
    with pytest.raises(WGSExtractError, match="CHM13v2.0's chrM.*variants only"):
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", vcf,
            "--haplogrep-path", _haplogrep(tmp_path),
        )  # fmt: skip
    assert not (tmp_path / "calls.jsonl").exists()


def test_mt_haplogroup_refuses_mtdna_split_over_two_contigs_827(tmp_path):
    """#827: an MT contig's rCRS md5 mustn't vouch for chrM records too."""
    body = (
        "MT\t73\t.\tA\tG\t50\tPASS\t.\tGT\t1\nchrM\t263\t.\tA\tG\t50\tPASS\t.\tGT\t1\n"
    )
    vcf = _vcf(tmp_path / "kit.vcf.gz", {"MT": 16_569, "chrM": 16_569}, body)
    plain = tmp_path / "plain.vcf"
    plain.write_bytes(gzip.decompress(Path(vcf).read_bytes()))
    with pytest.raises(WGSExtractError, match="records on MT, chrM"):
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", str(plain),
            "--haplogrep-path", _haplogrep(tmp_path),
        )  # fmt: skip


@pytest.mark.parametrize("at_10208", ["C", "T"])
def test_mt_haplogroup_maps_a_chm13_chrm_onto_rcrs_positions_832(tmp_path, at_10208):
    """#832: an alignment on CHM13's chrM was refused. It is mapped onto the
    rCRS now: HG00096's 750G sits at CHM13 174, and a sample matching CHM13
    at 10208 has no variant there yet carries 10208C against the rCRS."""
    chm13 = pinned_sequences()[1]
    assert (chm13[173], chm13[9630]) == ("A", "C")
    ref = write_fasta(tmp_path / "ref.fa", {"chrM": chm13})
    reads = _reads("chrM", chm13, 12, start=150, snp=173, alt="G")
    snp = 9630 if at_10208 == "T" else None
    reads += _reads("chrM", chm13, 12, start=9600, snp=snp, alt="T")
    bam = sam_to_bam([f"@SQ\tSN:chrM\tLN:{len(chm13)}", *reads], tmp_path / "k.bam")
    result = _classify(
        bam, tmp_path / "o", ref=ref, haplogrep_path=_haplogrep(tmp_path)
    )
    (call,) = _calls(tmp_path)
    expected = [["chrM", "750", "A", "G"]]
    expected += [["chrM", "10208", "T", "C"]] if at_10208 == "C" else []
    assert [r[:4] for r in call["records"]] == expected
    assert f"md5={RCRS_MD5}" in "".join(call["contigs"])
    assert "rCRS" in result.details["mt_reference"]
    assert not result.warnings


_POLY_C = {  # CHM13 16294-16310 is rCRS 302-316 with a C after 310
    "HG00096": (
        "16294\t.\tACCCCCCC\tACCCCCCCC",
        [("302", "A", "AC"), ("310", "T", "TC")],
    ),
    "CHM13's C": ("", [("310", "T", "TC")]),
    "the rCRS's": ("16302\t.\tTC\tT", []),
    # A shorter allele keeps the longer one's REF padding (GATK, DeepVariant).
    "the rCRS's, padded": ("16302\t.\tTCC\tT,TC", []),
}


@pytest.mark.parametrize("sample", _POLY_C)
def test_mt_haplogroup_renormalises_chm13_indels_against_rcrs_832(tmp_path, sample):
    """#832: indels in the poly-C run by rCRS 310 are restated against the
    rCRS, left-aligned as bcftools calls them there; CHM13's extra C counts
    only when the sample has it."""
    indel, expected = _POLY_C[sample]
    chm13 = pinned_sequences()[1]
    rows = [
        (q, f"chrM\t{q}\t.\t{chm13[q - 1]}\t.\t50\tPASS\t.\tGT\t0\n")
        for q in range(16280, 16321)
    ]
    if indel:  # after the reference call at its position
        gt = len(indel.split()[3].split(","))  # the last ALT
        rows.append((int(indel.split()[0]), f"chrM\t{indel}\t50\tPASS\t.\tGT\t{gt}\n"))
    body = "".join(row for _q, row in sorted(rows, key=lambda r: r[0]))
    meta = f"##contig=<ID=chrM,length=16569,md5={CHM13_CHRM_MD5}>\n"
    vcf = _vcf(tmp_path / "kit.vcf.gz", {}, body, meta)
    _classify(vcf, tmp_path / "o", haplogrep_path=_haplogrep(tmp_path))
    (call,) = _calls(tmp_path)
    assert [tuple(r[1:4]) for r in call["records"]] == expected


def test_mt_haplogroup_contamination_needs_the_alignment_on_chm13_832(tmp_path):
    chm13 = pinned_sequences()[1]
    meta = f"##contig=<ID=chrM,length=16569,md5={CHM13_CHRM_MD5}>\n"
    body = f"chrM\t174\t.\t{chm13[173]}\t.\t50\tPASS\t.\tGT\t0\n"
    vcf = _vcf(tmp_path / "kit.vcf.gz", {}, body, meta)
    with pytest.raises(WGSExtractError, match="--contamination.*BAM or CRAM"):
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", vcf, "--contamination",
            "--haplogrep-path", _haplogrep(tmp_path),
            "--haplocheck-path", _haplogrep(tmp_path),
        )  # fmt: skip
    assert not (tmp_path / "calls.jsonl").exists()


_CHM13_META = (
    f"##contig=<ID=chrM,length=16569,md5={CHM13_CHRM_MD5}>\n"
    '##INFO=<ID=END,Number=1,Type=Integer,Description="end">\n'
)


@pytest.mark.parametrize(
    ("site", "af", "expected"),
    [
        (9631, "", None),
        (9631, "0.5", None),
        # Mutect2 writes 0/1 even for a homoplasmy; Haplogrep's level decides.
        (9631, "0.99", []),
        (9631, "0.05", [["10208", "T", "C"]]),
        (174, "", [["750", "A", "G"]]),
    ],
)
def test_mt_haplogroup_refuses_a_mixed_call_at_rcrs_10208_on_chm13_832(
    tmp_path, site, af, expected
):
    """PR #927 review: a CHM13 0/1 9631 C>T read as T, so the sample's C
    vanished at rCRS 10208. A mixture there is refused unless its level
    (FORMAT/AF) puts one allele above Haplogrep's 0.9; one elsewhere still
    reads as its ALT, as on an rCRS VCF."""
    chm13 = pinned_sequences()[1]
    alt = "T" if site == 9631 else "G"
    fmt, level = ("GT:AF", f":{af}") if af else ("GT", "")
    body = (
        f"chrM\t{site - 1}\t.\t{chm13[site - 2]}\t.\t50\tPASS\t.\tGT\t0/0\n"
        f"chrM\t{site}\t.\t{chm13[site - 1]}\t{alt}\t50\tPASS\t.\t{fmt}\t0/1{level}\n"
    )
    meta = _CHM13_META + '##FORMAT=<ID=AF,Number=A,Type=Float,Description="l">\n'
    vcf = _vcf(tmp_path / "kit.vcf.gz", {}, body, meta)
    if expected is None:
        with pytest.raises(CommandFailed, match=r"mixed call.*rCRS 10208"):
            _classify(vcf, tmp_path / "o", haplogrep_path=_haplogrep(tmp_path))
        assert not (tmp_path / "calls.jsonl").exists()
    else:
        _classify(vcf, tmp_path / "o", haplogrep_path=_haplogrep(tmp_path))
        (call,) = _calls(tmp_path)
        assert [r[1:4] for r in call["records"]] == expected


@pytest.mark.parametrize("alt", ["CA", "C"])
def test_mt_haplogroup_keeps_the_anchor_of_a_chm13_gvcf_record_832(tmp_path, alt):
    """PR #927 review: a gVCF's 9631 CT>CA (or CT>C) keeps CHM13's C at
    9631, but with no reference block over it that C was dropped, and with it
    rCRS 10208C."""
    chm13 = pinned_sequences()[1]
    assert chm13[9630:9632] == "CT"
    body = (
        f"chrM\t1\t.\t{chm13[0]}\t<NON_REF>\t.\tPASS\tEND=100\tGT\t0/0\n"
        f"chrM\t9631\t.\tCT\t{alt},<NON_REF>\t50\tPASS\t.\tGT\t1/1\n"
    )
    vcf = _vcf(tmp_path / "kit.vcf.gz", {}, body, _CHM13_META)
    _classify(vcf, tmp_path / "o", haplogrep_path=_haplogrep(tmp_path))
    (call,) = _calls(tmp_path)
    assert ["10208", "T", "C"] in [r[1:4] for r in call["records"]]
    if alt == "CA":
        assert ["10209", "T", "A"] in [r[1:4] for r in call["records"]]


def _chm13_vcf(path: Path, samples: list[str], refcall: str) -> str:
    """A CHM13 chrM VCF of ``samples`` with 750G (at CHM13 174) and a 9631
    C>T row each sample calls ``refcall``; sites only without samples."""
    chm13 = pinned_sequences()[1]
    rows = [(174, chm13[173], "G", "1"), (9631, "C", "T", refcall)]
    header = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"
    if samples:
        header += "\tFORMAT\t" + "\t".join(samples)
    body = "".join(
        f"chrM\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t."
        + ("\tGT" + f"\t{gt}" * len(samples) if samples else "")
        + "\n"
        for pos, ref, alt, gt in rows
    )
    plain = path.with_suffix("")
    plain.write_text(
        "##fileformat=VCFv4.2\n" + _CHM13_META
        + '##FORMAT=<ID=GT,Number=1,Type=String,Description="g">\n'
        + header + "\n" + body
    )  # fmt: skip
    return bgzip_index(plain, path)


@pytest.mark.parametrize(
    ("shape", "error"),
    [
        ("RefCall", "variants only"),
        ("two samples", "single-sample VCF.*has 2 samples"),
        ("sites only", "single-sample VCF.*has no samples"),
    ],
)
def test_mt_haplogroup_needs_reference_only_records_on_chm13_832(
    tmp_path, shape, error
):
    """PR #927 review: a 0/0 on a real ALT (a DeepVariant RefCall, a cohort
    subset) passed for all-sites calls and dropped 10208C; multi-sample and
    sites-only VCFs crashed unpacking genotypes."""
    samples = {"RefCall": ["kit"], "two samples": ["kit", "mum"], "sites only": []}
    vcf = _chm13_vcf(tmp_path / "kit.vcf.gz", samples[shape], "0/0")
    with pytest.raises(CommandFailed, match=error):
        _classify(vcf, tmp_path / "o", haplogrep_path=_haplogrep(tmp_path))
    assert not (tmp_path / "calls.jsonl").exists()


def test_mt_haplogroup_refuses_an_rcrs_header_on_a_chm13_reference_832(tmp_path):
    """PR #927 review: an alignment whose header gives the rCRS's M5, run with
    a CHM13 --ref, was called against CHM13 and mapped as if aligned to it."""
    chm13 = pinned_sequences()[1]
    bam, ref = _mito_on(tmp_path, chm13, m5=RCRS_MD5)
    with pytest.raises(WGSExtractError) as error:
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", bam, "--ref", str(ref),
            "--haplogrep-path", _haplogrep(tmp_path),
        )  # fmt: skip
    assert "rCRS in the alignment's header" in str(error.value)
    assert "CHM13v2.0's chrM" in str(error.value) and "--ref" in str(error.value)
    assert not (tmp_path / "calls.jsonl").exists()


@pytest.mark.parametrize(
    ("ref", "md5"), [("A", ""), ("A", "0" * 32), ("C", ""), ("A", RCRS_MD5)]
)
def test_mt_haplogroup_vcf_warns_unless_its_chrm_is_confirmed_rcrs_827(
    tmp_path, ref, md5
):
    """#827: rCRS and CHM13 share the A at 73, so only an rCRS md5 confirms
    it; an unknown md5 or a REF neither has (C) proves nothing either."""
    meta = f"##contig=<ID=chrM,length=16569,md5={md5}>\n" if md5 else ""
    body = f"chrM\t73\t.\t{ref}\tG\t50\tPASS\t.\tGT\t1\n"
    vcf = _vcf(tmp_path / "kit.vcf.gz", {} if md5 else {"chrM": 16_569}, body, meta)
    result = _classify(vcf, tmp_path / "o", haplogrep_path=_haplogrep(tmp_path))
    warned = "mt_reference_unconfirmed" in {w.code for w in result.warnings}
    assert warned is (md5 != RCRS_MD5)


@pytest.mark.parametrize(
    "result",
    [
        _HAPLOGREP,
        '"SampleID"\t"Haplogroup"\n"kit"\t""\n',
    ],
)
def test_mt_haplogroup_without_a_haplogroup_names_the_reference(
    tmp_path, monkeypatch, result
):
    # Haplogrep writes a header alone when it gets no mtDNA variants, as when
    # the alignment was called against a reference of another build.
    # Without --ref, as the GUI's ancestry page calls it with a library set up
    # that doesn't hold this sample's reference: the FASTA beside the BAM is used.
    from wgsextract_cli.core.config import settings

    (tmp_path / "library").mkdir()
    monkeypatch.setitem(settings, "reference_library", str(tmp_path / "library"))
    bam, _ref = _mito_bam(tmp_path, 16_569)
    with pytest.raises(WGSExtractError, match=r"no mtDNA variants.*ref\.fa"):
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", bam,
            "--haplogrep-path", _haplogrep(tmp_path, result),
        )  # fmt: skip
    (call,) = _calls(tmp_path)
    assert [r[:2] for r in call["records"]] == [["chrM", "150"]]


def test_mt_haplogroup_calling_failure_stops_before_haplogrep(tmp_path):
    bam, ref = _mito_bam(tmp_path, 16_569)
    Path(f"{bam}.bai").unlink()  # mpileup -r needs the index
    with pytest.raises(WGSExtractError, match="mpileup"):
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", bam, "--ref", str(ref),
            "--haplogrep-path", _haplogrep(tmp_path),
        )  # fmt: skip
    assert not (tmp_path / "calls.jsonl").exists()


# --- Haplogrep 3 and haplocheck (#361) ---------------------------------------

needs_haplogrep3 = needs_tools("haplogrep3", "java")


def _mt_vcf(path: Path, body: str, af: bool = False) -> str:
    meta = '##FORMAT=<ID=AF,Number=1,Type=Float,Description="level">\n' if af else ""
    return _vcf(path, {"chrM": 16_569}, body, meta)


def _classify(vcf: str, outdir: Path, **params):
    from wgsextract_cli import api

    return api.lineage.mt_haplogroup(
        api.MtHaplogroupParams(input=Path(vcf), outdir=outdir, **params)
    )


def test_1052_mt_tree_needs_download_consent_then_runs_offline(
    tmp_path, mini_tree, blocked_network, monkeypatch
):
    from wgsextract_cli.core.config import settings

    monkeypatch.setitem(settings, "allow_reference_downloads", False)
    vcf = _mt_vcf(tmp_path / "fake.vcf.gz", mini_tree.vcf_body([100, 200, 300]))
    options = {"haplogrep_path": _haplogrep(tmp_path)}
    with pytest.raises(CommandFailed) as refused:
        _classify(vcf, tmp_path / "out", **options)
    assert refused.value.exit_code == 2
    assert "Haplogrep tree" in str(refused.value)
    assert "--download" in str(refused.value)
    assert not mini_tree.downloads
    assert _classify(vcf, tmp_path / "out", download=True, **options).exit_code == 0
    assert _classify(vcf, tmp_path / "out", **options).exit_code == 0
    assert len(mini_tree.downloads) == 1
    assert not blocked_network


@needs_haplogrep3
def test_haplogrep3_classifies_with_the_chosen_tree(tmp_path, mini_tree):
    vcf = _mt_vcf(tmp_path / "kit.vcf.gz", mini_tree.vcf_body([100, 200, 300]))
    for tree in (None, "mitotree-rcrs"):
        result = _classify(vcf, tmp_path / str(tree), tree=tree)
        assert result.metrics["haplogroup"] == "A1"
        assert result.details["tree"]["id"] == (tree or "phylotree-fu-rcrs")
        assert result.details["haplogrep_version"] == "3.2.2"
    _classify(vcf, tmp_path / "again")
    assert len(mini_tree.downloads) == 2  # each tree once, then reused


def test_mt_haplogroup_refuses_haplogrep_2(tmp_path):
    """#361: Haplogrep 2 takes other arguments and an older tree."""
    old = _fake_tool(tmp_path / "haplogrep.py", 'print("HaploGrep 2.4.0")\n')
    vcf = _vcf(tmp_path / "kit.vcf.gz", _HG38, _KIT_BODY)
    with pytest.raises(WGSExtractError, match="not Haplogrep 3"):
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", vcf, "--haplogrep-path", old,
        )  # fmt: skip


def test_a_modified_tree_is_downloaded_again(tmp_path, mini_tree):
    vcf = _vcf(tmp_path / "kit.vcf.gz", _HG38, _KIT_BODY)
    library = tmp_path / "library"
    library.mkdir()
    argv = ["lineage", "mt-haplogroup", "--input", vcf, "--ref", str(library)]
    run_wgsextract(*argv, "--haplogrep-path", _haplogrep(tmp_path))
    tree_xml = next(library.glob("haplogrep3/trees/phylotree-fu-rcrs/*/tree.xml"))
    original = tree_xml.read_bytes()
    tree_xml.write_text("<phylotree/>")
    run_wgsextract(*argv, "--haplogrep-path", _haplogrep(tmp_path))
    assert tree_xml.read_bytes() == original
    assert len(mini_tree.downloads) == 2


@needs_haplogrep3
@needs_tools("haplocheck")
def test_contamination_check_reports_haplocheck_status(tmp_path, mini_tree):
    body = "".join(
        line.replace("\tGT\t1", "\tGT:AF\t1:1.0")
        for line in mini_tree.vcf_body([100, 200, 300]).splitlines(True)
    )
    vcf = _mt_vcf(tmp_path / "kit.vcf.gz", body, af=True)
    result = _classify(vcf, tmp_path / "o", contamination=True)
    assert result.metrics["mt_contamination"] == "ND"
    roles = {Path(o.path).name: o.role for o in result.outputs}
    expected = {
        "haplogrep_results.txt": "haplogroup_report",
        "haplocheck_contamination.txt": "contamination_report",
        "haplocheck_contamination.raw.txt": "contamination_details",
    }
    warned = {w.code for w in result.warnings}
    # haplocheck 1.3.3 can't load its HTML template on Windows (PR #436 CI).
    if sys.platform == "win32":
        assert "haplocheck_no_html" in warned
    else:
        expected["haplocheck_contamination.html"] = "contamination_html"
        assert "haplocheck_no_html" not in warned
    assert roles == expected
    written = sorted(p.name for p in (tmp_path / "o").iterdir())
    assert written == sorted([*roles, "haplogrep_results.txt.manifest.json"])


@needs_haplogrep3
@needs_tools("haplocheck")
def test_contamination_check_detects_a_mixture(tmp_path):
    # Issue #361: mutserve's calls on NA12878 chrM reads with 5% of HG01112's.
    body = (Path(__file__).parent / "fixtures" / "mt_mixture_5pct.tsv").read_text()
    vcf = _mt_vcf(tmp_path / "mix.vcf.gz", body, af=True)
    result = _classify(vcf, tmp_path / "o", contamination=True)
    assert result.metrics["mt_contamination"] == "YES"
    assert result.metrics["mt_contamination_level"] == pytest.approx(0.05, abs=0.01)
    (warning,) = (w for w in result.warnings if w.code == "mt_contamination")
    assert warning.details["major_haplogroup"].startswith("H13")
    assert warning.details["minor_haplogroup"].startswith("A2")


def test_contamination_check_refuses_reads_of_several_samples(tmp_path):
    two, ref = _mito_bam(tmp_path, 16_569, read_groups=("mother", "child"))
    with pytest.raises(WGSExtractError, match="single-sample.*child, mother"):
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", two, "--ref", str(ref),
            "--contamination", "--haplogrep-path", _haplogrep(tmp_path),
            "--haplocheck-path", _haplogrep(tmp_path),
        )  # fmt: skip


def test_relative_tool_paths_survive_the_temporary_working_directory(
    tmp_path, monkeypatch
):
    vcf = _vcf(tmp_path / "kit.vcf.gz", _HG38, _KIT_BODY)
    _haplogrep(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = _classify(vcf, tmp_path, haplogrep_path="haplogrep.py")
    assert result.metrics["haplogroup"] == "H1c"
    # The override that ran is the version recorded, not one found by name.
    assert result.provenance.tools["haplogrep3"] == "3.2.2"


def test_contamination_check_needs_heteroplasmy_levels(tmp_path):
    vcf = _vcf(tmp_path / "kit.vcf.gz", _HG38, _KIT_BODY)
    with pytest.raises(WGSExtractError, match="heteroplasmy levels"):
        run_wgsextract(
            "lineage", "mt-haplogroup", "--input", vcf, "--contamination",
            "--haplogrep-path", _haplogrep(tmp_path),
            "--haplocheck-path", _haplogrep(tmp_path),
        )  # fmt: skip


@needs_haplogrep3
@needs_tools("haplocheck")
def test_contamination_check_on_a_bam_calls_heteroplasmies(tmp_path, mini_tree):
    bam, ref = _mito_bam(tmp_path, 16_569)
    from wgsextract_cli import api

    result = api.lineage.mt_haplogroup(
        api.MtHaplogroupParams(
            input=Path(bam), ref=ref, outdir=tmp_path / "o", contamination=True
        )
    )
    # Too few variants to judge; mutserve called the 12-read homoplasmy at 150.
    assert result.metrics["mt_contamination"] == "ND"
    (row,) = csv.DictReader(
        (tmp_path / "o" / "haplocheck_contamination.raw.txt").open(), delimiter="\t"
    )
    assert row["Sample Coverage"] == "12" and row["Overall Homoplasmies"] == "1"


@needs_haplogrep3
@needs_tools("haplocheck")
def test_contamination_check_maps_chm13_heteroplasmies_onto_rcrs_832(
    tmp_path, mini_tree
):
    """#832: haplocheck reads rCRS positions too, so mutserve's CHM13 calls are
    mapped, and 10208C, where the sample matches CHM13, is added."""
    chm13 = pinned_sequences()[1]
    ref = write_fasta(tmp_path / "ref.fa", {"chrM": chm13})
    reads = _reads("chrM", chm13, 12, start=150, snp=173, alt="G")
    reads += _reads("chrM", chm13, 12, start=9600)
    bam = sam_to_bam([f"@SQ\tSN:chrM\tLN:{len(chm13)}", *reads], tmp_path / "k.bam")
    result = _classify(bam, tmp_path / "o", ref=ref, contamination=True)
    assert result.metrics["mt_contamination"] == "ND"
    (row,) = csv.DictReader(
        (tmp_path / "o" / "haplocheck_contamination.raw.txt").open(), delimiter="\t"
    )
    assert row["Overall Homoplasmies"] == "2"  # 750G and 10208C
    # #1175: the kept mutserve evidence stays in CHM13 positions, and says so.
    kept = result.details["heteroplasmy"]["mt_reference"]
    assert (kept["origin"], kept["coordinates"]) == (CHM13_CHRM, "CHM13v2.0 chrM")


@needs_haplogrep3
@needs_tools("haplocheck")
def test_contamination_check_refuses_a_third_allele_mixture_at_10208_832(
    tmp_path, mini_tree
):
    """PR #927 review: mutserve's CHM13 9631 C>A at 10% became rCRS 10208
    T>A at 10%, so haplocheck read 90% T where the sample has 90% C."""
    chm13 = pinned_sequences()[1]
    ref = write_fasta(tmp_path / "ref.fa", {"chrM": chm13})
    reads = _reads("chrM", chm13, 90, start=9600)
    mixed = _reads("chrM", chm13, 10, start=9600, snp=9630, alt="A")
    reads += [read.replace("chrM_", "mixed_", 1) for read in mixed]
    # mutserve calls a heteroplasmy only when both strands carry it.
    reads = [
        r.replace("\t0\t", "\t16\t", 1) if i % 2 else r for i, r in enumerate(reads)
    ]
    bam = sam_to_bam([f"@SQ\tSN:chrM\tLN:{len(chm13)}", *reads], tmp_path / "k.bam")
    with pytest.raises(CommandFailed, match=r"mixture of C and A.*rCRS has T"):
        _classify(bam, tmp_path / "o", ref=ref, contamination=True)
    assert not (tmp_path / "o" / "haplocheck_contamination.raw.txt").exists()
