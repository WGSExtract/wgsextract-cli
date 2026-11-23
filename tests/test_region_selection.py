"""--region lists as the variant callers read them."""

import pytest

from wgsextract_cli.core.region_selection import (
    alignment_intervals,
    region_items,
    selected_intervals,
)


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
