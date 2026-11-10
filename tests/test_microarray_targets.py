"""The genome-wide microarray target built from the chip templates."""

import gzip
import subprocess
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools
from wgsextract_cli.core import microarray_targets as mt
from wgsextract_cli.core.dependencies import get_tool_path


def _templates(root: Path) -> Path:
    body = root / "microarray" / "raw_file_templates" / "body"
    body.mkdir(parents=True)
    (body / "23andMe_V3.txt").write_text(
        "rs1\t1\t1000\t\nrs2\t2\t2000\t\nrs3\tX\t3000\t\n"
    )
    # MyHeritage: quoted CSV; rs1's position again (one marker, first rsID kept).
    (body / "MyHeritage_V2.csv").write_text('"rs9","1","1000"\n"rs4","MT","73"\n')
    # 23andMe's API list puts the rsID last.
    (body / "23andMe_SNPs_API.txt").write_text("chr7\t5000\trs5\n")
    # FTDNA's Affymetrix export is NCBI36: only its lifted positions are targets.
    (body / "FTDNA_V1_Affy.csv").write_text("rs6,3,999999\n")
    (root / _HG18_CHAIN_NAME).write_bytes(gzip.compress(_HG18_CHAIN.encode()))
    return root / "microarray"


# NCBI36 chr3:999000-1000000 sits 1000 bp later on GRCh37.
_HG18_CHAIN_NAME = "hg18ToHg19.over.chain.gz"
_HG18_CHAIN = "chain 1000 chr3 2000000 + 999000 1000000 chr3 2000000 + 1000000 1001000 1\n1000\n\n"
_VERSION = "##wgsextract_microarray_target_version=3"


def test_markers_are_the_union_of_the_grch37_templates(tmp_path):
    markers = mt.collect_template_markers([str(_templates(tmp_path))])
    assert markers == {
        ("chr1", 1000): "rs1",
        ("chr2", 2000): "rs2",
        ("chrX", 3000): "rs3",
        ("chrM", 73): "rs4",
        ("chr7", 5000): "rs5",
    }


def test_unplaced_export_markers_are_not_genotyping_targets(tmp_path):
    templates = _templates(tmp_path)
    (templates / "raw_file_templates/body/Ancestry_V2_2021.txt").write_text(
        "rs77\t20\t0\nrs78\t20\t12345\n"
    )
    markers = mt.collect_template_markers([str(templates)])
    assert ("chr20", 0) not in markers
    assert markers[("chr20", 12345)] == "rs78"


def test_ncbi36_markers_are_lifted_to_grch37(tmp_path):
    templates = _templates(tmp_path)
    markers = mt.collect_template_markers(
        [str(templates)], str(tmp_path / _HG18_CHAIN_NAME)
    )
    assert markers[("chr3", 1000999)] == "rs6"
    assert ("chr3", 999999) not in markers


@needs_tools("bgzip", "tabix")
def test_hg19_target_is_written_sorted_and_indexed(tmp_path):
    templates = _templates(tmp_path)
    out = mt.build_genome_wide_target(str(tmp_path), "GRCh37", [str(templates)])

    assert out == str(tmp_path / "microarray" / "All_SNPs_hg19_ref.tab.gz")
    assert Path(out + ".tbi").is_file()
    with gzip.open(out, "rt") as f:
        lines = f.read().splitlines()
    assert lines == [
        _VERSION,
        "#CHROM\tPOS\tID",
        "chr1\t1000\trs1",
        "chr2\t2000\trs2",
        "chr3\t1000999\trs6",
        "chr7\t5000\trs5",
        "chrM\t73\trs4",
        "chrX\t3000\trs3",
    ]
    region = subprocess.run(
        [get_tool_path("tabix"), out, "chr2"], capture_output=True, text=True
    )
    assert region.stdout == "chr2\t2000\trs2\n"


@needs_tools("bgzip", "tabix")
def test_version2_target_adds_new_layouts_without_changing_existing_ids(
    tmp_path, monkeypatch
):
    templates = _templates(tmp_path)
    with monkeypatch.context() as previous:
        previous.setattr(mt, "TARGET_VERSION", 2)
        previous.setattr(
            mt, "_VERSION_LINE", "##wgsextract_microarray_target_version=2\n"
        )
        target = mt.build_genome_wide_target(str(tmp_path), "hg19", [str(templates)])
    (templates / "raw_file_templates/body/MyHeritage_GSA_2025.csv").write_text(
        "rs77\t4\t12345\n"
    )
    (templates / "raw_file_templates/body/Ancestry_V2_2021.txt").write_text(
        "rs78\t26\t73\n"
    )
    mt.build_genome_wide_target(str(tmp_path), "hg19", [str(templates)])
    with gzip.open(target, "rt") as stream:
        text = stream.read()
    assert "chr4\t12345\trs77\n" in text
    assert "chrM\t73\trs4\n" in text
    assert "chrM\t73\trs78\n" not in text


def test_no_templates_says_to_bootstrap(tmp_path):
    with pytest.raises(mt.WGSExtractError, match="ref bootstrap"):
        mt.build_genome_wide_target(str(tmp_path), "hg38", [str(tmp_path)])


# hg19 chr1:0-5000 sits 100 bp later on hg38; chr2:0-5000 maps onto an alt contig.
_CHAIN = (
    "chain 1000 chr1 10000 + 0 5000 chr1 10100 + 100 5100 1\n5000\n\n"
    "chain 900 chr2 10000 + 0 5000 chr2_KI270767v1_alt 10000 + 0 5000 2\n5000\n\n"
)


def test_lift_to_hg38_offsets_and_drops_unmappable(tmp_path):
    chain = tmp_path / "hg19ToHg38.over.chain"
    chain.write_text(_CHAIN)
    lifted = mt._lift_to_hg38(
        {
            ("chr1", 1000): "rs1",  # shifted by 100
            ("chr1", 9000): "rs2",  # outside every chain block
            ("chr2", 2000): "rs3",  # lands on an alt contig
        },
        str(chain),
    )
    assert lifted == {("chr1", 1100): "rs1"}


@needs_tools("bgzip", "tabix")
def test_hg38_target_uses_the_librarys_chain(tmp_path):
    templates = _templates(tmp_path)
    (tmp_path / "hg19ToHg38.over.chain.gz").write_bytes(gzip.compress(_CHAIN.encode()))
    out = mt.build_genome_wide_target(str(tmp_path), "hg38", [str(templates)])
    with gzip.open(out, "rt") as f:
        # chrM keeps its positions: GRCh37's MT and hg38's chrM are both the rCRS.
        assert f.read().splitlines() == [
            _VERSION,
            "#CHROM\tPOS\tID",
            "chr1\t1100\trs1",
            "chrM\t73\trs4",
        ]


@needs_tools("bgzip", "tabix")
def test_a_target_from_an_older_builder_is_rebuilt(tmp_path):
    templates = _templates(tmp_path)
    out = Path(mt.genome_wide_target_path(str(tmp_path), "hg19"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(gzip.compress(b"#CHROM\tPOS\tID\nchr1\t1\trsOld\n"))
    Path(str(out) + ".tbi").write_bytes(b"")

    mt.build_genome_wide_target(str(tmp_path), "GRCh37", [str(templates)])

    with gzip.open(out, "rt") as f:
        lines = f.read().splitlines()
    assert lines[0] == _VERSION
    assert "chr1\t1\trsOld" not in lines


@needs_tools("bgzip", "tabix")
def test_a_current_target_is_reused(tmp_path):
    templates = _templates(tmp_path)
    out = mt.build_genome_wide_target(str(tmp_path), "hg19", [str(templates)])
    mtime = Path(out).stat().st_mtime_ns
    (templates / "raw_file_templates" / "body" / "23andMe_V3.txt").write_text(
        "rsNew\t1\t1\t\n"
    )

    assert mt.build_genome_wide_target(str(tmp_path), "hg19", [str(templates)]) == out
    assert Path(out).stat().st_mtime_ns == mtime


@needs_tools("bgzip", "tabix")
def test_simultaneous_builds_leave_one_valid_target(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    templates = str(_templates(tmp_path))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: mt.build_genome_wide_target(
                    str(tmp_path), "hg19", [templates]
                ),
                range(4),
            )
        )
    out = str(tmp_path / "microarray" / "All_SNPs_hg19_ref.tab.gz")
    assert results == [out] * 4
    region = subprocess.run(
        [get_tool_path("tabix"), out, "chr1"], capture_output=True, text=True
    )
    assert region.stdout == "chr1\t1000\trs1\n"
    leftovers = {p.name for p in (tmp_path / "microarray").iterdir()} - {
        "raw_file_templates",
        "All_SNPs_hg19_ref.tab.gz",
        "All_SNPs_hg19_ref.tab.gz.tbi",
        "All_SNPs_hg19_ref.tab.gz.lock.guard",
    }
    assert not leftovers


@needs_tools("bgzip", "tabix")
def test_a_current_target_in_a_read_only_library_is_used(tmp_path):
    templates = _templates(tmp_path)
    out = mt.build_genome_wide_target(str(tmp_path), "hg19", [str(templates)])
    microarray_dir = Path(out).parent
    microarray_dir.chmod(0o555)
    try:
        assert (
            mt.build_genome_wide_target(str(tmp_path), "hg19", [str(templates)]) == out
        )
    finally:
        microarray_dir.chmod(0o755)


@pytest.fixture
def chain_servers(monkeypatch):
    """A primary (UCSC) and a mirror server for the hg19ToHg38 chain, each
    serving the genuine bytes until a test breaks one of them."""
    import hashlib

    from tests.http_server import RangeServer
    from wgsextract_cli.core.ref_library import downloads

    data = gzip.compress(_CHAIN.encode())
    name = mt.HG19_TO_HG38_CHAIN.name
    primary = RangeServer(None, {name: data})
    mirror = RangeServer(None, {name: data})
    monkeypatch.setattr(downloads, "curl_progress_args", lambda: ["--silent"])
    monkeypatch.setattr(downloads.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        mt,
        "HG19_TO_HG38_CHAIN",
        mt.ChainSource(
            name,
            (primary.url_for(name), mirror.url_for(name)),
            hashlib.sha256(data).hexdigest(),
        ),
    )
    yield primary, mirror, data
    primary.close()
    mirror.close()


def test_chain_comes_from_ucsc_first_without_touching_the_mirror(
    tmp_path, chain_servers
):
    primary, mirror, data = chain_servers
    chain = mt.hg19_to_hg38_chain(str(tmp_path))
    assert Path(chain).read_bytes() == data
    assert primary.gets() and not mirror.requests


def test_issue_667_chain_falls_back_to_the_mirror_when_ucsc_fails(
    tmp_path, chain_servers
):
    primary, mirror, data = chain_servers
    primary.statuses[mt.HG19_TO_HG38_CHAIN.name] = 503
    chain = mt.hg19_to_hg38_chain(str(tmp_path))
    assert Path(chain).read_bytes() == data
    assert mirror.gets()


def test_a_chain_with_the_wrong_checksum_is_rejected_for_the_mirrors(
    tmp_path, chain_servers
):
    primary, mirror, data = chain_servers
    primary.files[mt.HG19_TO_HG38_CHAIN.name] = gzip.compress(b"tampered\n")
    chain = mt.hg19_to_hg38_chain(str(tmp_path))
    assert Path(chain).read_bytes() == data


def test_no_chain_is_installed_when_no_source_has_the_pinned_checksum(
    tmp_path, chain_servers
):
    primary, mirror, _ = chain_servers
    name = mt.HG19_TO_HG38_CHAIN.name
    primary.files[name] = mirror.files[name] = gzip.compress(b"tampered\n")
    with pytest.raises(mt.WGSExtractError, match="Could not download"):
        mt.hg19_to_hg38_chain(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == []
