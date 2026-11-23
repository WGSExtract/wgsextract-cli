"""Issue #1171: `lineage y-private` reports candidate private Y SNVs: calls in
the callable mask whose alleles no installed marker set lists, passing strict
filters. Real samtools/bcftools on synthetic chrY data; the marker sets are
synthetic stand-ins for the pinned YBrowse snapshot and YFull module."""

import copy
import hashlib
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, run_wgsextract, sam_to_bam
from tests.test_ybrowse import mini_snapshot
from wgsextract_cli import api
from wgsextract_cli.api import CommandFailed
from wgsextract_cli.core import ybrowse
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.ydna.yleaf_native import trees

pytestmark = needs_htslib

T2T_Y = 62_460_029
# Inside a Poz interval of both the GRCh38 and the T2T mask.
NOVEL, OTHER_ALLELE, YBROWSE_KNOWN, YFULL_KNOWN, LOW_AF, FEW_READS, COMPETING = (
    7_000_011 + 100 * i for i in range(7)
)
OUTSIDE_MASK = 100_011  # PAR1 on GRCh38


def _install_yfull(library: Path, monkeypatch, rows: str) -> None:
    """Pre-install the YFull positions module under test pins (no download)."""
    files = {f"{build}/new_positions.txt": rows.encode() for build in ("hg38", "t2t")}
    meta = copy.deepcopy(trees.MANIFEST["trees"]["yfull"])
    meta["modules"]["positions"]["files"] = {
        name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in files.items()
    }
    monkeypatch.setitem(trees.MANIFEST["trees"], "yfull", meta)
    root = trees.module_root("yfull", "positions", library)
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)


@pytest.fixture
def library(tmp_path, monkeypatch) -> Path:
    library = tmp_path / "library"
    monkeypatch.setitem(settings, "reference_library", str(library))
    ybrowse.install_snapshot(
        library,
        mini_snapshot(
            tmp_path / "ybrowse.vcf",
            [
                f"chrY\t{OTHER_ALLELE}\tSYN_OTHER\tA\tC\t.\t.\tHG=R-O;AA=A\tGT\t0",
                f"chrY\t{YBROWSE_KNOWN}\tSYN_KNOWN\tA\tC\t.\t.\tHG=R-K;AA=A\tGT\t0",
            ],
        ),
    )
    _install_yfull(library, monkeypatch, f"Y\tYF1\tR-YF1\t{YFULL_KNOWN}\tA->G\tA\tG\n")
    return library


def _reads(pos: int, alt: str, alt_reads: int, other: str = "A", others: int = 0):
    """20 bp reads with ``alt``, then ``others`` with ``other``, at chrY:``pos``."""
    seq = "A" * 10 + "{}" + "A" * 9
    return [
        f"r{pos}_{i}\t{16 if i % 2 else 0}\tchrY\t{pos - 10}\t60\t20M\t*\t0\t0"
        f"\t{seq.format(alt if i < alt_reads else other)}\t{'I' * 20}"
        for i in range(alt_reads + others)
    ]


def _hg38_sample(tmp_path) -> tuple[str, str]:
    # A FASTA in another library's genomes folder: the marker sets are still
    # found in the configured library (HG002 validation of #1402).
    # Its unrelated Yleaf tables don't make it the marker library (review).
    reference = tmp_path / "other-library/genomes/ref.fa"
    reference.parent.mkdir(parents=True)
    (tmp_path / "other-library/yleaf/v4.1.6/hg_prediction_tables").mkdir(parents=True)
    with reference.open("w", newline="\n") as stream:
        stream.write(">chrY\n")
        for start in range(0, ybrowse.Y_LENGTH, 100_000):
            stream.write("A" * min(100_000, ybrowse.Y_LENGTH - start) + "\n")
    run_command(["samtools", "faidx", str(reference)])
    lines = [f"@SQ\tSN:chrY\tLN:{ybrowse.Y_LENGTH}"]
    for pos, alt, alt_reads, other, others in (
        (NOVEL, "T", 30, "A", 0),
        (OTHER_ALLELE, "G", 30, "A", 0),
        (YBROWSE_KNOWN, "C", 30, "A", 0),
        (YFULL_KNOWN, "G", 30, "A", 0),
        (LOW_AF, "G", 24, "A", 6),
        (FEW_READS, "C", 6, "A", 0),
        # Review of #1402: a second ALT's reads count against the first's AF.
        (COMPETING, "C", 20, "G", 10),
        (OUTSIDE_MASK, "C", 30, "A", 0),
    ):
        lines += _reads(pos, alt, alt_reads, other, others)
    return str(reference), sam_to_bam(lines, tmp_path / "sample.bam")


def _vcf(
    path: Path, length: int | None, body: list[str], *, chrom: str = "chrY"
) -> str:
    contig = f"##contig=<ID={chrom},length={length}>\n" if length else ""
    path.write_text(
        "##fileformat=VCFv4.2\n" + contig + '##FILTER=<ID=LowQual,Description="q">\n'
        "##FORMAT=<ID=GT,Number=1,Type=String,"
        'Description="g">\n##FORMAT=<ID=AD,Number=R,Type=Integer,Description="d">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tkit\n"
        + "".join(f"{row}\n" for row in body)
    )
    return bgzip_index(path)


def _private(**params):
    return api.lineage.y_private(api.YPrivateParams(**params))


def _table(path: Path) -> list[dict[str, str]]:
    lines = [row for row in path.read_text().splitlines() if not row.startswith("#")]
    header = lines[0].split("\t")
    return [dict(zip(header, row.split("\t"), strict=True)) for row in lines[1:]]


def test_bam_candidates_exclude_known_markers_and_failed_filters(tmp_path, library):
    reference, bam = _hg38_sample(tmp_path)
    result = _private(input=Path(bam), ref=Path(reference), outdir=tmp_path / "out")

    report = tmp_path / "out/y_private/sample.y_private.tsv"
    rows = _table(report)
    assert [(r["pos"], r["alt"], r["note"]) for r in rows] == [
        (str(NOVEL), "T", ""),
        (str(OTHER_ALLELE), "G", "known site, other allele"),
    ]
    assert rows[0]["alt_reads"] == "30" and rows[0]["af"] == "1.0"
    header = report.read_text()
    assert "CANDIDATE" in header and "YBrowse SNP snapshot" in header
    assert "CombBED/McDonald/Poznik" in header and "pseudoautosomal" in header

    # The PAR1 call is outside the mask, so it is never made.
    assert result.details["counts"] == {
        "called": 7,
        "known": 2,
        "failed_filter": 0,
        "failed_qual": 1,
        "failed_alt_reads": 1,
        "failed_af": 1,
        "candidates": 2,
    }
    assert result.metrics["y_private_candidates"] == 2
    assert [s["name"] for s in result.details["marker_sets"]] == [
        "YBrowse SNP snapshot",
        "YFull YTree positions",
    ]
    filters = result.details["filters"]
    assert (filters["min_af"], filters["min_alt_reads"], filters["min_mapq"]) == (
        0.9,
        10,
        20,
    )
    assert result.details["callable_mask"]["bases"] > 11_000_000
    roles = {Path(o.path).name: o.role for o in result.outputs}
    assert roles["sample.y_private.tsv"] == "y_private_candidates"
    assert roles["sample.y_private.vcf.gz"] == "y_private_vcf"
    positions = run_command(
        [
            "bcftools",
            "query",
            "-f",
            "%POS\n",
            str(tmp_path / "out/y_private/sample.y_private.vcf.gz"),
        ],
        capture_output=True,
    ).stdout.split()
    assert positions == [str(NOVEL), str(OTHER_ALLELE)]
    assert not list((tmp_path / "out").glob(".y_private_*"))

    # Review of #1402: without the QUAL gate, the 20 C / 10 G site still fails
    # on AF (0.67): the G reads stay in the denominator.
    relaxed = _private(
        input=Path(bam), ref=Path(reference), outdir=tmp_path / "relaxed", min_qual=0
    )
    assert relaxed.details["counts"]["failed_af"] == 2
    assert COMPETING not in {c["pos"] for c in relaxed.details["candidates"]}


def test_grch38_needs_the_installed_ybrowse_snapshot(tmp_path, monkeypatch):
    library = tmp_path / "library"
    monkeypatch.setitem(settings, "reference_library", str(library))
    _install_yfull(library, monkeypatch, "")
    vcf = _vcf(tmp_path / "kit.vcf", ybrowse.Y_LENGTH, [])
    with pytest.raises(CommandFailed, match="ref ybrowse --install"):
        _private(input=Path(vcf), outdir=tmp_path / "out")


@pytest.mark.parametrize("chrom", ["chrY", "NC_060948.1"])
def test_t2t_vcf_uses_yfull_markers_and_says_ybrowse_is_absent(
    tmp_path, library, chrom
):
    vcf = _vcf(
        tmp_path / "kit.vcf",
        T2T_Y,
        [
            f"{chrom}\t{NOVEL}\t.\tA\tT\t60\tPASS\t.\tGT:AD\t1/1:1,24",
            f"{chrom}\t{YFULL_KNOWN}\t.\tA\tG\t60\tPASS\t.\tGT:AD\t1:0,25",
            f"{chrom}\t{LOW_AF}\t.\tA\tT\t60\tPASS\t.\tGT:AD\t0/1:12,12",
            f"{chrom}\t{FEW_READS}\t.\tA\tC\t60\tLowQual\t.\tGT:AD\t1:0,25",
        ],
        chrom=chrom,
    )
    result = _private(input=Path(vcf), outdir=tmp_path / "out")
    assert [(c["chrom"], c["pos"]) for c in result.details["candidates"]] == [
        (chrom, NOVEL)
    ]
    counts = result.details["counts"]
    assert (counts["known"], counts["failed_af"], counts["failed_filter"]) == (1, 1, 1)
    assert result.details["build"] == "t2t"
    assert [s["name"] for s in result.details["marker_sets"]] == [
        "YFull YTree positions"
    ]
    assert result.details["filters"]["min_mapq"] is None
    assert "known_markers_partial" in {w.code for w in result.warnings}


def test_headerless_vcf_with_two_y_contigs_is_ambiguous(tmp_path, library):
    vcf = _vcf(
        tmp_path / "kit.vcf",
        None,
        [
            f"{chrom}\t{NOVEL}\t.\tA\tT\t60\tPASS\t.\tGT:AD\t1:0,20"
            for chrom in ("chrY", "Y")
        ],
    )
    with pytest.raises(CommandFailed, match="exactly one primary Y contig"):
        _private(input=Path(vcf), outdir=tmp_path / "out", build="t2t")


def test_many_candidates_raise_the_qc_warning(tmp_path, library):
    body = [
        f"chrY\t{7_000_000 + 10 * i}\t.\tA\tT\t60\tPASS\t.\tGT:AD\t1:0,20"
        for i in range(60)
    ]
    vcf = _vcf(tmp_path / "kit.vcf", None, body)
    result = _private(input=Path(vcf), outdir=tmp_path / "out", build="t2t")
    assert result.metrics["y_private_candidates"] == 60
    warning = next(w for w in result.warnings if w.code == "y_private_excess")
    assert warning.details["count"] == 60


@pytest.mark.parametrize(
    "length,build,error",
    [
        (59_373_566, None, "GRCh37/hg19"),
        (12_345, None, "Unknown or inconsistent Y assembly"),
        (None, None, "pass --build"),
        (T2T_Y, "hg38", "contradicts"),
    ],
)
def test_unsupported_or_unknown_builds_are_refused(
    tmp_path, library, length, build, error
):
    vcf = _vcf(
        tmp_path / "kit.vcf",
        length,
        [f"chrY\t{NOVEL}\t.\tA\tT\t60\t.\t.\tGT:AD\t1:0,20"],
    )
    with pytest.raises(CommandFailed, match=error):
        _private(input=Path(vcf), outdir=tmp_path / "out", build=build)


def test_vcf_without_allele_depths_is_refused(tmp_path, library):
    plain = tmp_path / "kit.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n"
        f"##contig=<ID=chrY,length={T2T_Y}>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="g">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tkit\n"
        f"chrY\t{NOVEL}\t.\tA\tT\t60\t.\t.\tGT\t1\n"
    )
    with pytest.raises(CommandFailed, match="FORMAT/AD"):
        _private(input=Path(bgzip_index(plain)), outdir=tmp_path / "out")


def test_missing_yfull_markers_need_download_consent(tmp_path, monkeypatch):
    monkeypatch.setitem(settings, "reference_library", str(tmp_path / "library"))
    monkeypatch.setitem(settings, "allow_reference_downloads", False)
    vcf = _vcf(tmp_path / "kit.vcf", T2T_Y, [])
    with pytest.raises(CommandFailed, match="--download") as refused:
        _private(input=Path(vcf), outdir=tmp_path / "out")
    assert refused.value.exit_code == 2


def test_incomplete_or_shared_allele_depths_fail_the_af_filter(tmp_path, library):
    """Review of #1402: a missing REF depth, or reads split between two ALT
    alleles, must not pass as AF 1.0."""
    vcf = _vcf(
        tmp_path / "kit.vcf",
        T2T_Y,
        [
            f"chrY\t{NOVEL}\t.\tA\tT\t60\tPASS\t.\tGT:AD\t1:.,12",
            f"chrY\t{LOW_AF}\t.\tA\tG,T\t60\tPASS\t.\tGT:AD\t1/2:0,15,15",
        ],
    )
    result = _private(input=Path(vcf), outdir=tmp_path / "out")
    assert result.details["candidates"] == []
    assert result.details["counts"]["failed_af"] == 3


def test_marker_sets_come_from_the_ref_library(tmp_path, library, monkeypatch):
    """Review of #1402: --ref naming a library selects its marker sets."""
    monkeypatch.setitem(settings, "reference_library", str(tmp_path / "other"))
    row = f"chrY\t{YBROWSE_KNOWN}\t.\tA\tC\t60\tPASS\t.\tGT:AD\t1:0,20"
    vcf = _vcf(tmp_path / "kit.vcf", ybrowse.Y_LENGTH, [row])
    result = _private(input=Path(vcf), outdir=tmp_path / "out", ref=library)
    assert result.details["counts"]["known"] == 1


def test_a_sample_recorded_as_female_is_refused(tmp_path, library):
    vcf = _vcf(tmp_path / "kit.vcf", T2T_Y, [])
    run_wgsextract("sample", "set", "--input", vcf, "--sex", "Female")
    with pytest.raises(CommandFailed, match="female"):
        _private(input=Path(vcf), outdir=tmp_path / "out")


def test_reads_of_several_samples_are_refused(tmp_path, library):
    """Review of #1402: mpileup would call each read group's sample apart."""
    lines = [
        f"@SQ\tSN:chrY\tLN:{ybrowse.Y_LENGTH}",
        "@RG\tID:a\tSM:father",
        "@RG\tID:b\tSM:son",
        *_reads(NOVEL, "T", 2),
    ]
    bam = sam_to_bam(lines, tmp_path / "pair.bam")
    with pytest.raises(CommandFailed, match="read groups name 2"):
        _private(input=Path(bam), outdir=tmp_path / "out")


def test_candidate_vcf_holds_only_the_reported_alleles(tmp_path, library):
    """Review of #1402: a multiallelic record exports only its candidate ALT,
    with AD still summing every allele's reads."""
    row = f"chrY\t{YFULL_KNOWN}\t.\tA\tT,G\t60\tPASS\t.\tGT:AD\t1/2:0,19,1"
    vcf = _vcf(tmp_path / "kit.vcf", T2T_Y, [row])
    result = _private(input=Path(vcf), outdir=tmp_path / "out")
    assert [(c["alt"], c["af"]) for c in result.details["candidates"]] == [("T", 0.95)]
    records = run_command(
        [
            "bcftools",
            "query",
            "-f",
            "%POS\t%ALT[\t%AD]\n",
            str(tmp_path / "out/y_private/kit.y_private.vcf.gz"),
        ],
        capture_output=True,
    ).stdout.splitlines()
    assert records == [f"{YFULL_KNOWN}\tT\t1,19"]
