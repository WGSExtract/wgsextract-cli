"""`typing str` on Nanopore and PacBio reads (#615): real LongTR and samtools
on a small GRCh38-headed genome with an HTT-like CAG repeat."""

import gzip
import hashlib
import json
import random
from importlib.resources import files
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, run_wgsextract, sam_to_bam, write_fasta
from wgsextract_cli import api
from wgsextract_cli.commands.genotyping import long_read_repeats, repeats
from wgsextract_cli.commands.genotyping.repeats import THRESHOLDS
from wgsextract_cli.core.pixi_tools import pixi_env_tool
from wgsextract_cli.core.utils import UsageError

pytestmark = needs_tools("samtools", "bcftools")
needs_longtr = pytest.mark.skipif(
    not pixi_env_tool("LongTR"), reason="pixi install -e longtr"
)

_RNG = random.Random(615)
FLANK = "".join(_RNG.choice("ACGT") for _ in range(12_000))
# LongTR only reads a repeat several kb from the contig's ends.
PAD, LEFT, RIGHT = FLANK[:5000], FLANK[5000:6000], FLANK[6000:7000]
TAIL = FLANK[7000:]
GENOME = PAD + LEFT + "CAG" * 20 + RIGHT + TAIL  # Repeat: chr4:6001-6060.
CONTIGS = {"chr1": 248_956_422, "chr4": len(GENOME)}
CATALOG_ROW = "chr4\t6001\t6060\tCAG\tHTT\n"


def _read(name: str, units: int, clip: int = 0, hard: int = 0) -> str:
    """A 2 kb read over the repeat with ``units`` CAGs; with ``clip``, its
    first ``clip`` bases (an expansion's worth) are soft-clipped at the
    repeat, as aligners do with expansions longer than they place."""
    if clip:
        seq = "CAG" * (clip // 3) + RIGHT
        cigar = (f"{hard}H" if hard else "") + f"{clip // 3 * 3}S{len(RIGHT)}M"
        return f"{name}\t0\tchr4\t6061\t60\t{cigar}\t*\t0\t0\t{seq}\t{'I' * len(seq)}"
    seq = LEFT + "CAG" * units + RIGHT
    extra = 3 * (units - 20)
    cigar = f"1060M{extra}I{len(RIGHT)}M" if extra else f"{len(seq)}M"
    return f"{name}\t0\tchr4\t5001\t60\t{cigar}\t*\t0\t0\t{seq}\t{'I' * len(seq)}"


def _sample(tmp_path: Path, reads: list[str], platform="ONT", ds="") -> Path:
    header = ["@HD\tVN:1.6\tSO:unsorted"]
    header += [f"@SQ\tSN:{name}\tLN:{length}" for name, length in CONTIGS.items()]
    header.append(
        f"@RG\tID:rg\tSM:s\tLB:s\tPL:{platform}" + (f"\tDS:{ds}" if ds else "")
    )
    reads = [f"{read}\tRG:Z:rg" for read in reads]
    (tmp_path / "in dir").mkdir(exist_ok=True)
    return Path(sam_to_bam(header + reads, tmp_path / "in dir" / "s.bam"))


@pytest.fixture
def ref(tmp_path):
    return write_fasta(tmp_path / "ref" / "ref.fa", {"chr4": GENOME})


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.bed"
    path.write_text(CATALOG_ROW)
    return path


def _run(bam, ref, catalog, outdir, **extra):
    return api.genotyping.repeats(
        api.TypingStrParams(
            input=bam, ref=ref, catalog=catalog, outdir=outdir, sex="female",
            threads=2, **extra,
        )
    )  # fmt: skip


def _summary(outdir: Path) -> dict[str, list[str]]:
    lines = (outdir / "s.str.tsv").read_text().splitlines()
    return {row[0]: row for row in (line.split("\t") for line in lines[1:])}


@needs_longtr
def test_nanopore_reads_go_to_longtr_and_an_expanded_htt_is_pathogenic(
    tmp_path, ref, catalog
):
    reads = [_read(f"n{i}", 20) for i in range(15)]
    reads += [_read(f"e{i}", 45) for i in range(15)]
    out = tmp_path / "out dir"
    result = _run(_sample(tmp_path, reads), ref, catalog, out)

    row = _summary(out)["HTT"]
    assert (row[4], row[5], row[8]) == ("20/45", "-", "pathogenic")
    assert result.metrics["repeat_caller"] == "longtr"
    assert result.metrics["flagged_repeats"] == 1
    assert [w.code for w in result.warnings] == ["repeat_outside_normal_range"]
    roles = {Path(o.path).name: o.role for o in result.outputs}
    assert roles["s.str.vcf.gz"] == "repeat_calls"
    assert roles["s.str.tsv"] == "repeat_summary"


@needs_longtr
def test_reads_clipped_at_a_repeat_flag_an_expansion_longtr_cannot_size(
    tmp_path, ref, catalog
):
    """#615: PacBio PureTarget DMPK/FMR1/FXN expansions of 1-3 kb were
    soft-clipped by the aligner and called normal or not at all."""
    reads = [_read(f"n{i}", 20) for i in range(15)]
    reads += [_read(f"c{i}", 0, clip=900) for i in range(10)]
    out = tmp_path / "out"
    result = _run(_sample(tmp_path, reads, platform="PACBIO"), ref, catalog, out)

    status = _summary(out)["HTT"][8]
    assert status.startswith("normal (10 reads clipped at the repeat")
    assert [w.details["locus"] for w in result.warnings] == ["HTT"]


# LongTR's HG002 ONT DMPK record (out/615/full/longtr), ALLREADS shortened.
HG002_DMPK = (
    "chr19\t45770205\tDMPK\t" + "CAG" * 20 + "CATT\t" + "CAG" * 11 + "CATT,"
    + "CAG" * 11 + "CAATTCCCTTGCAGCCCCGTCGGGATG\t.\t.\tSTART=45770205;"
    "END=45770264;MOTIF=CAG;PERIOD=3;DP=28\tGT:GB:DP:ALLREADS:MALLREADS\t"
    "1|2:-27|-4:28:-27|20;-4|1:-27|27;-4|1\n"
)  # fmt: skip


def test_longtr_one_read_alleles_are_low_support(tmp_path):
    """#643 review: LongTR called HG002's DMPK 11/19 (truth 11/11), the 19
    from one read of 28."""
    vcf = tmp_path / "dmpk.vcf.gz"
    with gzip.open(vcf, "wt") as f:
        f.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG002\n")
        f.write(HG002_DMPK)
    [call] = long_read_repeats.parse_longtr(str(vcf))
    assert (call.alleles, call.allele_reads) == ((11, 19), (27, 1))
    summary = tmp_path / "s.tsv"
    repeats.write_summary(str(summary), [call])
    row = summary.read_text().splitlines()[1].split("\t")
    assert (row[8], row[10]) == ("normal", "27/1 (low support)")


@needs_longtr
def test_hard_and_soft_clips_together_count_as_one_clip(tmp_path, ref, catalog):
    """Copilot on #643: 10H100S reads were never counted as clipped."""
    reads = [_read(f"n{i}", 20) for i in range(15)]
    reads += [_read(f"c{i}", 0, clip=60, hard=60) for i in range(10)]
    out = tmp_path / "out"
    _run(_sample(tmp_path, reads, platform="PACBIO"), ref, catalog, out)
    assert "10 reads clipped" in _summary(out)["HTT"][8]


@needs_longtr
def test_a_comma_in_the_name_and_catalog_comments_are_handled(tmp_path, ref):
    """Copilot on #643: LongTR split a "s,1" sample name at the comma, and
    comment lines in a --catalog broke the summary."""
    catalog = tmp_path / "catalog.bed"
    catalog.write_text("# my repeats\n\n" + CATALOG_ROW)
    bam = _sample(tmp_path, [_read(f"n{i}", 20) for i in range(15)])
    renamed = bam.with_name("s,1.bam")
    bam.rename(renamed)
    Path(f"{bam}.bai").rename(f"{renamed}.bai")
    out = tmp_path / "out"
    _run(renamed, ref, catalog, out)
    row = (out / "s,1.str.tsv").read_text().splitlines()[1].split("\t")
    assert (row[0], row[4], row[8]) == ("HTT", "20/20", "normal")


@needs_longtr
def test_a_bad_catalog_fails_before_sex_inference(tmp_path, ref, caplog):
    bam = _sample(tmp_path, [_read("r", 20)])
    with pytest.raises(api.CommandFailed, match="Repeat catalog not found"):
        api.genotyping.repeats(
            api.TypingStrParams(input=bam, ref=ref, catalog=tmp_path / "none.bed",
                                outdir=tmp_path / "out", caller="longtr")
        )  # fmt: skip
    assert "Sample sex" not in caplog.text


@needs_longtr
def test_r9_nanopore_reads_warn_that_longtr_drops_them(tmp_path, ref, catalog):
    reads = [_read(f"n{i}", 20) for i in range(12)]
    bam = _sample(tmp_path, reads, ds="basecall_model=dna_r9.4.1_e8_sup@v3.3")
    result = _run(bam, ref, catalog, tmp_path / "out")
    assert "r9_reads" in {w.code for w in result.warnings}


@pytest.mark.parametrize(
    ("platform", "caller", "advice"),
    [("ONT", "expansionhunter", "--caller longtr"),
     ("ILLUMINA", "longtr", "--caller expansionhunter")],
)  # fmt: skip
def test_a_caller_for_the_other_read_type_is_a_usage_error(
    tmp_path, ref, catalog, platform, caller, advice
):
    bam = _sample(tmp_path, [_read("r", 20)], platform=platform)
    with pytest.raises(UsageError, match=advice):
        run_wgsextract("typing", "str", "-i", str(bam), "--ref", str(ref),
                       "--catalog", str(catalog), "--caller", caller)  # fmt: skip
    assert not list((tmp_path / "in dir").glob("s.str.*"))


def test_mixed_platform_reads_need_an_explicit_caller(tmp_path, ref, catalog):
    """#652: ONT and Illumina read groups in one BAM defaulted to ExpansionHunter."""
    mixed = "ONT\n@RG\tID:ill\tSM:s\tLB:s\tPL:ILLUMINA"
    bam = _sample(tmp_path, [_read("r", 20)], platform=mixed)
    with pytest.raises(UsageError, match="--caller") as error:
        run_wgsextract("typing", "str", "-i", str(bam), "--ref", str(ref),
                       "--catalog", str(catalog))  # fmt: skip
    assert "illumina, ont" in str(error.value)
    result = api.genotyping.repeats(
        api.TypingStrParams(
            input=bam, ref=ref, catalog=catalog, outdir=tmp_path / "out",
            sex="female", caller="expansionhunter",
        ),
        check=False,
    )  # fmt: skip
    assert result.error is None or "platforms" not in result.error.message


CATALOGS = files("wgsextract_cli.commands.genotyping").joinpath("str_catalog")


@pytest.mark.parametrize("build", ["hg38", "hg19"])
@pytest.mark.parametrize("caller", ["longtr", "strkit"])
def test_bundled_catalogs_hold_every_disease_repeat_their_build_has(build, caller):
    """Only hg19 lacks a repeat: NOTCH2NLC, which GRCh37 doesn't hold (#643
    review); `typing str` reports it as unavailable."""
    text = CATALOGS.joinpath(f"{build}.{caller}.bed").read_text()
    ids = {
        line.split("\t")[4]
        if caller == "longtr"
        else line.split("ID=")[1].split(";")[0]
        for line in text.splitlines()
    }
    assert set(THRESHOLDS) - ids == ({"NOTCH2NL"} if build == "hg19" else set())


def test_nipa1_has_one_motif_per_build_on_its_reference_strand():
    """#643 review: GRCh38 inverted NIPA1's region, so (GCG)n on hg38 reads
    (CGC)n on hg19; no wrong-strand STRchive motif is added."""
    motifs = {
        build: next(
            line.split("\t")[3]
            for line in CATALOGS.joinpath(f"{build}.longtr.bed")
            .read_text()
            .splitlines()
            if line.endswith("\tNIPA1")
        )
        for build in ("hg19", "hg38")
    }
    assert motifs == {"hg19": "CGC", "hg38": "GCG"}


def test_bundled_catalogs_match_their_recorded_provenance():
    provenance = json.loads(CATALOGS.joinpath("provenance.json").read_text())
    for name, digest in provenance["files"].items():
        data = CATALOGS.joinpath(name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name
    assert all(s["sha256"] and s["license"] for s in provenance["sources"].values())
