"""--region lists as the variant callers read them."""

import pytest

from wgsextract_cli.core.region_selection import region_items, selected_intervals


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
        " chr1:1-5, ,chr1:6-10,", {"chr1": 100}, commas=True, allow_files=False
    ) == {"chr1": [(0, 10)]}
