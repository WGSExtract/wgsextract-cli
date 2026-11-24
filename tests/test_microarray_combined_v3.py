"""Tests for keeping CombinedKit v3 alongside CombinedKit v5."""

from wgsextract_cli.core.microarray.combined_v3 import convert_to_combined_v3
from wgsextract_cli.core.microarray.formats import (
    COMBINED_V3_FORMAT,
    resolve_format_key,
)
from wgsextract_cli.core.microarray.identity import MicroarrayIdentity, load_rsid_map
from wgsextract_cli.core.microarray.targets import installed_lifted_chain
from wgsextract_cli.core.microarray.templates import template_resource_paths


def test_combined_v3_filters_combinedkit_v5_to_classic_templates(tmp_path):
    assert resolve_format_key("combined_v3") == COMBINED_V3_FORMAT
    assert resolve_format_key("combined_v5") is None

    templates = tmp_path / "microarray/raw_file_templates"
    body = templates / "body"
    head = templates / "head"
    body.mkdir(parents=True)
    head.mkdir(parents=True)
    (head / "23andMe_V3.txt").write_text("# header\n")
    (body / "23andMe_V3.txt").write_text("rsV3Name\t1\t100\nrsShared\t1\t200\n")
    (body / "MyHeritage_V3.csv").write_text("rsV5Only\t1\t300\n")

    kit_v5 = tmp_path / "sample_CombinedKit.txt"
    kit_v5.write_text(
        "# rsid\tchromosome\tposition\tgenotype\n"
        "rsV2Name\t1\t100\tAG\n"
        "rsShared\t1\t200\tCC\n"
        "rsV5Only\t1\t300\tTT\n"
    )
    out_v3 = tmp_path / "sample_CombinedKit_v3.txt"
    tally = convert_to_combined_v3(str(kit_v5), str(out_v3), str(tmp_path))

    assert out_v3.read_text().splitlines() == [
        "# rsid\tchromosome\tposition\tgenotype",
        "rsV3Name\t1\t100\tAG",
        "rsShared\t1\t200\tCC",
    ]
    assert (tally.sites, tally.called, tally.het) == (2, 2, 1)
    assert body / "23andMe_V3.txt" in template_resource_paths(
        str(tmp_path), COMBINED_V3_FORMAT
    )

    # CHM13 rejected_ids masks a v3 rsID even when the v5 row was called.
    out_masked = tmp_path / "sample_CombinedKit_v3_masked.txt"
    masked_tally = convert_to_combined_v3(
        str(kit_v5), str(out_masked), str(tmp_path), rejected_ids={"rsV3Name"}
    )
    assert out_masked.read_text().splitlines() == [
        "# rsid\tchromosome\tposition\tgenotype",
        "rsV3Name\t1\t100\t--",
        "rsShared\t1\t200\tCC",
    ]
    assert (masked_tally.sites, masked_tally.called, masked_tally.het) == (2, 1, 0)


def test_combined_v3_resolve_rsids_and_offline_chain_lookup(tmp_path):
    # Missing chain returns None locally without touching the network.
    assert installed_lifted_chain(str(tmp_path), "NCBI36") is None

    # Find a real retired/withdrawn rsID in the packaged dbSNP map.
    retired = next(
        rsid for rsid, entry in load_rsid_map().items() if entry.current is None
    )
    templates = tmp_path / "microarray/raw_file_templates"
    body = templates / "body"
    body.mkdir(parents=True)
    (body / "23andMe_V3.txt").write_text(f"{retired}\t1\t100\nrsKept\t1\t200\n")

    kit_v5 = tmp_path / "sample_CombinedKit.txt"
    kit_v5.write_text(
        "# rsid\tchromosome\tposition\tgenotype\n"
        "rsUnlistedV5\t1\t100\tAG\n"
        "rsKept\t1\t200\tCC\n"
    )
    out_v3 = tmp_path / "sample_CombinedKit_v3.txt"
    tally = convert_to_combined_v3(
        str(kit_v5),
        str(out_v3),
        str(tmp_path),
        identity=MicroarrayIdentity(resolve_rsids=True),
    )
    assert out_v3.read_text().splitlines() == [
        "# rsid\tchromosome\tposition\tgenotype",
        f"{retired}\t1\t100\t--",
        "rsKept\t1\t200\tCC",
    ]
    assert (tally.sites, tally.called, tally.het) == (2, 1, 0)


def test_format_aliases_and_removed_or_internal_formats_in_v04_templates():
    import pytest

    from wgsextract_cli.core.microarray.templates import (
        _PACKAGED_TEMPLATES,
        template_rows,
    )
    from wgsextract_cli.core.utils import UsageError

    assert resolve_format_key("ancestry_v2") == "Ancestry_V2ab"
    assert resolve_format_key("Ancestry_V2") == "Ancestry_V2ab"
    assert resolve_format_key("23andme_v4") == "23andMe_V4"
    assert resolve_format_key("ftdna_v2") == "FTDNA_V2"

    for rejected in (
        "23andme_v5_2025",
        "ancestry_v2_2021",
        "ancestry_v2_2024",
        "myheritage_gsa_2025",
        "myheritage_gsa_2025_609424",
        "23andMe_V4_v3",
        "FTDNA_V2_v3",
    ):
        with pytest.raises(UsageError):
            resolve_format_key(rejected)

    # Merged 23andMe_V4 (v4a-c) and FTDNA_V2 (v2a-d) expand their frozen v3 deltas.
    v4_merged = sum(
        1
        for _ in template_rows(
            "23andMe_V4", str(_PACKAGED_TEMPLATES / "body/23andMe_V4.txt.gz")
        )
    )
    v4_frozen = sum(
        1
        for _ in template_rows(
            "23andMe_V4_v3",
            str(_PACKAGED_TEMPLATES / "body/23andMe_V4_v3.txt.delta.gz"),
        )
    )
    f2_merged = sum(
        1
        for _ in template_rows(
            "FTDNA_V2", str(_PACKAGED_TEMPLATES / "body/FTDNA_V2.csv.gz")
        )
    )
    f2_frozen = sum(
        1
        for _ in template_rows(
            "FTDNA_V2_v3", str(_PACKAGED_TEMPLATES / "body/FTDNA_V2_v3.csv.delta.gz")
        )
    )
    assert v4_merged > v4_frozen == 601_885
    assert f2_merged > f2_frozen == 720_449


def test_stale_v03_delta_falls_through_to_packaged_templates(tmp_path):
    """An un-upgraded v0.3.0 library whose delta references a dropped base
    falls back cleanly to the CLI's packaged template instead of crashing."""
    import gzip

    from wgsextract_cli.core.microarray.formats import get_template_format
    from wgsextract_cli.core.microarray.templates import (
        _PACKAGED_TEMPLATES,
        _resolve_templates_for_format,
    )

    body = tmp_path / "microarray/raw_file_templates/body"
    body.mkdir(parents=True)
    with gzip.open(body / "Ancestry_V2cd.txt.delta.gz", "wt") as handle:
        handle.write("#base\tAncestry_V2_2021\n=1\n")

    fmt = get_template_format("Ancestry_V2cd")
    assert fmt is not None
    root, paths = _resolve_templates_for_format(
        [str(tmp_path), str(_PACKAGED_TEMPLATES)], "Ancestry_V2cd", fmt
    )
    assert root == str(_PACKAGED_TEMPLATES)
    assert paths == [str(_PACKAGED_TEMPLATES / "body/Ancestry_V2cd.txt.gz")]
