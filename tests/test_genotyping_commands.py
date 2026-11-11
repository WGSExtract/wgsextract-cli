"""`typing hla|cyp2d6|str` end to end: real samtools on tiny GRCh38-headed BAMs,
with T1K, Cyrius and ExpansionHunter replaced by scripts that log their command
line and write output in each tool's format."""

import json
import logging
import random
import sys
import textwrap
from argparse import Namespace
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, run_wgsextract, sam_to_bam, write_fasta
from wgsextract_cli import api
from wgsextract_cli.commands.genotyping import common, hla, repeats
from wgsextract_cli.commands.info.metrics import SEX_INFERENCE_VERSION
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.personal_report import build_report
from wgsextract_cli.core.pixi_tools import _join_command, pixi_env_prefix
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.core.variant_files import calculate_bam_md5
from wgsextract_cli.main import main

pytestmark = needs_tools("samtools")

HG38 = {
    "chr1": 248_956_422,
    "chr6": 170_805_979,
    "chr22": 50_818_468,
    "chrX": 156_040_895,
    "chrY": 57_227_415,
}
_RNG = random.Random(7)
SEQ = "".join(_RNG.choice("ACGT") for _ in range(100))

# Prepended to every fake tool: logs argv, and opt() reads an option's value.
_PRELUDE = """\
import json, pathlib, subprocess, sys
with open({log!r}, "a") as _f:
    _f.write(json.dumps([{tool!r}, *sys.argv[1:]]) + "\\n")
def opt(name):
    return sys.argv[sys.argv.index(name) + 1]
"""


def fake_tools(monkeypatch, tmp_path, scripts):
    """Run each tool of ``scripts`` (name -> Python source) in place of its
    Pixi environment's; returns a function listing the logged command lines."""
    log = tmp_path / "tool_calls.jsonl"
    commands = {}
    for tool, body in scripts.items():
        script = tmp_path / "fake_bin" / f"{tool}.py"
        script.parent.mkdir(exist_ok=True)
        script.write_text(
            _PRELUDE.format(log=str(log), tool=tool) + textwrap.dedent(body)
        )
        commands[tool] = _join_command([sys.executable, str(script)])
    monkeypatch.setattr(common, "pixi_env_tool", commands.get)

    def calls(tool=None):
        if not log.exists():
            return []
        logged = [json.loads(line) for line in log.read_text().splitlines()]
        return [c[1:] for c in logged if tool in (None, c[0])]

    return calls


def pair(
    name, chrom, pos, mate_chrom=None, mate_pos=None, mapq=60, seq=SEQ, mate_seq=None
):
    """SAM lines of an FR pair (1-based positions), mates on any contigs."""
    mate_chrom = mate_chrom or chrom
    mate_pos = mate_pos or pos + 200
    mate_seq = mate_seq or seq
    same = mate_chrom == chrom
    tlen = mate_pos + 100 - pos if same else 0
    rnext1, rnext2 = ("=", "=") if same else (mate_chrom, chrom)
    return [
        f"{name}\t99\t{chrom}\t{pos}\t{mapq}\t{len(seq)}M\t{rnext1}\t{mate_pos}\t"
        f"{tlen}\t{seq}\t{'I' * len(seq)}",
        f"{name}\t147\t{mate_chrom}\t{mate_pos}\t{mapq}\t{len(mate_seq)}M\t{rnext2}\t"
        f"{pos}\t{-tlen}\t{mate_seq}\t{'I' * len(mate_seq)}",
    ]


def bam(path: Path, reads, contigs=HG38) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["@HD\tVN:1.6\tSO:unsorted"]
    header += [f"@SQ\tSN:{name}\tLN:{length}" for name, length in contigs.items()]
    return sam_to_bam(header + list(reads), path)


def outputs(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir()}


# --- common -------------------------------------------------------------------


def _open(path, **overrides):
    values = {"input": path, "threads": 1, **overrides}
    return common.open_alignment(api.TypingCyp2d6Params(**values))


def test_open_alignment_reads_build_contigs_and_stem(tmp_path):
    contigs = {**HG38, "chr22_KI270879v1_alt": 304_135}
    path = bam(tmp_path / "in" / "Sample.bam", pair("p", "chr1", 1000), contigs)
    aln = _open(path, threads=3)
    assert (aln.build, aln.chr_prefix, aln.stem, aln.threads) == (
        "hg38",
        True,
        "Sample",
        "3",
    )
    assert aln.outdir == str(tmp_path / "in")
    assert aln.reference is None and aln.ref_opt == []
    assert aln.contig("22") == "chr22"
    assert aln.alt_contigs("22") == ["chr22_KI270879v1_alt"]


def test_open_alignment_accepts_grch37_numeric_names(tmp_path):
    contigs = {"1": 249_250_621, "6": 171_115_067, "X": 155_270_560}
    aln = _open(bam(tmp_path / "s.bam", pair("p", "1", 1000), contigs))
    assert (aln.build, aln.chr_prefix, aln.contig("6")) == ("hg19", False, "6")


def test_open_alignment_creates_the_outdir_and_keeps_an_existing_ref(tmp_path):
    path = bam(tmp_path / "s.bam", pair("p", "chr1", 1000))
    ref = tmp_path / "ref.fa"
    ref.write_text(">chr1\nACGT\n")
    aln = _open(path, ref=str(ref), outdir=str(tmp_path / "new" / "out"))
    assert Path(aln.outdir).is_dir()
    assert aln.ref_opt == ["-T", str(ref)]


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("missing", "s.bam"),
        ("vcf", "BAM or CRAM"),
        ("unindexed", "needs an index"),
        ("t2t", "detected: t2t"),
        ("cram", "--ref is required"),
    ],
)
def test_open_alignment_rejects_unusable_inputs(tmp_path, setup, message):
    path = tmp_path / "s.bam"
    if setup == "vcf":
        path = tmp_path / "s.vcf"
        path.write_text("##fileformat=VCFv4.2\n")
    elif setup == "unindexed":
        bam(path, pair("p", "chr1", 1000))
        Path(f"{path}.bai").unlink()
    elif setup == "t2t":
        bam(path, pair("p", "chr1", 1000), {"chr1": 248_387_328})
    elif setup == "cram":
        # An indexed CRAM header is enough: the reference check comes first.
        bam(tmp_path / "s.bam", pair("p", "chr1", 1000))
        path = tmp_path / "s.cram"
        Path(tmp_path / "s.bam").rename(path)
        Path(f"{tmp_path / 's.bam'}.bai").rename(f"{path}.crai")
    with pytest.raises(WGSExtractError, match=message):
        _open(path)


def test_a_missing_ref_is_reported_as_missing(tmp_path):
    path = bam(tmp_path / "s.bam", pair("p", "chr1", 1000))
    with pytest.raises(WGSExtractError, match="typo.fa"):
        _open(path, ref=str(tmp_path / "typo.fa")).require_reference()


def test_env_tool_names_the_pixi_environment_to_install(monkeypatch):
    monkeypatch.setattr(common, "pixi_env_tool", lambda tool: None)
    with pytest.raises(WGSExtractError, match=r"pixi install -e t1k \(note\)"):
        common.env_tool("run-t1k", "t1k", " (note)")
    command = _join_command(["/a b/pixi", "run", "x"])
    monkeypatch.setattr(common, "pixi_env_tool", lambda tool: command)
    assert common.env_tool("x", "e") == ["/a b/pixi", "run", "x"]


def test_outside_calls_are_utf8_and_an_empty_call_removes_a_stale_file(tmp_path):
    path = tmp_path / "s.cyp2d6.pharmcat.tsv"
    assert common.write_outside_calls(str(path), [("CYP2D6", "*1/*2x≥3")])
    assert path.read_bytes() == "CYP2D6\t*1/*2x≥3\n".encode()
    assert common.write_outside_calls(str(path), []) is None
    assert not path.exists()


# --- typing hla ---------------------------------------------------------------

# T1K's genotype TSV: gene, number of alleles, then allele (equally likely
# names comma-separated), abundance and quality for each, then secondary alleles.
T1K_GENOTYPE = """\
HLA-A\t2\tHLA-A*02:01:01:01\t95.86\t60\tHLA-A*24:02:01:01\t80.12\t45\t.
HLA-B\t2\tHLA-B*57:01:01:01\t60.10\t33\tHLA-B*08:01:01:01,HLA-B*08:01:01:02\t50.71\t28\t.
HLA-C\t2\tHLA-C*06:02:01:01\t71.03\t41\tHLA-C*07:01:01:01\t69.88\t40\t.
HLA-DRB1\t1\tHLA-DRB1*15:01:01:01\t120.45\t60\t.\t0\t-1\t.
HLA-DQB1\t2\tHLA-DQB1*06:02:01:01\t40.22\t12\tHLA-DQB1*02:01:01,HLA-DQB1*03:02:01\t11.02\t1\t.
HLA-DPB1\t0\t.\t0\t-1\t.\t0\t-1\t.
"""

FAKE_T1K = """\
od, prefix = opt("--od"), opt("-o")
with open(opt("-1")) as r1, open(opt("-2")) as r2:
    names = [line[1:].strip() for line in r1.readlines()[::4]]
    mates = [line[1:].strip() for line in r2.readlines()[::4]]
pathlib.Path({reads!r}).write_text(json.dumps([names, mates]))
genotype = pathlib.Path({genotype!r})
if not genotype.exists():
    sys.exit("T1K: index has no HLA genes\\n" + "x" * 600)
out = pathlib.Path(od, prefix)
pathlib.Path(f"{{out}}_genotype.tsv").write_text(genotype.read_text())
pathlib.Path(f"{{out}}_allele.tsv").write_text("HLA-A*02:01:01:01\\t95.86\\t60\\n")
pathlib.Path(f"{{out}}_allele.vcf").write_text("##fileformat=VCFv4.2\\n")
pathlib.Path(f"{{out}}_aligned_1.fa").write_text(">r\\nACGT\\n")
"""


@pytest.fixture
def mhc_bam(tmp_path):
    """Reads in and around the MHC, on HLA decoys and on a chr6 ALT."""
    contigs = {**HG38, "chr6_GL000250v2_alt": 4_672_374, "HLA-A*01:01:01:01": 3_503}
    secondary = (
        f"out\t355\tchr6\t29000500\t0\t100M\t=\t29000700\t300\t{SEQ}\t{'I' * 100}"
    )
    reads = [
        *pair("mhc", "chr6", 29_000_000),
        *pair("far_mate", "chr6", 30_000_000, "chr1", 1_000_000),
        *pair("decoy", "HLA-A*01:01:01:01", 500),
        *pair("alt", "chr6_GL000250v2_alt", 2_000_000),
        *pair("edge", "chr6", 33_480_500),  # last 77 bp of the MHC
        # The MHC starts at 28,510,120: one mate ends on it, one just before.
        *pair("straddle", "chr6", 28_509_900, mate_pos=28_510_021),
        *pair("before", "chr6", 28_509_900, mate_pos=28_510_020),
        *pair("out", "chr6", 10_000_000),
        secondary,  # a secondary alignment of `out` inside the MHC
        *pair("x", "chrX", 5_000_000),
    ]
    return bam(tmp_path / "in" / "NA12878.bam", reads, contigs)


@pytest.fixture
def hla_setup(tmp_path, monkeypatch):
    index_dir = tmp_path / "t1k_hla"
    index_dir.mkdir()
    index = index_dir / "hla_dna_seq.fa"
    index.write_text(">HLA-A*01:01:01:01\nACGT\n")
    (index_dir / "release.txt").write_text("IPD-IMGT/HLA 3.56.0\n")
    genotype = tmp_path / "t1k_genotype_template.tsv"
    genotype.write_text(T1K_GENOTYPE)
    reads = tmp_path / "t1k_reads.json"
    calls = fake_tools(
        monkeypatch,
        tmp_path,
        {"run-t1k": FAKE_T1K.format(reads=str(reads), genotype=str(genotype))},
    )
    return Namespace(index=index, genotype=genotype, reads=reads, calls=calls)


def test_hla_extracts_mhc_pairs_and_writes_pharmcat_calls(
    tmp_path, mhc_bam, hla_setup, caplog
):
    caplog.set_level(logging.INFO)
    out = tmp_path / "out"
    result = api.genotyping.hla(
        api.TypingHlaParams(
            input=Path(mhc_bam), hla_index=hla_setup.index, outdir=out, threads=2
        )
    )

    names, mates = json.loads(hla_setup.reads.read_text())
    # Mates outside the regions come along; secondary-only and outside reads don't.
    assert sorted(names) == ["alt", "decoy", "edge", "far_mate", "mhc", "straddle"]
    assert names == mates

    [command] = hla_setup.calls("run-t1k")
    assert command[command.index("-f") + 1] == str(hla_setup.index)
    assert command[command.index("--preset") + 1] == "hla-wgs"
    assert command[command.index("-t") + 1] == "2"

    assert outputs(out) == {
        "NA12878.t1k_genotype.tsv",
        "NA12878.t1k_allele.tsv",
        "NA12878.t1k_allele.vcf",
        "NA12878.hla.pharmcat.tsv",
        "NA12878.t1k_genotype.tsv.manifest.json",
    }  # T1K's other files go with the work directory
    assert (out / "NA12878.t1k_genotype.tsv").read_text() == T1K_GENOTYPE
    assert (out / "NA12878.hla.pharmcat.tsv").read_text() == (
        "HLA-A\t*02:01/*24:02\nHLA-B\t*57:01/*08:01\n"
    )
    log = caplog.text
    assert "HLA alleles (IPD-IMGT/HLA 3.56.0):" in log
    assert "HLA-C     *06:02:01:01/*07:01:01:01" in log
    assert "HLA-DRB1  *15:01:01:01/*15:01:01:01" in log
    assert "HLA-DQB1  no confident call" in log
    assert "HLA-DPB1  no confident call" in log
    assert "HLA-DQA1  no confident call" in log  # not in the TSV at all
    assert result.metrics["HLA-B"] == "*57:01/*08:01"
    assert result.metrics["ipd_imgt_hla_release"] == "IPD-IMGT/HLA 3.56.0"
    assert {o.role for o in result.outputs} >= {
        "hla_genotypes",
        "pharmcat_outside_call",
    }


def test_hla_release_is_not_read_through_a_symlink_out_of_the_read_scope(
    tmp_path, mhc_bam, hla_setup
):
    # PR #492 review: the index's release.txt was read without the server's
    # read scope, so a symlink returned an outside file as a metric.
    from wgsextract_cli.core.read_scope import limited_to

    secret = tmp_path / "secret.txt"
    secret.write_text("outside\n")
    release = hla_setup.index.parent / "release.txt"
    release.unlink()
    try:
        release.symlink_to(secret)
    except OSError:
        pytest.skip("can't create symlinks here")
    params = api.TypingHlaParams(
        input=Path(mhc_bam), hla_index=hla_setup.index, outdir=tmp_path / "out"
    )
    with limited_to(lambda path: Path(path).resolve() != secret):
        result = api.genotyping.hla(params)
    assert result.metrics.get("ipd_imgt_hla_release") != "outside"


@pytest.mark.skipif(sys.platform == "win32", reason="T1K is not supported on Windows")
def test_hla_streams_t1k_output_only_with_debug(
    tmp_path, mhc_bam, hla_setup, monkeypatch, capfd
):
    # PR #492 review: a persistent run log sets the root logger to DEBUG, which
    # streamed T1K's output into ordinary (even --quiet) runs.
    fake_tools(
        monkeypatch,
        tmp_path,
        {
            "run-t1k": FAKE_T1K.format(
                reads=str(hla_setup.reads), genotype=str(hla_setup.genotype)
            )
            + 'print("T1K chatter")\n'
        },
    )
    argv = ["typing", "hla", "-i", str(mhc_bam), "--hla-index", str(hla_setup.index),
            "-o", str(tmp_path / "out"), "--config", str(tmp_path / "config.toml")]  # fmt: skip
    main([*argv, "--log-file", str(tmp_path / "run1.log")])
    assert "T1K chatter" not in capfd.readouterr().out
    main(["--debug", *argv, "--log-file", str(tmp_path / "run2.log")])
    assert "T1K chatter" in capfd.readouterr().out


def test_hla_accepts_home_relative_paths(tmp_path, mhc_bam, hla_setup, monkeypatch):
    # PR #492 review: the alignment check ran before ~ was expanded, so an
    # existing ~/sample.bam was refused as missing.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    relative = Path(mhc_bam).relative_to(tmp_path)
    result = api.genotyping.hla(
        api.TypingHlaParams(
            input=Path("~", relative),
            hla_index=hla_setup.index,
            outdir=Path("~/home_out"),
        )
    )
    assert result.exit_code == 0
    assert (tmp_path / "home_out" / "NA12878.t1k_genotype.tsv").is_file()


def test_hla_log_agrees_with_the_pharmcat_call(tmp_path, mhc_bam, hla_setup, caplog):
    caplog.set_level(logging.INFO)
    run_wgsextract("typing", "hla", "-i", mhc_bam, "--hla-index",
                   str(hla_setup.index), "-o", str(tmp_path))  # fmt: skip
    assert "HLA-B\t*57:01/*08:01" in (tmp_path / "NA12878.hla.pharmcat.tsv").read_text()
    assert "HLA-B     no confident call" not in caplog.text


def test_hla_leaves_unconfident_genes_out_of_pharmcat(
    tmp_path, mhc_bam, hla_setup, caplog
):
    hla_setup.genotype.write_text(
        "HLA-A\t2\tHLA-A*02:01:01:01\t95.8\t60\tHLA-A*24:02:01:01\t9.1\t0\t.\n"
        "HLA-B\t1\tHLA-B*57:01:01:01,HLA-B*57:01:01:02\t60.1\t33\t.\t0\t-1\t.\n"
    )
    run_wgsextract("typing", "hla", "-i", mhc_bam, "--hla-index",
                   str(hla_setup.index), "-o", str(tmp_path))  # fmt: skip
    pharmcat = tmp_path / "NA12878.hla.pharmcat.tsv"
    assert pharmcat.read_text() == "HLA-B\t*57:01/*57:01\n"
    assert "No confident HLA-A call; it's left out for PharmCAT." in caplog.text

    hla_setup.genotype.write_text(
        "HLA-C\t1\tHLA-C*06:02:01:01\t71.0\t41\t.\t0\t-1\t.\n"
    )
    run_wgsextract("typing", "hla", "-i", mhc_bam, "--hla-index",
                   str(hla_setup.index), "-o", str(tmp_path))  # fmt: skip
    assert not pharmcat.exists()  # the earlier run's calls aren't left behind


def test_hla_reports_t1k_failure_with_its_stderr_tail(tmp_path, mhc_bam, hla_setup):
    hla_setup.genotype.unlink()
    with pytest.raises(WGSExtractError, match=r"T1K failed \(exit 1\)\. x{500}$"):
        run_wgsextract("typing", "hla", "-i", mhc_bam, "--hla-index",
                       str(hla_setup.index), "-o", str(tmp_path))  # fmt: skip
    assert outputs(tmp_path / "in") == {"NA12878.bam", "NA12878.bam.bai",
                                       "NA12878.sam"}  # fmt: skip
    assert not any(p.name.startswith(".wgse_hla_") for p in tmp_path.iterdir())


def test_hla_extraction_uses_hg19_mhc_and_numeric_names(tmp_path, hla_setup):
    contigs = {"1": 249_250_621, "6": 171_115_067}
    reads = [*pair("in", "6", 28_477_797), *pair("hg38_only", "6", 33_470_000)]
    path = bam(tmp_path / "s.bam", reads, contigs)
    run_wgsextract("typing", "hla", "-i", path, "--hla-index", str(hla_setup.index))
    names, _ = json.loads(hla_setup.reads.read_text())
    assert names == ["in"]
    assert (tmp_path / "s.hla.pharmcat.tsv").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="T1K is not supported on Windows")
def test_report_shows_the_hla_calls_with_t1k_quality(tmp_path, mhc_bam, hla_setup):
    # Issue #398: `report` shows `typing hla` like the CYP2D6 call.
    hla_setup.genotype.write_text(
        T1K_GENOTYPE.replace(
            "HLA-A*24:02:01:01\t80.12\t45", "HLA-A*24:02:01:01\t9.1\t0"
        )
    )
    out = tmp_path / "out"
    main(["typing", "hla", "-i", str(mhc_bam), "--hla-index", str(hla_setup.index),
          "-o", str(out), "--config", str(tmp_path / "config.toml")])  # fmt: skip
    found = {
        f["gene"]: f
        for f in build_report(out)["sections"]["pharmacogenomics"]["findings"]
    }
    a, b = found["HLA-A"], found["HLA-B"]
    assert (a["value"], a["confidence"], a["confidence_reasons"]) == (
        "no confident call",
        "low",
        ["no_call"],
    )
    assert (b["value"], b["confidence"]) == ("*57:01/*08:01", "high")
    assert b["detail"] == "T1K quality 33, 28; IPD-IMGT/HLA 3.56.0"
    assert b["evidence"]["file"] == "NA12878.t1k_genotype.tsv"

    # Review of #482: genotypes that no longer give the recorded call (say, a
    # later run was interrupted) don't vouch for it.
    genotypes = out / "NA12878.t1k_genotype.tsv"
    genotypes.write_text(genotypes.read_text().replace("B*57:01", "B*58:01"))
    pgx = build_report(out)["sections"]["pharmacogenomics"]
    found = {f["gene"]: f for f in pgx["findings"]}
    assert (found["HLA-B"]["confidence"], found["HLA-B"]["confidence_reasons"]) == (
        "not_assessed",
        ["evidence_mismatch"],
    )
    assert found["HLA-A"]["confidence"] == "low"
    assert [w["code"] for w in pgx["warnings"]].count("evidence_mismatch") == 1

    # Without T1K's genotypes the qualities are unknown.
    (out / "NA12878.t1k_genotype.tsv").unlink()
    found = {
        f["gene"]: f
        for f in build_report(out)["sections"]["pharmacogenomics"]["findings"]
    }
    assert (found["HLA-B"]["confidence"], found["HLA-B"]["confidence_reasons"]) == (
        "moderate",
        ["quality_unknown"],
    )


def test_hla_index_must_exist(tmp_path, mhc_bam, hla_setup):
    with pytest.raises(WGSExtractError, match="HLA index not found"):
        run_wgsextract("typing", "hla", "-i", mhc_bam, "--hla-index",
                       str(tmp_path / "nope.fa"))  # fmt: skip
    assert hla_setup.calls() == []


FAKE_T1K_BUILD = """\
work = pathlib.Path(opt("-o"))
assert opt("--download") == "IPD-IMGT/HLA" and opt("--prefix") == "hla"
if {fail!r}:
    sys.exit("could not download hla.dat")
(work / "hla.dat").write_text(
    "ID   HLA00001; SV 1; standard; DNA; HUM; 3503 BP.\\n"
    "CC   IPD-IMGT/HLA Release Version 3.56.0\\n"
)
(work / "hla_dna_seq.fa").write_text(">HLA-A*01:01:01:01\\nACGT\\n")
(work / "hla_rna_seq.fa").write_text(">HLA-A*01:01:01:01\\nACGT\\n")
"""


def _index_args(tmp_path):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    settings["reference_library"] = str(library)
    params = api.TypingHlaParams(input=tmp_path / "s.bam")
    return params, library / "ref" / "t1k_hla"


def test_hla_index_is_built_once_in_the_reference_library(tmp_path, monkeypatch):
    calls = fake_tools(
        monkeypatch, tmp_path, {"t1k-build.pl": FAKE_T1K_BUILD.format(fail=False)}
    )
    args, directory = _index_args(tmp_path)

    assert hla.hla_index(args) == str(directory / "hla_dna_seq.fa")
    assert outputs(directory) == {"hla_dna_seq.fa", "release.txt"}
    assert (directory / "release.txt").read_text() == "IPD-IMGT/HLA 3.56.0\n"
    assert outputs(directory.parent) == {"t1k_hla"}  # no work directory left

    assert hla.hla_index(args) == str(directory / "hla_dna_seq.fa")
    assert len(calls("t1k-build.pl")) == 1


def test_hla_index_build_failure_is_reported(tmp_path, monkeypatch):
    fake_tools(
        monkeypatch, tmp_path, {"t1k-build.pl": FAKE_T1K_BUILD.format(fail=True)}
    )
    args, directory = _index_args(tmp_path)
    with pytest.raises(WGSExtractError, match="could not download hla.dat"):
        hla.hla_index(args)
    assert outputs(directory.parent) == set()


def test_hla_index_rebuilds_over_an_incomplete_index(tmp_path, monkeypatch):
    fake_tools(
        monkeypatch, tmp_path, {"t1k-build.pl": FAKE_T1K_BUILD.format(fail=False)}
    )
    args, directory = _index_args(tmp_path)
    directory.mkdir(parents=True)
    (directory / "release.txt").write_text("IPD-IMGT/HLA 3.50.0\n")
    assert Path(hla.hla_index(args)).is_file()


def test_hla_index_needs_a_reference_library(monkeypatch):
    settings.pop("reference_library", None)
    with pytest.raises(WGSExtractError, match="pass --hla-index"):
        hla.hla_index(api.TypingHlaParams(input=Path("s.bam")))


# --- typing cyp2d6 ------------------------------------------------------------

# Cyrius's CYP2D6_region_38.bed: its CYP2D6/CYP2D7 regions and the
# normalization regions (type "norm", with GC content) it measures depth against.
REGION_BED = """\
chr22\t42126000\t42132000\tcyp2d6\tcyp2d6\t0.55
chr22\t42139500\t42145000\tcyp2d7\tcyp2d7\t0.56
chr1\t1000000\t1002000\tnorm1\tnorm\t0.61
chrX\t5000000\t5002000\tnorm2\tnorm\t0.38
"""

FAKE_CYRIUS = """\
work, prefix = opt("-o"), opt("-p")
[reads] = pathlib.Path(opt("-m")).read_text().split()
view = subprocess.run(["samtools", "view", reads], capture_output=True, text=True)
pathlib.Path({seen!r}).write_text(view.stdout)
result = json.loads(pathlib.Path({result!r}).read_text())
pathlib.Path(work, prefix + ".json").write_text(json.dumps({{"NA12878": result}}))
pathlib.Path(work, prefix + ".tsv").write_text(
    f"Sample\\tGenotype\\tFilter\\nNA12878\\t{{result['Genotype']}}\\t"
    f"{{result['Filter']}}\\n"
)
"""

CYRIUS_RESULT = {
    "Coverage_MAD": 0.07,
    "Median_depth": 31.2,
    "Total_CN": 3,
    "Spacer_CN": 2,
    "Total_CN_raw": 3.04,
    "Spacer_CN_raw": 2.01,
    "Variants_called": ["g.42126611C>G", "g.42127941G>A"],
    "CNV_group": "dup",
    "Genotype": "*1/*2x2",
    "Filter": "PASS",
    "Raw_star_allele": ["*1_*2_*2"],
    "Call_info": "pass_",
    "Exon9_CN": 3,
    "CNV_consensus": "dup",
    "d67_snp_call": "3,3",
    "d67_snp_raw": "3.0,3.0",
}


@pytest.fixture
def cyrius(tmp_path, monkeypatch):
    bed = tmp_path / "cyrius_data" / "CYP2D6_region_38.bed"
    bed.parent.mkdir()
    bed.write_text(REGION_BED)
    result = tmp_path / "cyrius_result.json"
    result.write_text(json.dumps(CYRIUS_RESULT))
    seen = tmp_path / "cyrius_input.sam"
    calls = fake_tools(
        monkeypatch,
        tmp_path,
        {
            "cyrius": FAKE_CYRIUS.format(seen=str(seen), result=str(result)),
            # Prints where `import cyrius` finds its data, like the real one.
            "cyrius_python": f"print({str(bed.parent)!r} + '/CYP2D6_region_' "
            "+ sys.argv[-1] + '.bed')\n",
        },
    )

    def set_result(**fields):
        result.write_text(json.dumps({**CYRIUS_RESULT, **fields}))

    return Namespace(calls=calls, seen=seen, set_result=set_result, bed=bed)


def test_cyp2d6_runs_cyrius_on_the_input_and_writes_the_pharmcat_call(
    tmp_path, cyrius, caplog
):
    caplog.set_level(logging.INFO)
    path = bam(tmp_path / "in" / "NA12878.bam", pair("p", "chr22", 42_127_000))
    out = tmp_path / "out"
    result = api.genotyping.cyp2d6(api.TypingCyp2d6Params(input=Path(path), outdir=out))

    assert cyrius.calls("cyrius_python")[0][-1] == "38"
    [command] = cyrius.calls("cyrius")
    assert command[command.index("-g") + 1] == "38"
    assert command[command.index("-p") + 1] == "NA12878.cyrius"
    assert "-r" not in command  # a BAM needs no reference
    assert cyrius.seen.read_text().count("\tchr22\t42127000\t") == 1

    assert outputs(out) == {
        "NA12878.cyrius.tsv",
        "NA12878.cyrius.json",
        "NA12878.cyp2d6.pharmcat.tsv",
        "NA12878.cyrius.tsv.manifest.json",
    }
    assert (out / "NA12878.cyp2d6.pharmcat.tsv").read_text() == "CYP2D6\t*1/*2x2\n"
    assert (
        "CYP2D6: *1/*2x2 (filter PASS, 3 copies; raw 3.04; median depth 31.2x)"
        in caplog.text
    )
    assert result.metrics["cyp2d6_genotype"] == "*1/*2x2"
    assert result.metrics["cyp2d6_copy_number"] == 3


@pytest.mark.parametrize(
    ("result", "pharmcat", "warning"),
    [
        (
            {"Genotype": "*1/*4x2", "Filter": "LowQ_high_CN", "Total_CN": 4},
            "CYP2D6\t*1/*4x2\n",
            "4 or more copies",
        ),
        (
            {"Genotype": "*1/*4;*4/*10", "Filter": "More_than_one_possible_genotype"},
            None,
            "couldn't settle on one genotype (More_than_one_possible_genotype)",
        ),
        (
            {"Genotype": None, "Filter": None, "Total_CN": None, "Median_depth": 8.4},
            None,
            "check the median depth (8.4x)",
        ),
    ],
)
def test_cyp2d6_writes_only_calls_cyrius_stands_behind(
    tmp_path, cyrius, caplog, result, pharmcat, warning
):
    path = bam(tmp_path / "NA12878.bam", pair("p", "chr22", 42_127_000))
    stale = tmp_path / "NA12878.cyp2d6.pharmcat.tsv"
    stale.write_text("CYP2D6\t*1/*1\n")
    cyrius.set_result(**result)
    run_wgsextract("typing", "cyp2d6", "-i", path)
    assert (stale.read_text() if stale.exists() else None) == pharmcat
    assert warning in caplog.text


def test_cyp2d6_names_three_copies_as_pharmcat_does(tmp_path, cyrius):
    path = bam(tmp_path / "NA12878.bam", pair("p", "chr22", 42_127_000))
    cyrius.set_result(Genotype="*1/*2x3", Filter="PASS", Total_CN=4)
    run_wgsextract("typing", "cyp2d6", "-i", path)
    pharmcat = tmp_path / "NA12878.cyp2d6.pharmcat.tsv"
    assert pharmcat.read_text(encoding="utf-8") == "CYP2D6\t*1/*2x≥3\n"


def test_cyp2d6_failure_is_reported(tmp_path, cyrius):
    path = bam(tmp_path / "NA12878.bam", pair("p", "chr22", 42_127_000))
    (tmp_path / "cyrius_result.json").write_text("not json")
    with pytest.raises(WGSExtractError, match=r"Cyrius failed \(exit 1\)"):
        run_wgsextract("typing", "cyp2d6", "-i", path)
    assert not [
        p.name
        for p in tmp_path.iterdir()
        if "cyrius." in p.name
        or "pharmcat" in p.name
        or p.name.startswith(".wgse_cyp2d6_")
    ]


def test_cyp2d6_needs_cyrius_data_and_chr_names(tmp_path, cyrius):
    path = bam(tmp_path / "s.bam", pair("p", "chr22", 42_127_000))
    cyrius.bed.unlink()
    with pytest.raises(WGSExtractError, match="region file .*CYP2D6_region_38.bed"):
        run_wgsextract("typing", "cyp2d6", "-i", path)

    contigs = {"1": 248_956_422, "22": 50_818_468}
    path = bam(tmp_path / "n.bam", pair("p", "22", 42_127_000), contigs)
    with pytest.raises(WGSExtractError, match="named chr1, chr2"):
        run_wgsextract("typing", "cyp2d6", "-i", path)


def test_cyp2d6_with_alt_contigs_needs_the_reference(tmp_path, cyrius):
    contigs = {**HG38, "chr22_KI270879v1_alt": 304_135}
    path = bam(tmp_path / "s.bam", pair("p", "chr22", 42_127_000), contigs)
    with pytest.raises(WGSExtractError, match="--ref is required"):
        run_wgsextract("typing", "cyp2d6", "-i", path)
    assert cyrius.calls("cyrius") == []


@needs_tools("minimap2")
def test_cyp2d6_realigns_alt_contig_reads_to_primary_chr22(tmp_path, cyrius):
    """Reads of the locus split between chr22 and its ALT copy (MAPQ 0) are
    realigned to chr22; the normalization regions are passed through as they
    were and nothing else of chr22 is."""
    rng = random.Random(3)
    lo, hi = 42_080_000, 42_180_000
    locus = "".join(rng.choice("ACGT") for _ in range(hi - lo))
    (tmp_path / "ref").mkdir()
    ref = write_fasta(
        tmp_path / "ref" / "hs38DH.fa",
        {"chr22": "N" * lo + locus + "N" * (HG38["chr22"] - hi)},
    )

    def piece(pos):  # 100 bp of chr22 at 1-based pos
        return locus[pos - 1 - lo : pos - 1 - lo + 100]

    def locus_pair(name, at, chrom, pos):
        """A pair from chr22:at and chr22:at+200 (reverse strand), as SAM
        stores it, aligned at chrom:pos with MAPQ 0."""
        return pair(name, chrom, pos, mapq=0, seq=piece(at), mate_seq=piece(at + 200))

    contigs = {**HG38, "chr22_KI270879v1_alt": 304_135}
    reads = [
        *locus_pair("on_alt", 42_127_001, "chr22_KI270879v1_alt", 150_001),
        *locus_pair("on_chr22", 42_130_001, "chr22", 42_130_001),
        *pair("norm", "chr1", 1_000_500),
        *pair("elsewhere", "chr22", 20_000_000),
    ]
    path = bam(tmp_path / "in" / "s.bam", reads, contigs)
    run_wgsextract("typing", "cyp2d6", "-i", path, "--ref", str(ref), "-t", "2")

    seen = [line.split("\t") for line in cyrius.seen.read_text().splitlines()]
    placed = sorted((r[0], r[2], int(r[3])) for r in seen)
    assert placed == [
        ("norm", "chr1", 1_000_500),
        ("norm", "chr1", 1_000_700),
        ("on_alt", "chr22", 42_127_001),
        ("on_alt", "chr22", 42_127_201),
        ("on_chr22", "chr22", 42_130_001),
        ("on_chr22", "chr22", 42_130_201),
    ]
    assert all(int(r[4]) > 0 for r in seen if r[0] != "norm")
    [command] = cyrius.calls("cyrius")
    assert "-r" not in command
    assert (tmp_path / "in" / "s.cyp2d6.pharmcat.tsv").read_text() == (
        "CYP2D6\t*1/*2x2\n"
    )


# --- typing str ---------------------------------------------------------------


def _variant(vid, unit, region, genotype, ci):
    return {
        "CountsOfFlankingReads": "(2, 1)",
        "CountsOfInrepeatReads": "()",
        "CountsOfSpanningReads": "(17, 9), (40, 4)",
        "Genotype": genotype,
        "GenotypeConfidenceInterval": ci,
        "ReferenceRegion": region,
        "RepeatUnit": unit,
        "VariantId": vid,
        "VariantSubtype": "Repeat",
        "VariantType": "Repeat",
    }


def _locus(lid, coverage, *variants, allele_count=2):
    return {
        lid: {
            "AlleleCount": allele_count,
            "Coverage": coverage,
            "FragmentLength": 433,
            "LocusId": lid,
            "ReadLength": 150,
            "Variants": {v["VariantId"]: v for v in variants},
        }
    }


# ExpansionHunter 5's JSON for a male sample (one allele on chrX).
EH_RESULT = {
    "LocusResults": {
        **_locus(
            "HTT",
            34.2,
            _variant("HTT", "CAG", "chr4:3074876-3074933", "19/42", "19-19/39-45"),
            _variant("HTT_CCG", "CCG", "chr4:3074939-3074966", "7/10", "7-7/10-10"),
        ),
        **_locus(
            "FMR1",
            15.7,
            _variant("FMR1", "GGC", "chrX:147912050-147912110", "30", "30-30"),
            allele_count=1,
        ),
        **_locus(
            "C9ORF72",
            29.0,
            _variant("C9ORF72", "GGCCCC", "chr9:27573528-27573546", "2/8", "2-2/8-35"),
        ),
        **_locus(
            "DIP2B",
            30.1,
            _variant("DIP2B", "GGC", "chr12:50505001-50505022", "7/9", "7-7/9-9"),
        ),
        **_locus(
            "ATXN8OS",
            31.0,
            _variant(
                "ATXN8OS", "CTG", "chr13:70139383-70139428", "15/22", "15-15/22-22"
            ),
            _variant(
                "ATXN8OS_CTA", "CTA", "chr13:70139353-70139383", "10/11", "10-10/11-11"
            ),
        ),  # fmt: skip
        "NOP56": {"AlleleCount": 2, "Coverage": 0.0, "LocusId": "NOP56"},
        **_locus(
            "RFC1",
            3.0,
            {
                **_variant("RFC1", "AAAAG", "chr4:39348424-39348483", "", ""),
                "Genotype": None,
            },
        ),  # fmt: skip
    },
    "SampleParameters": {"SampleId": "NA12878", "Sex": "Male"},
}

FAKE_EH = """\
prefix = opt("--output-prefix")
if {fail!r}:
    sys.exit("[2026-10-24 10:00:00] [Error] Reference contig chr4 is missing")
pathlib.Path(prefix + ".json").write_text(pathlib.Path({result!r}).read_text())
pathlib.Path(prefix + ".vcf").write_text("##fileformat=VCFv4.1\\n")
pathlib.Path(prefix + "_realigned.bam").write_bytes(b"")
"""


@pytest.fixture
def eh(tmp_path, monkeypatch):
    result = tmp_path / "eh_result.json"
    result.write_text(json.dumps(EH_RESULT))
    catalog = tmp_path / "variant_catalog.json"
    catalog.write_text("[]")
    ref = tmp_path / "ref" / "hg38.fa"
    ref.parent.mkdir()
    ref.write_text(">chr1\nACGT\n")
    scripts = {"ExpansionHunter": FAKE_EH.format(fail=False, result=str(result))}
    calls = fake_tools(monkeypatch, tmp_path, scripts)
    return Namespace(calls=calls, catalog=catalog, ref=ref, result=result)


def _sex_reads(x, y):
    reads = []
    for i in range(x):
        reads += pair(f"x{i}", "chrX", 5_000_000 + 1000 * i)
    for i in range(y):
        reads += pair(f"y{i}", "chrY", 5_000_000 + 1000 * i)
    return reads


def test_str_sizes_repeats_and_flags_those_outside_the_normal_range(
    tmp_path, eh, caplog
):
    caplog.set_level(logging.INFO)
    path = bam(tmp_path / "in" / "NA12878.bam", _sex_reads(10, 2))
    out = tmp_path / "out"
    result = api.genotyping.repeats(
        api.TypingStrParams(
            input=Path(path), ref=eh.ref, catalog=eh.catalog, outdir=out, threads=2
        )
    )

    [command] = eh.calls("ExpansionHunter")
    options = dict(zip(command[::2], command[1::2], strict=True))
    assert options == {
        "--reads": path,
        "--reference": str(eh.ref),
        "--variant-catalog": str(eh.catalog),
        "--output-prefix": str(out / "NA12878.str"),
        "--sex": "male",  # 2 chrY pairs to 10 chrX
        "--threads": "2",
    }
    assert "Sample sex: male (from chrX/chrY coverage)" in caplog.text

    rows = [
        line.split("\t") for line in (out / "NA12878.str.tsv").read_text().splitlines()
    ]
    assert rows[0][:2] == ["locus", "disease"]
    table = {row[0]: row[1:] for row in rows[1:]}
    assert table == {
        "ATXN8OS": ["SCA8", "AD", "CTG", "15/22", "15-15/22-22", "50", "71",
                    "normal", "31.0", "-"],
        "C9ORF72": ["FTD/ALS", "AD", "GGCCCC", "2/8", "2-2/8-35", "23", "31",
                    "normal (CI reaches pathogenic)", "29.0", "-"],
        "DIP2B": ["-", "-", "GGC", "7/9", "7-7/9-9", "-", "-", "-", "30.1", "-"],
        "FMR1": ["Fragile X", "XD", "GGC", "30", "30-30", "44", "201", "normal",
                 "15.7", "-"],
        "HTT": ["Huntington disease", "AD", "CAG", "19/42", "19-19/39-45", "26",
                "36", "pathogenic", "34.2", "-"],
    }  # fmt: skip
    assert "2 of 4 disease repeats are in the normal range." in caplog.text
    assert (
        "HTT (Huntington disease, AD): CAG 19/42 [CI 19-19/39-45] pathogenic"
        in caplog.text
    )
    assert "C9ORF72 (FTD/ALS, AD): GGCCCC 2/8 [CI 2-2/8-35] normal (CI" in caplog.text
    assert "isn't a diagnosis" in caplog.text
    assert {"NA12878.str.json", "NA12878.str.vcf", "NA12878.str.tsv"} <= outputs(out)
    assert (result.metrics["sex"], result.metrics["flagged_repeats"]) == ("male", 2)
    assert {w.code for w in result.warnings} == {"repeat_outside_normal_range"}


def test_str_all_normal_has_no_warnings(tmp_path, eh, caplog):
    caplog.set_level(logging.INFO)
    eh.result.write_text(
        json.dumps(
            {"LocusResults": _locus("HTT", 30.0, _variant(
                "HTT", "CAG", "chr4:3074876-3074933", "17/19", "17-17/19-19"))}
        )
    )  # fmt: skip
    path = bam(tmp_path / "s.bam", _sex_reads(4, 0))
    run_wgsextract("typing", "str", "-i", path, "--ref", str(eh.ref),
                   "--catalog", str(eh.catalog), "--sex", "female")  # fmt: skip
    assert "1 of 1 disease repeats are in the normal range." in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.skipif(
    not pixi_env_prefix("expansionhunter"), reason="pixi install -e expansionhunter"
)
def test_str_on_hg19_says_notch2nlc_is_unavailable(tmp_path, eh):
    """#643 review: ExpansionHunter's hg19 catalog (and the bundled long-read
    one) lack NOTCH2NLC, which GRCh37 doesn't hold."""
    hg19 = {**HG38, "chr1": 249_250_621}
    path = bam(tmp_path / "in" / "s.bam", _sex_reads(4, 0), contigs=hg19)
    result = api.genotyping.repeats(
        api.TypingStrParams(input=Path(path), ref=eh.ref, sex="female",
                            outdir=tmp_path / "out", threads=1)
    )  # fmt: skip
    rows = {
        line.split("\t")[0]: line.split("\t")
        for line in (tmp_path / "out" / "s.str.tsv").read_text().splitlines()
    }
    assert rows["NOTCH2NL"][8].startswith("not available on hg19")
    assert ("repeat_unavailable", "NOTCH2NL") in {
        (w.code, w.details.get("locus")) for w in result.warnings
    }


@pytest.mark.parametrize(
    ("x", "y", "sex", "warned"),
    [
        (10, 0, "female", True),  # chrX alone, with no chrY or chr7 to compare
        (0, 0, "female", True),
        (30, 1, "female", False),
        (10, 2, "male", False),
    ],
)
def test_str_infers_sex_from_chrx_chry_reads(tmp_path, eh, caplog, x, y, sex, warned):
    path = bam(tmp_path / "s.bam", _sex_reads(x, y) or pair("p", "chr1", 1000))
    run_wgsextract("typing", "str", "-i", path, "--ref", str(eh.ref),
                   "--catalog", str(eh.catalog))  # fmt: skip
    [command] = eh.calls("ExpansionHunter")
    assert command[command.index("--sex") + 1] == sex
    assert ("Couldn't infer the sample's sex" in caplog.text) == warned


def test_str_prefers_info_cached_sex_and_an_explicit_sex(tmp_path, eh):
    path = bam(tmp_path / "s.bam", _sex_reads(10, 0))
    st = Path(path).stat()  # the cache must be from this very file
    (tmp_path / "s.bam.wgse_info.json").write_text(
        json.dumps({
            "md5_signature": calculate_bam_md5(path, None), "gender": "Male",
            "sex_inference_version": SEX_INFERENCE_VERSION,
            "input_signature": {"size": st.st_size, "mtime_ns": st.st_mtime_ns},
        })
    )  # fmt: skip
    args = ["typing", "str", "-i", path, "--ref", str(eh.ref), "--catalog",
            str(eh.catalog)]  # fmt: skip
    run_wgsextract(*args)
    run_wgsextract(*args, "--sex", "female")
    assert [c[c.index("--sex") + 1] for c in eh.calls()] == ["male", "female"]


def test_str_reports_expansionhunter_failure(tmp_path, eh, monkeypatch):
    fake_tools(
        monkeypatch,
        tmp_path,
        {"ExpansionHunter": FAKE_EH.format(fail=True, result=str(eh.result))},
    )
    path = bam(tmp_path / "s.bam", _sex_reads(2, 0))
    with pytest.raises(
        WGSExtractError,
        match=r"ExpansionHunter failed \(exit 1\): .*Reference contig chr4 is missing$",
    ):
        run_wgsextract("typing", "str", "-i", path, "--ref", str(eh.ref),
                       "--catalog", str(eh.catalog), "--sex", "male")  # fmt: skip


def test_str_needs_the_reference_and_a_catalog(tmp_path, eh):
    path = bam(tmp_path / "s.bam", _sex_reads(2, 0))
    with pytest.raises(WGSExtractError, match="--ref is required"):
        run_wgsextract("typing", "str", "-i", path, "--catalog", str(eh.catalog))
    with pytest.raises(WGSExtractError, match="Variant catalog not found"):
        run_wgsextract("typing", "str", "-i", path, "--ref", str(eh.ref),
                       "--catalog", str(tmp_path / "nope.json"))  # fmt: skip
    assert eh.calls() == []


@pytest.mark.parametrize(
    ("contigs", "name"),
    [
        ({"chr1": 248_956_422}, "hg38"),
        ({"1": 248_956_422}, "grch38"),
        ({"chr1": 249_250_621}, "hg19"),
        ({"1": 249_250_621}, "grch37"),
    ],
)
def test_str_default_catalog_matches_build_and_names(
    tmp_path, monkeypatch, contigs, name
):
    prefix = tmp_path / "envs" / "expansionhunter"
    catalogs = prefix / "share" / "ExpansionHunter" / "variant_catalog"
    for build in ("hg38", "grch38", "hg19", "grch37"):
        (catalogs / build).mkdir(parents=True)
        (catalogs / build / "variant_catalog.json").write_text("[]")
    monkeypatch.setattr(repeats, "pixi_env_prefix", {"expansionhunter": prefix}.get)
    aln = common.Alignment(
        path="s.bam",
        lengths=contigs,
        build="hg38" if 248_956_422 in contigs.values() else "hg19",
        chr_prefix="chr1" in contigs,
        reference=None,
        stem="s",
        outdir=".",
        threads="1",
    )
    assert repeats._catalog(aln, None) == str(catalogs / name / "variant_catalog.json")

    monkeypatch.setattr(repeats, "pixi_env_prefix", lambda env: None)
    with pytest.raises(WGSExtractError, match="pixi install -e expansionhunter"):
        repeats._catalog(aln, None)


# --- typing smn ---------------------------------------------------------------

SMN_CONTIGS = {**HG38, "chr5": 181_538_259}
SMN1_EXON7 = 70_951_900

FAKE_SMN_CALLER = """\
work, prefix = opt("--outDir"), opt("--prefix")
[reads] = pathlib.Path(opt("--manifest")).read_text().split()
result = json.loads(pathlib.Path({result!r}).read_text())
pathlib.Path(work, prefix + ".json").write_text(json.dumps({{"NA12878": result}}))
pathlib.Path(work, prefix + ".tsv").write_text("Sample\\tresult\\n" + str(result))
"""

# SMNCopyNumberCaller's JSON for NA12878 (SMN_1kGP_results.tsv: 2 + 2 copies).
SMN_RESULT = {
    "Coverage_MAD": 0.07,
    "Median_depth": 33.4,
    "Full_length_CN_raw": 3.77,
    "Total_CN_raw": 4.01,
    "SMN1": 2,
    "SMN2": 2,
    "SMN2delta78": 0,
    "isCarrier": False,
    "isSMA": False,
    "Info": "PASS:Majority",
    "g27134TG_raw": 0.0,
    "g27134TG_CN": 0,
}


@pytest.fixture
def smn(tmp_path, monkeypatch):
    result = tmp_path / "smn_result.json"
    result.write_text(json.dumps(SMN_RESULT))
    calls = fake_tools(
        monkeypatch,
        tmp_path,
        {"smn_caller.py": FAKE_SMN_CALLER.format(result=str(result))},
    )

    def set_result(**fields):
        result.write_text(json.dumps({**SMN_RESULT, **fields}))

    return Namespace(calls=calls, set_result=set_result)


def smn_bam(tmp_path, seq=SEQ, name="NA12878.bam"):
    return bam(tmp_path / "in" / name, pair("p", "chr5", SMN1_EXON7, seq=seq),
               SMN_CONTIGS)  # fmt: skip


def manifest_warnings(result):
    return {w["code"]: w for w in result.to_dict()["warnings"]}


def test_smn_reports_copy_numbers_and_says_it_is_not_a_diagnosis(tmp_path, smn, caplog):
    caplog.set_level(logging.INFO)
    out = tmp_path / "out"
    result = api.genotyping.smn(
        api.TypingSmnParams(input=Path(smn_bam(tmp_path)), outdir=out)
    )

    [command] = smn.calls("smn_caller.py")
    assert command[command.index("--genome") + 1] == "38"
    assert outputs(out) == {
        "NA12878.smn.tsv",
        "NA12878.smn.json",
        "NA12878.smn.tsv.manifest.json",
    }
    assert {o["role"] for o in result.to_dict()["outputs"]} == {
        "smn_calls",
        "smn_report",
    }
    assert result.metrics["smn1_copy_number"] == 2
    assert result.metrics["smn2_copy_number"] == 2
    assert result.metrics["sma_carrier"] is False
    assert result.metrics["sma_affected"] is False
    assert "SMN1: 2 copies, SMN2: 2 copies" in caplog.text
    assert "not a diagnosis" in caplog.text
    assert "2+0" in caplog.text
    assert manifest_warnings(result) == {}


@pytest.mark.skipif(
    sys.platform == "win32", reason="SMNCopyNumberCaller is not supported on Windows"
)
def test_smn_reports_a_carrier_and_its_report_finding(tmp_path, smn):
    smn.set_result(SMN1=1, SMN2=3, isCarrier=True)
    out = tmp_path / "out"
    main(["typing", "smn", "-i", smn_bam(tmp_path), "-o", str(out),
          "--config", str(tmp_path / "config.toml")])  # fmt: skip

    section = build_report(out)["sections"]["carrier_screening"]
    [finding] = section["findings"]
    assert finding["value"] == "1 SMN1 copy: SMA carrier"
    assert finding["confidence"] == "high"
    assert "SMN2 3" in finding["detail"]
    assert "not a diagnosis" in finding["note"]
    assert finding["evidence"]["file"] == "NA12878.smn.tsv"


def test_smn_flags_a_possible_silent_carrier(tmp_path, smn):
    smn.set_result(g27134TG_CN=1)
    result = api.genotyping.smn(
        api.TypingSmnParams(input=Path(smn_bam(tmp_path)), outdir=tmp_path / "out")
    )
    warning = manifest_warnings(result)["possible_silent_carrier"]
    assert "g.27134T>G" in warning["message"]
    assert result.metrics["g27134tg_copy_number"] == 1


@pytest.mark.parametrize(
    ("depth", "reported"),
    [(12.0, False), (29.9, False), (float("nan"), False), (30.0, True)],
)
def test_smn_withholds_calls_below_30x(tmp_path, smn, depth, reported):
    # #619 asks for a 30x minimum; NaN (an empty sampling region) is no depth.
    smn.set_result(Median_depth=depth)
    out = tmp_path / "out"
    result = api.genotyping.smn(api.TypingSmnParams(input=Path(smn_bam(tmp_path)),
                                                    outdir=out))  # fmt: skip
    assert (result.metrics["smn1_copy_number"] == 2) is reported
    if reported:
        assert "low_coverage" not in manifest_warnings(result)
        return
    details = manifest_warnings(result)["low_coverage"]["details"]
    assert details["threshold"] == 30.0
    # The caller's own files, which still hold its calls, aren't published as
    # the calls.
    assert not (out / "NA12878.smn.tsv").exists()
    assert (out / "NA12878.smn.below_min_depth.json").exists()
    [finding] = build_report(out)["sections"]["carrier_screening"]["findings"]
    assert (finding["value"], finding["confidence"]) == ("no call", "low")


def test_smn_rerun_with_unreadable_output_keeps_the_previous_calls(tmp_path, smn):
    # PR #663 review: a failed rerun mustn't replace the evidence of the last run.
    out = tmp_path / "out"
    path = smn_bam(tmp_path)
    api.genotyping.smn(api.TypingSmnParams(input=Path(path), outdir=out))
    before = (out / "NA12878.smn.tsv").read_text()
    (tmp_path / "smn_result.json").write_text('"not an object"')
    with pytest.raises(WGSExtractError, match="unreadable"):
        run_wgsextract("typing", "smn", "-i", path, "-o", str(out))
    assert (out / "NA12878.smn.tsv").read_text() == before


def test_smn_report_labels_a_caller_filter_low(tmp_path, smn):
    # The caller can call SMN1 0 with FLCNnoCall when its full-length model fails.
    smn.set_result(SMN1=0, SMN2="2-3", isSMA=True, Info="FLCNnoCall")
    out = tmp_path / "out"
    api.genotyping.smn(api.TypingSmnParams(input=Path(smn_bam(tmp_path)), outdir=out))
    [finding] = build_report(out)["sections"]["carrier_screening"]["findings"]
    assert finding["value"] == "0 SMN1 copies: consistent with SMA"
    assert (finding["confidence"], finding["confidence_reasons"]) == (
        "low",
        ["caller_filter"],
    )


def test_smn_warns_when_the_caller_makes_no_call(tmp_path, smn):
    smn.set_result(SMN1=None, isCarrier=None, isSMA=None, Info="Ambiguous")
    result = api.genotyping.smn(
        api.TypingSmnParams(input=Path(smn_bam(tmp_path)), outdir=tmp_path / "out")
    )
    assert manifest_warnings(result)["no_call"]["details"]["filter"] == "Ambiguous"


@pytest.mark.parametrize("case", ["long_reads", "ont_read_group", "no_reads"])
def test_smn_refuses_non_short_read_wgs_before_running_the_caller(tmp_path, smn, case):
    header = ["@HD\tVN:1.6\tSO:coordinate"]
    header += [f"@SQ\tSN:{n}\tLN:{length}" for n, length in SMN_CONTIGS.items()]
    seq, pos = SEQ, SMN1_EXON7
    if case == "long_reads":
        seq = "".join(_RNG.choice("ACGT") for _ in range(5000))
    elif case == "ont_read_group":
        # Mixed: short reads at SMN, but an ONT read group in the file.
        header += ["@RG\tID:a\tPL:ILLUMINA", "@RG\tID:b\tPL:ONT"]
    else:
        pos = 1_000_000
    path = sam_to_bam(header + pair("p", "chr5", pos, seq=seq), tmp_path / "s.bam")
    with pytest.raises(WGSExtractError, match="short-read|no reads at SMN"):
        run_wgsextract("typing", "smn", "-i", path)
    assert smn.calls("smn_caller.py") == []


def test_smn_needs_chr_names_on_grch38(tmp_path, smn):
    contigs = {k.removeprefix("chr"): v for k, v in SMN_CONTIGS.items()}
    path = bam(tmp_path / "s.bam", pair("p", "5", SMN1_EXON7), contigs)
    with pytest.raises(WGSExtractError, match="SMNCopyNumberCaller needs GRCh38"):
        run_wgsextract("typing", "smn", "-i", path)


def test_smn_without_its_environment_says_how_to_install_it(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "pixi_env_tool", lambda tool: None)
    with pytest.raises(WGSExtractError, match="pixi install -e smn"):
        run_wgsextract("typing", "smn", "-i", smn_bam(tmp_path))


def test_smn_report_names_one_copy_a_carrier_even_when_status_is_uncertain(
    tmp_path, smn
):
    # PR #663 review: an unsettled isCarrier must not read "not a carrier".
    smn.set_result(SMN1=1, SMN2=2, isCarrier=None)
    out = tmp_path / "out"
    api.genotyping.smn(api.TypingSmnParams(input=Path(smn_bam(tmp_path)), outdir=out))
    [finding] = build_report(out)["sections"]["carrier_screening"]["findings"]
    assert finding["value"] == "1 SMN1 copy: SMA carrier (status uncertain)"
    assert finding["confidence_reasons"] == ["status_uncertain"]


@pytest.mark.parametrize(
    ("program", "warned"),
    [("novoalign\tVN:V3.02.07", True), ("bwa\tVN:0.7.17", False), (None, False)],
)
def test_smn_warns_when_the_aligner_is_not_bwa_like(tmp_path, smn, program, warned):
    # #619: GIAB's HG002 Novoalign BAM gave SMN1 1/SMN2 1; its bwa BAM the
    # truth, 2/2.
    header = ["@HD\tVN:1.6\tSO:coordinate"]
    header += [f"@SQ\tSN:{n}\tLN:{length}" for n, length in SMN_CONTIGS.items()]
    if program:
        header.append(f"@PG\tID:{program.split()[0]}\tPN:{program}")
    path = sam_to_bam(header + pair("p", "chr5", SMN1_EXON7), tmp_path / "s.bam")
    out = tmp_path / "out"
    result = api.genotyping.smn(api.TypingSmnParams(input=Path(path), outdir=out))
    assert ("aligner_not_calibrated" in manifest_warnings(result)) is warned
    [finding] = build_report(out)["sections"]["carrier_screening"]["findings"]
    assert (finding["confidence"] == "low") is warned
