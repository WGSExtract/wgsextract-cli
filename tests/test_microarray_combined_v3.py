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
