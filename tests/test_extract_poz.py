import subprocess
import sys
import zipfile
from importlib.resources import files
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, sam_to_bam
from wgsextract_cli.commands._extract_poz import poz_bed
from wgsextract_cli.core.utils import WGSExtractError, run_command


@needs_htslib
@pytest.mark.parametrize(
    "name,length,left,right",
    [
        ("chrY", 57_227_415, 2_781_558, 3_049_958),
        ("Y", 59_373_566, 2_649_599, 2_917_999),
        # #1170 (T2T Poz mask): CHM13v2.0 chrY was refused instead of lifted.
        ("chrY", 62_460_029, 2_458_399, 2_568_761),
    ],
)
@pytest.mark.parametrize(
    "command",
    [["ydna-bam", "--poz"], ["region", "--target", "y", "--formats", "bam", "--poz"]],
)
def test_poz_keeps_overlapping_reads_not_other_y_or_mt(
    tmp_path, name, length, left, right, command
):
    lines = [f"@SQ\tSN:{name}\tLN:{length}", "@SQ\tSN:chrM\tLN:16569"]
    for label, pos, cigar, seq in [
        ("before", left, "1M", "A"),
        ("left", left + 1, "1M", "A"),
        ("right", right, "1M", "A"),
        ("after", right + 1, "1M", "A"),
        ("overlap", left, "2M", "AA"),
    ]:
        lines.append(
            f"{label}\t0\t{name}\t{pos}\t60\t{cigar}\t*\t0\t0\t{seq}\t{'I' * len(seq)}"
        )
    lines.append("mito\t0\tchrM\t3500\t60\t1M\t*\t0\t0\tA\tI")
    bam = sam_to_bam(lines, tmp_path / "sample.bam")
    out = tmp_path / "o"
    run_wgsextract(
        "extract",
        *command,
        "--input",
        bam,
        "--outdir",
        str(out),
        "--threads",
        "2",
    )
    output = out / "sample_Y_poz.bam"
    records = run_command(["samtools", "view", output], capture_output=True).stdout
    assert {row.split("\t")[0] for row in records.splitlines()} == {
        "left",
        "right",
        "overlap",
    }
    assert (out / "sample_Y_poz.bam.bai").exists()
    assert not (out / "sample_Y.bam").exists()
    assert not list(out.glob(".poz_*"))


@pytest.mark.parametrize(
    "contigs",
    [
        {"chrY": 1000},
        {"chr1": 248_387_328, "chrY": 57_227_415},
        {"chr1": 248_956_422, "chrY": 62_460_029},
    ],
)
def test_poz_rejects_unknown_and_inconsistent_coordinates(tmp_path, contigs):
    with pytest.raises(WGSExtractError, match="GRCh37/hg19"):
        with poz_bed(contigs, "chrY", str(tmp_path)):
            pytest.fail("must not select a BED")


def test_t2t_poz_mask_keeps_most_of_the_lifted_hg38_regions():
    """#1170: the hg38ToHs1 lift keeps ~11 Mb of sorted, disjoint chrY regions."""
    rows = [
        line.split("\t")
        for line in files("wgsextract_cli")
        .joinpath("assets/reference/poz/t2t.bed")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    regions = [(int(start), int(end)) for _, start, end in rows]
    assert {name for name, _, _ in rows} == {"chrY"}
    assert all(0 <= start < end <= 62_460_029 for start, end in regions)
    assert all(a[1] < b[0] for a, b in zip(regions, regions[1:], strict=False))
    assert 10_000_000 < sum(end - start for start, end in regions) < 12_000_000


@needs_htslib
def test_t2t_poz_keeps_independently_lifted_sites_not_t2t_only_sequence(tmp_path):
    """#1170: pyliftover's hg38ToHs1 lift of hg38 Poz sites, 0-based, across
    regions and reverse-strand chains, must be kept; the T2T-only TSPY array
    insertion and the X-transposed region must not."""
    kept = {
        2_871_024: 2_547_856,
        8_105_125: 7_761_126,
        12_866_956: 13_775_229,
        20_941_141: 21_816_375,
        21_030_000: 21_920_942,  # reverse-strand chain
        22_233_000: 23_189_043,  # reverse-strand chain
        22_288_352: 23_083_897,
        26_538_352: 27_350_292,
    }
    dropped = {"tspy_insertion": 9_300_000, "x_transposed": 4_000_000}
    sites = {f"hg38_{hg38}": t2t for hg38, t2t in kept.items()} | dropped
    lines = ["@SQ\tSN:chrY\tLN:62460029"] + [
        f"{name}\t0\tchrY\t{pos + 1}\t60\t1M\t*\t0\t0\tA\tI"
        for name, pos in sorted(sites.items(), key=lambda item: item[1])
    ]
    bam = sam_to_bam(lines, tmp_path / "sample.bam")
    out = tmp_path / "o"
    run_wgsextract("extract", "ydna-bam", "--poz", "--input", bam, "--outdir", str(out))
    records = run_command(
        ["samtools", "view", out / "sample_Y_poz.bam"], capture_output=True
    ).stdout
    assert {row.split("\t")[0] for row in records.splitlines()} == {
        f"hg38_{hg38}" for hg38 in kept
    }


def test_poz_resources_work_when_package_is_zip_imported(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src" / "wgsextract_cli"
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in [*source.rglob("*.py"), *source.glob("assets/reference/poz/*")]:
            bundle.write(path, path.relative_to(source.parent).as_posix())
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]);"
        "from wgsextract_cli.commands import _extract_poz as p;"
        "assert 'package.zip' in p.__file__;"
        "\nwith p.poz_bed({'Y': 57227415}, 'Y', sys.argv[2]) as bed:"
        "\n    assert Path(bed).read_text().startswith('Y\\t')"
    )
    subprocess.run(
        [sys.executable, "-c", code, str(archive), str(tmp_path)], check=True
    )
