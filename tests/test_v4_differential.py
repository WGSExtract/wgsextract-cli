"""Differential corpus: the released WGS Extract v44.2 rules versus this CLI.

Each test runs the current CLI with real samtools/bcftools on a small
synthetic genome, applies the released v44.2 rule to the same input, and checks
both against the parity or documented difference recorded for each case in
``tests/fixtures/v4_differential/corpus.json`` (#1187, v44.2 migration
corpus). The v44.2 rules are reproduced from the pinned release program
(SHA-256 ee7b57e0...a52a23): liftover ``program/hg38tohg19.py:63-81``,
CombinedKit calling ``program/microarray.py:643-644,683-692`` and mtDNA VCF
``program/mainwindow.py:1779-1783``.
"""

import gzip
import hashlib
import itertools
import json
import random
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
from pyliftover import LiftOver

from tests.htslib_helpers import (
    needs_htslib,
    run_wgsextract,
    sam_to_bam,
    vcf_calls,
    write_fasta,
)
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import run_command, run_pipeline

pytestmark = needs_htslib

CORPUS = Path(__file__).parent / "fixtures" / "v4_differential" / "corpus.json"
LENGTH = 5000
# The released v8 reference library's microarray/ploidy.txt: every sample
# diploid on every contig (SHA-256 d209259d...c3c3ab).
V44_2_PLOIDY = "* * * F 2\n* * * M 2"


def _cases() -> dict[str, dict[str, Any]]:
    return {case["id"]: case for case in json.loads(CORPUS.read_text())["cases"]}


def _tuple(value: Any) -> Any:
    """JSON lists as the tuples the tests observe, nested ones included."""
    return tuple(map(_tuple, value)) if isinstance(value, list) else value


def _assert_case(
    case_id: str, v44_2: Any, current: Any, same: bool | None = None
) -> None:
    """Observed outputs match the corpus record, and so does its relation:
    parity exactly when both programs give the same result."""
    case = _cases()[case_id]
    expected = case["expected"]
    assert (v44_2, current) == (
        _tuple(expected["v44_2"]),
        _tuple(expected["current"]),
    ), case_id
    same = v44_2 == current if same is None else same
    assert (case["relation"] == "parity") == same, case_id


def _genome(markers: dict[str, tuple[str, int]], contigs: tuple[str, ...]):
    rng = random.Random(1187)
    seqs = {c: [rng.choice("CGT") for _ in range(LENGTH)] for c in contigs}
    for chrom, pos in markers.values():
        seqs[chrom][pos - 1] = "A"
    return {c: "".join(s) for c, s in seqs.items()}


def _reads(
    seqs: dict[str, str],
    chrom: str,
    pos: int,
    genotype: str,
    name: str,
    *,
    depth: int = 30,
    mapq: int = 60,
) -> list[str]:
    lines = []
    for i in range(depth):
        start = pos - 60 + i
        read = list(seqs[chrom][start - 1 : start + 99])
        read[pos - start] = genotype[i % len(genotype)]
        lines.append(
            f"{name}{i}\t{16 * (i % 2)}\t{chrom}\t{start}\t{mapq}\t100M\t*\t0\t0\t"
            f"{''.join(read)}\t{'I' * 100}\tRG:Z:s"
        )
    return lines


def _targets(path: Path, markers: dict[str, tuple[str, int]]) -> str:
    path.write_text(
        "#CHROM\tPOS\tID\n"
        + "".join(f"{c}\t{p}\t{rsid}\n" for rsid, (c, p) in markers.items())
    )
    run_command([get_tool_path("bgzip"), "-f", str(path)])
    run_command([get_tool_path("tabix"), "-f", "-s1", "-b2", "-e2", f"{path}.gz"])
    return f"{path}.gz"


def _templates(root: Path, rows: dict[str, tuple[str, int]]) -> None:
    templates = root / "microarray" / "raw_file_templates"
    (templates / "head").mkdir(parents=True)
    (templates / "body").mkdir()
    (templates / "head" / "23andMe_V3.txt").write_text("# rsid\tchromosome\n")
    (templates / "body" / "23andMe_V3.txt").write_text(
        "".join(f"{r}\t{c.removeprefix('chr')}\t{p}\t\n" for r, (c, p) in rows.items())
    )


def _rows(path: Path) -> dict[str, tuple[str, ...]]:
    rows = {}
    for line in path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        rsid, *fields = line.split("\t")
        rows[rsid] = tuple(fields)
    return rows


@pytest.fixture(autouse=True)
def _offline_synthetic_library(monkeypatch):
    from wgsextract_cli.core.microarray import targets as microarray_targets

    def unexpected_download(url, _destination):
        pytest.fail(f"The differential corpus attempted a download: {url}")

    monkeypatch.setattr(microarray_targets, "download_file", unexpected_download)


# --- v44.2 rules ------------------------------------------------------------


def _v44_2_liftover(
    rows: dict[str, tuple[str, ...]], chain: Path
) -> dict[str, tuple[str, ...]]:
    """program/hg38tohg19.py:63-81: the one-based kit position goes to
    pyliftover's zero-based API unchanged, the first hit is kept, its strand is
    ignored and the returned zero-based position is written as is."""
    lifted = LiftOver(str(chain))
    out = {}
    for rsid, (chrom, pos, genotype, *_) in rows.items():
        name = f"chr{chrom}".replace("chrMT", "chrM")
        hits = lifted.convert_coordinate(name, int(pos))
        if hits:
            new_chrom = hits[0][0].replace("chrM", "chrMT").replace("chr", "")
            out[rsid] = (new_chrom, str(hits[0][1]), genotype)
    return out


def _v44_2_combined_kit(
    bam: str, fasta: str, targets: str, work: Path
) -> dict[str, tuple[str, ...]]:
    """program/microarray.py:643-644,683-692: bcftools with the released
    all-diploid ploidy file, no MAPQ or depth/GQ gate, the sed allele sort."""
    bcftools = get_tool_path("bcftools")
    ploidy = work / "v44.2-ploidy.txt"
    ploidy.write_text(V44_2_PLOIDY)
    called = work / "CombKit_called.vcf.gz"
    pileup = [bcftools, "mpileup", "-B", "-I", "-C", "50", "-T", targets]
    pileup += ["-f", fasta, "-Ou", bam]
    call = [bcftools, "call", "--ploidy-file", str(ploidy), "-V", "indels"]
    call += ["-m", "-P", "0", "-Oz", "-o", str(called)]
    run_pipeline([pileup, call])
    run_command([get_tool_path("tabix"), "-p", "vcf", str(called)])
    annotated = work / "CombKit_annotated.vcf.gz"
    run_command(
        [bcftools, "annotate", "-Oz", "-a", targets, "-c", "CHROM,POS,ID"]
        + ["-o", str(annotated), str(called)]
    )
    query = [bcftools, "query", "-f", "%ID\t%CHROM\t%POS[\t%TGT]\n", str(annotated)]
    rows = {}
    for line in run_command(query, capture_output=True).stdout.splitlines():
        rsid, chrom, pos, tgt = line.split("\t")
        genotype = tgt.replace("/", "", 1)
        genotype = "--" if genotype == ".." else "".join(sorted(genotype))
        rows[rsid] = (chrom.removeprefix("chr"), pos, genotype)
    return rows


# --- liftover ---------------------------------------------------------------

# hg38 chr1 -> hg19 chr1. Forward block hg38 [100,1600) -> hg19 [0,1500), a
# gap, forward block [1800,2800) -> [1600,2600); an inverted block
# [3000,3500) -> hg19 reverse strand; [4000,4400) placed twice. Zero-based.
CHAINS = (
    "chain 100 chr1 5000 + 100 2800 chr1 5000 + 0 2600 1\n1500 200 100\n1000\n\n"
    "chain 100 chr1 5000 + 3000 3500 chr1 5000 - 1500 2000 2\n500\n\n"
    "chain 100 chr1 5000 + 4000 4400 chr1 5000 + 2600 3000 3\n400\n\n"
    "chain 100 chr1 5000 + 4000 4400 chr1 5000 + 3600 4000 4\n400\n\n"
)
LIFT_MARKERS = {
    "liftover-interior": ("chr1", 1001),
    "liftover-block-end": ("chr1", 1600),
    "liftover-gap-edge": ("chr1", 1800),
    "liftover-inverted-block": ("chr1", 3201),
    "liftover-ambiguous-hit": ("chr1", 4201),
}


def test_liftover_block_edges_strand_and_ambiguous_hits(tmp_path, monkeypatch):
    root = tmp_path / "reference"
    seqs = _genome(LIFT_MARKERS, ("chr1",))
    (root / "genomes").mkdir(parents=True)
    fasta = str(write_fasta(root / "genomes" / "hg38.fa", seqs))
    chain = root / "hg38ToHg19.over.chain.gz"
    with gzip.open(chain, "wt") as handle:
        handle.write(CHAINS)
    _templates(root, {"liftover-interior": ("chr1", 901)})
    targets = _targets(tmp_path / "targets_hg38.tab", LIFT_MARKERS)
    header = [f"@SQ\tSN:chr1\tLN:{LENGTH}", "@RG\tID:s\tSM:s"]
    reads = [
        line
        for rsid, (chrom, pos) in LIFT_MARKERS.items()
        for line in _reads(seqs, chrom, pos, "AC", rsid)
    ]
    bam = sam_to_bam(header + reads, tmp_path / "sample.bam")
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Female")
    monkeypatch.setitem(settings, "reference_library", str(root))
    out = tmp_path / "out"

    run_wgsextract(
        "microarray",
        *("--input", bam, "--ref", fasta, "--outdir", str(out)),
        *("--formats", "23andme_v3", "--threads", "1", "--ref-vcf-tab", targets),
    )

    hg38 = _rows(out / "sample_CombinedKit.txt")
    assert set(hg38) == set(LIFT_MARKERS)  # every marker was called on hg38
    current = _rows(out / "sample_CombinedKit_hg19.txt")
    v44_2 = _v44_2_liftover(hg38, chain)
    for case_id in LIFT_MARKERS:
        _assert_case(case_id, v44_2.get(case_id), current.get(case_id))


# --- calling policy ---------------------------------------------------------

CALL_MARKERS = {
    "call-high-depth-het": ("chr1", 1000),
    "call-low-depth": ("chr1", 2000),
    "call-low-mapq": ("chr1", 3000),
    "call-male-x-nonpar": ("chrX", 3000),
}


def test_calling_policy_depth_mapq_and_male_x(tmp_path, monkeypatch):
    root = tmp_path / "reference"
    seqs = _genome(CALL_MARKERS, ("chr1", "chrX", "chrY"))
    (root / "genomes").mkdir(parents=True)
    fasta = str(write_fasta(root / "genomes" / "hg19.fa", seqs))
    _templates(root, CALL_MARKERS)
    targets = _targets(tmp_path / "targets_hg19.tab", CALL_MARKERS)
    header = [f"@SQ\tSN:{c}\tLN:{LENGTH}" for c in seqs] + ["@RG\tID:s\tSM:s"]
    reads = [
        *_reads(seqs, "chr1", 1000, "AC", "het"),
        *_reads(seqs, "chr1", 2000, "C", "low", depth=3),
        *_reads(seqs, "chr1", 3000, "G", "mapq", mapq=5),
        *_reads(seqs, "chrX", 3000, "AG", "x"),
        *_reads(seqs, "chrY", 3000, "C", "y"),
    ]
    bam = sam_to_bam(header + reads, tmp_path / "male.bam")
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Male")
    monkeypatch.setitem(settings, "reference_library", str(root))
    out = tmp_path / "out"

    run_wgsextract(
        "microarray",
        *("--input", bam, "--ref", fasta, "--outdir", str(out)),
        *("--formats", "23andme_v3", "--threads", "1", "--ref-vcf-tab", targets),
        "--call-reasons",
    )

    reasons = _rows(out / "male_CombinedKit_reasons.tsv")
    v44_2 = _v44_2_combined_kit(bam, fasta, targets, tmp_path)
    for case_id in CALL_MARKERS:
        genotype, reason = reasons[case_id][2:4]
        v44_2_genotype = v44_2[case_id][2]
        _assert_case(
            case_id,
            v44_2_genotype,
            (genotype, reason),
            same=v44_2_genotype == genotype,
        )


# --- genealogy: mtDNA VCF ---------------------------------------------------

MT_MARKERS = {"mt-vcf-homoplasmic": ("chrM", 1000), "mt-vcf-low-depth": ("chrM", 3000)}


def test_mt_vcf_homoplasmic_and_low_depth_variants(tmp_path):
    seqs = _genome(MT_MARKERS, ("chrM",))
    fasta = str(write_fasta(tmp_path / "ref.fa", seqs))
    header = [f"@SQ\tSN:chrM\tLN:{LENGTH}", "@RG\tID:s\tSM:s"]
    reads = [
        *_reads(seqs, "chrM", 1000, "G", "homoplasmic"),
        *_reads(seqs, "chrM", 3000, "G", "low", depth=2),
    ]
    bam = sam_to_bam(header + reads, tmp_path / "s.bam")
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Female")

    run_wgsextract(
        "extract", "mito-vcf", "--input", bam, "--ref", fasta,
        "--outdir", str(tmp_path / "out"),
    )  # fmt: skip

    bcftools = get_tool_path("bcftools")
    v44_2_vcf = tmp_path / "v44.2_chrM.vcf.gz"
    pileup = [bcftools, "mpileup", "-B", "-I", "-C", "50", "-r", "chrM"]
    pileup += ["-f", fasta, "-Ou", bam]
    call = [bcftools, "call", "--ploidy", "1", "-mv", "-P", "0"]
    run_pipeline([pileup, [*call, "-Oz", "-o", str(v44_2_vcf)]])
    run_command([get_tool_path("tabix"), str(v44_2_vcf)])
    v44_2 = {int(c[1]): c[2:] for c in vcf_calls(v44_2_vcf)}
    current = {int(c[1]): c[2:] for c in vcf_calls(tmp_path / "out" / "s_MT.vcf.gz")}
    for case_id, (_, pos) in MT_MARKERS.items():
        _assert_case(case_id, v44_2.get(pos), current.get(pos))


# --- whole-genome InDel export ----------------------------------------------


def test_indel_export_keeps_the_indels_v44_2_skipped(tmp_path):
    seqs = _genome({}, ("chr1",))
    fasta = str(write_fasta(tmp_path / "ref.fa", seqs))
    ref, deletion = seqs["chr1"], 1000  # one-based base before the 2 bp deletion
    lines = [f"@SQ\tSN:chr1\tLN:{LENGTH}", "@RG\tID:s\tSM:s"]
    for i in range(30):
        start = deletion - 60 + i
        left = deletion - start + 1
        bases = ref[start - 1 : deletion] + ref[deletion + 2 : start + 101]
        lines.append(
            f"del{i}\t{16 * (i % 2)}\tchr1\t{start}\t60\t{left}M2D{100 - left}M\t"
            f"*\t0\t0\t{bases}\t{'I' * 100}\tRG:Z:s"
        )
    bam = sam_to_bam(lines, tmp_path / "s.bam")
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Female")

    # Both programs get the released all-diploid ploidy file, as in #994's run.
    ploidy = tmp_path / "v44.2-ploidy.txt"
    ploidy.write_text(V44_2_PLOIDY)
    out = tmp_path / "out"

    run_wgsextract(
        "vcf", "indel", "--input", bam, "--ref", fasta, "--outdir", str(out),
        "--ploidy-file", str(ploidy),
    )  # fmt: skip

    bcftools = get_tool_path("bcftools")
    v44_2_vcf = tmp_path / "v44.2.indel.vcf.gz"
    run_pipeline(
        [
            [bcftools, "mpileup", "-B", "-I", "-C", "50", "-f", fasta, "-Ou", bam],
            [bcftools, "call", "--ploidy-file", str(ploidy), "-V", "snps", "-v"]
            + ["-m", "-P", "0", "-Ou"],
            [bcftools, "norm", "-f", fasta, "-Oz", "-o", str(v44_2_vcf)],
        ]
    )
    run_command([get_tool_path("tabix"), "-p", "vcf", str(v44_2_vcf)])
    _assert_case(
        "indel-export",
        tuple(vcf_calls(v44_2_vcf)),
        tuple(vcf_calls(out / "indels.vcf.gz")),
    )


# --- the corpus record ------------------------------------------------------


def test_corpus_cases_name_existing_evidence_or_a_tracking_issue():
    root = Path(__file__).parent.parent
    for case in _cases().values():
        assert case["relation"] in {"parity", "correction", "policy", "known-gap"}
        evidence = case.get("evidence")
        if case["relation"] == "known-gap":
            assert evidence is None and case["tracking"], case["id"]
            continue
        path, _, name = evidence.partition("::")
        assert f"def {name}(" in (root / path).read_text(), case["id"]


# --- the corpus runner ------------------------------------------------------


def test_public_corpus_fetch_needs_download_confirmation(tmp_path, capsys):
    from scripts.v4_differential_corpus import main

    cache = tmp_path / "cache"
    assert main(["fetch", "--cache", str(cache)]) == 1
    error = capsys.readouterr().err
    assert "HG00096" in error and "--download" in error
    assert not cache.exists()  # nothing was fetched or created


def test_public_corpus_run_names_missing_inputs_offline(tmp_path, capsys):
    from scripts.v4_differential_corpus import main

    cache = tmp_path / "cache"
    outdir = tmp_path / "out"
    assert main(["public", "--cache", str(cache), "--outdir", str(outdir)]) == 1
    error = capsys.readouterr().err
    assert "HG00096.slice.bam" in error and "fetch" in error and "--download" in error


# --- the migration guide ----------------------------------------------------


def test_migration_guide_names_current_commands_and_parseable_examples():
    """docs/migrating-from-v4.md: every example parses and every command the
    button map names exists, so the guide fails here when the CLI changes."""
    from wgsextract_cli.cli.help import command_index
    from wgsextract_cli.cli.parser import build_parser, parse_args

    parser, base = build_parser()
    guide = (Path(__file__).parent.parent / "docs" / "migrating-from-v4.md").read_text()
    examples = guide.split("```sh", 1)[1].split("```", 1)[0]
    commands = [
        shlex.split(line)[1:]
        for line in examples.splitlines()
        if line.startswith("wgsextract ")
    ]
    assert len(commands) >= 10
    for argv in commands:
        args, _ = parse_args(parser, base, argv)
        assert callable(args.func), argv

    table = guide.split("## Old buttons", 1)[1].split("## Working examples", 1)[0]
    rows = [row.split("|") for row in table.splitlines() if row.startswith("| ")]
    spans = re.findall(r"`([^`]+)`", "".join(row[3] for row in rows[2:]))
    named = {
        " ".join(itertools.takewhile(lambda t: not t.startswith("-"), span.split()))
        for span in spans
    }
    named = {name for name in named if name and "_" not in name}  # not settings
    assert len(named) >= 20
    assert named <= {entry.path for entry in command_index(parser)}, named


def test_public_corpus_rejects_a_cached_input_changed_after_fetch(tmp_path, capsys):
    """#1375 review: `public` rechecks every pinned cached input, not only the
    slice BAM, so an edited ploidy file, chain or template cannot pass."""
    from scripts.v4_differential_corpus import ZIP_PREFIX, load_corpus, main

    public = load_corpus()["public"]
    cache = tmp_path / "cache"
    reflib = cache / "reflib"
    for member in public["reference_zip_members"]:
        path = reflib / member.removeprefix(ZIP_PREFIX)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("edited after fetch\n")
    for path in (cache / "HG00096.slice.bam", reflib / "chr20.fa"):
        path.write_text("placeholder\n")
    for name in ("targets.tab", "targets.tab.gz"):
        (cache / name).write_text("placeholder\n")

    assert main(["public", "--cache", str(cache), "--outdir", str(tmp_path / "o")]) == 1
    assert "does not match its pinned SHA-256" in capsys.readouterr().err
    assert not (tmp_path / "o" / "public").exists()  # nothing ran


def test_public_corpus_runs_tools_resolved_to_wrapper_commands(tmp_path, monkeypatch):
    """#1375 review: a tool installed only in a Pixi environment resolves to a
    wrapper command (``pixi run -e ENV samtools``), not one executable path."""
    import os
    import subprocess
    import sys

    from scripts import v4_differential_corpus as corpus
    from wgsextract_cli.core import dependencies

    samtools = get_tool_path("samtools")
    seqs = _genome({}, ("chr1",))
    header = [f"@SQ\tSN:chr1\tLN:{LENGTH}", "@RG\tID:s\tSM:s"]
    bam = sam_to_bam(header + _reads(seqs, "chr1", 500, "A", "r"), tmp_path / "s.bam")
    direct = run_command([samtools, "view", bam], capture_output=True).stdout
    wrapper = tmp_path / "samtools_wrapper.py"
    wrapper.write_text(
        f"import subprocess, sys\n"
        f"sys.exit(subprocess.call([{samtools!r}, *sys.argv[1:]]))\n"
    )
    parts = [sys.executable, str(wrapper)]
    command = subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)
    monkeypatch.setattr(
        dependencies,
        "get_tool_path",
        lambda name: command if name == "samtools" else None,
    )

    digest = corpus.records_sha256(Path(bam))

    assert digest == hashlib.sha256(direct.encode()).hexdigest()


def test_synthetic_corpus_fails_when_a_completed_fix_did_not_run(tmp_path, monkeypatch):
    """#1375 review: a skipped completed-fix regression fails the run even
    when every comparison case passes."""
    from scripts import v4_differential_corpus as corpus

    tests = tmp_path / "test_evidence.py"
    tests.write_text(
        "import pytest\n\n"
        "def test_case_passes():\n    pass\n\n"
        "def test_fix_is_skipped():\n    pytest.skip('not run here')\n"
    )
    record = tmp_path / "corpus.json"
    record.write_text(
        json.dumps(
            {
                "pins": {},
                "cases": [
                    {
                        "id": "case",
                        "relation": "parity",
                        "tracking": None,
                        "evidence": f"{tests}::test_case_passes",
                    }
                ],
                "completed_fixes": [
                    {"issue": "#0", "evidence": f"{tests}::test_fix_is_skipped"}
                ],
            }
        )
    )
    monkeypatch.setattr(corpus, "CORPUS", record)

    assert corpus.synthetic(tmp_path / "out") == 1
    evidence = json.loads((tmp_path / "out" / "synthetic-evidence.json").read_text())
    assert [c["outcome"] for c in evidence["cases"]] == ["passed"]
    assert [f["outcome"] for f in evidence["completed_fixes"]] == ["skipped"]
