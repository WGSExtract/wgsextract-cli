"""The genome-wide microarray target built from the chip templates."""

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.microarray import targets as mt


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
_VERSION = f"##wgsextract_microarray_target_version={mt.TARGET_VERSION}"


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


def test_issue_1329_target_keeps_corrections_scoped_to_their_template(
    tmp_path: Path,
) -> None:
    templates = _templates(tmp_path)
    body = templates / "raw_file_templates/body"
    (body / "23andMe_V4_1.txt").write_text("rs397507452\tMT\t7444\n")
    (body / "23andMe_V4_2.txt").write_text("# No additional markers.\n")
    (body / "Ancestry_V2_2021.txt").write_text(
        "rs397507452\t26\t7444\nrsLater\tchrM\t7443\n"
    )

    assert mt.collect_template_markers(
        [str(templates)], formats=["23andMe_V4", "Ancestry_V2_2021"]
    ) == {("chrM", 7443): "rs397507452", ("chrM", 7444): "rs397507452"}


def test_placeholder_ids_yield_to_real_ids_or_get_their_own(tmp_path):
    templates = _templates(tmp_path)
    body = templates / "raw_file_templates/body"
    (body / "TellMeGen_V1.csv").write_text(".\t5\t500\n.\t4\t4000\n.\t4\t4100\n")
    # Later in the template order than tellmeGen.
    (body / "SelfDecode_V1.txt").write_text("rs50\t5\t500\n")
    markers = mt.collect_template_markers([str(templates)])
    assert markers[("chr5", 500)] == "rs50"
    assert markers[("chr4", 4000)] == "chr4_4000"
    assert markers[("chr4", 4100)] == "chr4_4100"


def test_ncbi36_markers_are_lifted_to_grch37(tmp_path):
    templates = _templates(tmp_path)
    markers = mt.collect_template_markers(
        [str(templates)], {"NCBI36": str(tmp_path / _HG18_CHAIN_NAME)}
    )
    assert markers[("chr3", 1000999)] == "rs6"
    assert ("chr3", 999999) not in markers


def test_grch38_y_markers_are_lifted_to_grch37(tmp_path):
    templates = _templates(tmp_path)
    (templates / "raw_file_templates/body/FTDNA_V2Y.csv").write_text("Z57\tY\t2500\n")
    # GRCh38 chrY:2000-3000 sits 500 bp later on GRCh37.
    chain = tmp_path / "hg38ToHg19.over.chain"
    chain.write_text(
        "chain 1000 chrY 9000 + 2000 3000 chrY 9000 + 2500 3500 1\n1000\n\n"
    )
    assert mt.lifted_builds([str(templates)]) == {"NCBI36", "GRCh38"}
    markers = mt.collect_template_markers(
        [str(templates)],
        {"NCBI36": str(tmp_path / _HG18_CHAIN_NAME), "GRCh38": str(chain)},
    )
    assert markers[("chrY", 3000)] == "Z57"
    # Without its chain, a GRCh38 template contributes nothing.
    assert ("chrY", 3000) not in mt.collect_template_markers([str(templates)])


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
@pytest.mark.parametrize("changed", ["template", "chain"])
def test_issue_1043_target_rebuilds_after_preserved_mtime_resource_edit(
    tmp_path, changed
):
    templates = _templates(tmp_path)
    target = mt.build_genome_wide_target(str(tmp_path), "hg19", [str(templates)])
    if changed == "template":
        resource = templates / "raw_file_templates/body/23andMe_V3.txt"
        before, after = "1000", "1001"
        expected = "chr1\t1001\trs1"
    else:
        resource = tmp_path / _HG18_CHAIN_NAME
        before, after = "1000000 1001000", "1000001 1001001"
        expected = "chr3\t1001000\trs6"
    stat = resource.stat()
    if changed == "chain":
        resource.write_bytes(
            gzip.compress(
                gzip.decompress(resource.read_bytes()).replace(
                    before.encode(), after.encode()
                ),
                mtime=0,
            )
        )
    else:
        resource.write_text(resource.read_text().replace(before, after))
    os.utime(resource, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    mt.build_genome_wide_target(str(tmp_path), "hg19", [str(templates)])
    with gzip.open(target, "rt") as source:
        assert expected in source.read()


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
def test_issue_853_targets_skip_markers_with_several_placements(tmp_path):
    templates = _templates(tmp_path)
    # A second placement for NCBI36 chr3:999999 and for hg19 chr7:5000.
    (tmp_path / _HG18_CHAIN_NAME).write_bytes(
        gzip.compress(
            (
                _HG18_CHAIN + "chain 900 chr3 2000000 + 999000 1000000 chr3 2000000 + "
                "1500000 1501000 2\n1000\n\n"
            ).encode()
        )
    )
    (tmp_path / "hg19ToHg38.over.chain.gz").write_bytes(
        gzip.compress(
            (
                _CHAIN
                + "chain 800 chr7 10000 + 4000 6000 chr7 10000 + 0 2000 3\n2000\n\n"
                "chain 700 chr7 10000 + 4000 6000 chr7 10000 + 8000 10000 4\n2000\n\n"
            ).encode()
        )
    )

    hg19 = mt.build_genome_wide_target(str(tmp_path), "hg19", [str(templates)])
    hg38 = mt.build_genome_wide_target(str(tmp_path), "hg38", [str(templates)])

    with gzip.open(hg19, "rt") as f:
        assert "rs6" not in f.read()
    with gzip.open(hg38, "rt") as f:
        assert f.read().splitlines()[2:] == ["chr1\t1100\trs1", "chrM\t73\trs4"]


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
    assert mt.build_genome_wide_target(str(tmp_path), "hg19", [str(templates)]) == out
    assert Path(out).stat().st_mtime_ns == mtime


@needs_tools("bgzip", "tabix")
def test_issue_1043_shared_target_reuses_byte_identical_resources_at_another_path(
    tmp_path,
):
    first = tmp_path / "first"
    templates = _templates(first)
    target = mt.build_genome_wide_target(str(first), "hg19", [str(templates)])
    second = tmp_path / "second"
    shutil.copytree(first, second)
    stamp = Path(target).stat().st_mtime_ns
    mt.build_genome_wide_target(
        str(first), "hg19", [str(second / "microarray")], [str(second)]
    )
    assert Path(target).stat().st_mtime_ns == stamp


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
        "All_SNPs_hg19_ref.tab.gz.inputs.json",
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


def _pre_inputs_target(root: Path) -> str:
    # Output of the pre-#1051 v3 builder (81313d8b), indexed with real tools,
    # stamped with the current version: a sidecar-less target of this version.
    source = Path(__file__).parent / "fixtures" / "microarray-target-pre-inputs.tab"
    stamped = root / "pre-inputs.tab"
    root.mkdir(parents=True, exist_ok=True)
    stamped.write_text(
        mt._VERSION_LINE + source.read_text().split("\n", 1)[1], newline="\n"
    )
    target = mt.genome_wide_target_path(str(root), "hg38")
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as output:
        subprocess.run(
            [get_tool_path("bgzip"), "-c", str(stamped)], stdout=output, check=True
        )
    subprocess.run(
        [get_tool_path("tabix"), "-s", "1", "-b", "2", "-e", "2", target], check=True
    )
    return target


@needs_tools("bgzip", "tabix")
@pytest.mark.parametrize("read_only", [False, True], ids=["writable", "read-only"])
def test_issue_1043_pre_sidecar_current_target_is_reused_offline(
    tmp_path, monkeypatch, read_only
):
    if read_only and os.name == "nt":
        pytest.skip("POSIX directory permissions")
    root = tmp_path / "library"
    templates = _templates(root)
    (root / _HG18_CHAIN_NAME).unlink()
    target = _pre_inputs_target(root)
    metadata = Path(target + ".inputs.json")
    stamp = Path(target).stat().st_mtime_ns
    before = Path(target).read_bytes()
    monkeypatch.setattr(
        mt,
        "download_file",
        lambda *_args, **_kwargs: pytest.fail("current target downloaded a chain"),
    )
    microarray = Path(target).parent
    if read_only:
        root.chmod(0o555)
        microarray.chmod(0o555)
    try:
        assert (
            mt.build_genome_wide_target(str(root), "hg38", [str(templates)]) == target
        )
        assert Path(target).stat().st_mtime_ns == stamp
        assert Path(target).read_bytes() == before
        assert metadata.is_file() is not read_only
        assert not list(root.glob("*.lock*"))
        assert not list(microarray.glob("*.lock*"))
        assert (
            mt.build_genome_wide_target(str(root), "hg38", [str(templates)]) == target
        )
    finally:
        root.chmod(0o755)
        microarray.chmod(0o755)


@needs_tools("bgzip", "tabix")
def test_issue_1043_current_fingerprinted_target_does_not_need_removed_chains(
    tmp_path, monkeypatch
):
    templates = _templates(tmp_path)
    chain = tmp_path / "hg19ToHg38.over.chain.gz"
    chain.write_bytes(gzip.compress(_CHAIN.encode()))
    target = mt.build_genome_wide_target(str(tmp_path), "hg38", [str(templates)])
    metadata = Path(target + ".inputs.json")
    before = (Path(target).stat().st_mtime_ns, metadata.read_bytes())
    chain.unlink()
    (tmp_path / _HG18_CHAIN_NAME).unlink()
    monkeypatch.setattr(
        mt,
        "download_file",
        lambda *_args, **_kwargs: pytest.fail("current target downloaded a chain"),
    )
    assert (
        mt.build_genome_wide_target(str(tmp_path), "hg38", [str(templates)]) == target
    )
    assert (Path(target).stat().st_mtime_ns, metadata.read_bytes()) == before
    assert not list(tmp_path.glob("*.lock*"))


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


def test_issue_1043_concurrent_missing_chain_acquisition(tmp_path, chain_servers):
    from concurrent.futures import ThreadPoolExecutor

    _primary, _mirror, data = chain_servers
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(lambda _: mt.hg19_to_hg38_chain(str(tmp_path)), range(2)))
    assert all(Path(path).read_bytes() == data for path in paths)


def test_issue_1043_chain_acquisition_creates_a_fresh_library(tmp_path, chain_servers):
    _primary, _mirror, data = chain_servers
    chain = mt.hg19_to_hg38_chain(str(tmp_path / "new-library"))
    assert Path(chain).read_bytes() == data


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
    assert sorted(p.name for p in tmp_path.iterdir()) == [name + ".lock.guard"]


def test_the_pinned_chain_wins_over_an_earlier_same_named_copy(tmp_path, chain_servers):
    # A FASTA folder searched first holds a different hg19ToHg38; the library's
    # pinned copy is the one that places the markers.
    primary, _mirror, data = chain_servers
    name = mt.HG19_TO_HG38_CHAIN.name
    fasta_dir, library = tmp_path / "fasta", tmp_path / "library"
    fasta_dir.mkdir()
    library.mkdir()
    (fasta_dir / name).write_bytes(gzip.compress(b"another release"))
    (library / name).write_bytes(data)
    assert mt.hg19_to_hg38_chain(str(library), [str(fasta_dir)]) == str(library / name)
    assert primary.requests == []


def test_an_unpinned_chain_is_still_used_but_named_when_nothing_matches(
    tmp_path, chain_servers, caplog
):
    primary, _mirror, _data = chain_servers
    name = mt.HG19_TO_HG38_CHAIN.name
    (tmp_path / name).write_bytes(gzip.compress(b"another release"))
    with caplog.at_level("WARNING"):
        assert mt.hg19_to_hg38_chain(str(tmp_path)) == str(tmp_path / name)
    assert f"{tmp_path / name} is not UCSC's pinned {name}" in caplog.text
    assert primary.requests == []


def _cut_off_with_a_partial(server, data: bytes) -> None:
    """Every GET and every resume is cut off half-way, leaving a partial."""
    server.drops = 1
    server.drop_starts = set(range(len(data) // 2, len(data)))


def test_a_mirror_download_leaves_no_staging_files(tmp_path, chain_servers):
    """PR #669 review: UCSC and the mirror shared one partial file; with
    separate ones, none may outlive a completed chain."""
    primary, mirror, data = chain_servers
    _cut_off_with_a_partial(primary, data)
    chain = mt.hg19_to_hg38_chain(str(tmp_path))
    assert Path(chain).read_bytes() == data
    assert mirror.gets() == [None]
    assert {p.name for p in tmp_path.iterdir()} == {
        mt.HG19_TO_HG38_CHAIN.name,
        mt.HG19_TO_HG38_CHAIN.name + ".lock.guard",
    }


def test_each_source_keeps_its_own_resumable_partial(tmp_path, chain_servers):
    """PR #669 review: trying the mirror threw away UCSC's resumable download."""
    primary, mirror, data = chain_servers
    _cut_off_with_a_partial(primary, data)
    mirror.statuses[mt.HG19_TO_HG38_CHAIN.name] = 503
    with pytest.raises(mt.WGSExtractError, match="Could not download"):
        mt.hg19_to_hg38_chain(str(tmp_path))
    (partial,) = tmp_path.glob("*.partial")
    kept = partial.stat().st_size
    assert 0 < kept < len(data)

    primary.requests.clear()
    primary.drop_starts.clear()
    mirror.requests.clear()
    assert Path(mt.hg19_to_hg38_chain(str(tmp_path))).read_bytes() == data
    assert primary.gets() == [f"bytes={kept}-"]
    assert not mirror.requests
    assert {p.name for p in tmp_path.iterdir()} == {
        mt.HG19_TO_HG38_CHAIN.name,
        mt.HG19_TO_HG38_CHAIN.name + ".lock.guard",
    }


@pytest.mark.parametrize("sub", ["", "downloads", "ref", "microarray"])
def test_requested_chain_is_found_in_library_subdirectories(tmp_path, monkeypatch, sub):
    chain = tmp_path / sub / "hg38ToHg19.over.chain.gz"
    chain.parent.mkdir(parents=True, exist_ok=True)
    chain.write_bytes(b"")
    monkeypatch.setattr(
        mt, "download_chain", lambda *_: pytest.fail("must not download")
    )
    assert mt.fetch_requested_chains(str(tmp_path), ["23andme_v5", "ftdna_v4y"]) == {
        "GRCh38": str(chain)
    }
    assert mt.fetch_requested_chains(str(tmp_path), ["23andme_v5"]) == {}
