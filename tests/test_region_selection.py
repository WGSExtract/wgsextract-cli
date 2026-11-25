"""--region lists as the variant callers read them."""

import pytest

from tests.htslib_helpers import needs_htslib, sam_to_bam, write_fasta
from wgsextract_cli.commands.vcf import structural
from wgsextract_cli.core.region_selection import (
    alignment_intervals,
    region_items,
    selected_intervals,
)
from wgsextract_cli.core.utils import run_command


@needs_htslib
def test_region_bam_1250_is_indexable_in_reference_contig_order(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "A" * 50, "chr2": "C" * 50})
    bam = sam_to_bam(
        [
            "@HD\tVN:1.6\tSO:coordinate",
            "@SQ\tSN:chr2\tLN:50",
            "@SQ\tSN:chr1\tLN:50",
            "r2\t0\tchr2\t1\t60\t4M\t*\t0\t0\tCCCC\tIIII",
            "r1\t0\tchr1\t1\t60\t4M\t*\t0\t0\tAAAA\tIIII",
        ],
        tmp_path / "reverse.bam",
    )
    extracted = str(tmp_path / "selected.bam")
    structural._extract_region_bam_with_reference_header(
        bam, {"chr2": [(0, 50)], "chr1": [(0, 50)]}, str(ref), extracted
    )
    run_command(["samtools", "index", extracted])
    reads = run_command(["samtools", "view", extracted], capture_output=True).stdout
    assert [row.split("\t")[2] for row in reads.splitlines()] == ["chr1", "chr2"]


@pytest.mark.parametrize(
    ("region", "interval"),
    [
        ("chr20", (0, 5000)),
        ("chr20:1,001-2,000", (1000, 2000)),
        ("chr20:4001", (4000, 5000)),
    ],
)
def test_sv_1250_preserves_single_region_coordinates(region, interval):
    lengths = {"chr20": 5000}
    assert alignment_intervals(region, lengths, lengths) == {"chr20": [interval]}


def test_sv_1250_rejects_unknown_contig():
    lengths = {"chr20": 5000}
    with pytest.raises(ValueError, match="chrX"):
        alignment_intervals("chrX:1-5", lengths, lengths)


@pytest.mark.parametrize(("separator", "commas"), [(" ", False), (",", True)])
def test_long_region_list_is_not_file_name_too_long_977(separator, commas):
    # Issue #977: a region list longer than a path (as test_38's DeepVariant
    # run passes for every contig) raised "[Errno 36] File name too long".
    items = [f"chr1:{s}-{s + 9}" for s in range(1, 5001, 10)]
    region = separator.join(items)
    assert len(region) > 4096
    assert region_items(region, commas=commas) == items
    assert selected_intervals(region, {"chr1": 10_000}, commas=commas) == {
        "chr1": [(0, 5000)]
    }


def test_compare_1295_literal_evidence_regions_allow_empty_comma_items():
    assert selected_intervals(
        " chr1:1-5, ,chr1:6-10,", {"chr1": 100}, commas=True, files=False
    ) == {"chr1": [(0, 10)]}
