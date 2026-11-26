"""Reading a bgzipped, tabix-indexed VCF at a list of sites only (issue 629:
per-run gnomAD and SpliceAI for `vcf acmg`), from a local server standing in
for gnomAD's bucket (the network is the boundary) or a local file. The VCF is
made with the real bgzip and tabix: two contigs over many 16 kb windows and
BGZF blocks, with records dense in places and sparse in others."""

import os
import random
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib
from tests.http_server import serving
from wgsextract_cli.core import read_scope
from wgsextract_cli.core.tabix_sites import TabixFile
from wgsextract_cli.core.utils import WGSExtractError

pytestmark = needs_htslib

HEADER = (
    "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=20000000>\n"
    "##contig=<ID=chr2,length=20000000>\n"
    '##INFO=<ID=X,Number=1,Type=String,Description="padding">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
)


def _records(contig: str, count: int, rng: random.Random) -> list[tuple[str, int, str]]:
    found, pos = [], 0
    for _ in range(count):
        pos += rng.choice((0, 1, 1, 2, 3, 40, 900))  # several alleles at a position too
        pos = max(pos, 1)
        line = f"{contig}\t{pos}\t.\tA\tG\t.\tPASS\tX={rng.randbytes(100).hex()}"
        found.append((contig, pos, line))
    return found


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    """The bgzipped VCF and its index as bytes, and its records."""
    rng = random.Random(629)
    records = _records("chr1", 100000, rng) + _records("chr2", 300, rng)
    plain = tmp_path_factory.mktemp("tabix") / "sites.vcf"
    plain.write_text(
        HEADER + "".join(line + "\n" for *_, line in records), newline="\n"
    )
    vcf = Path(bgzip_index(plain))
    return vcf, records


def _sites(records, rng: random.Random, count: int) -> list[tuple[int, int]]:
    """Sites on chr1: some at records, some between them, past the last one too."""
    chr1 = [pos for contig, pos, _ in records if contig == "chr1"]
    picked = {(p - 1, p + 1) for p in rng.sample(chr1, count)}
    picked |= {(p, p) for p in rng.sample(range(1, chr1[-1] + 2000), count)}
    picked.add((chr1[-1], chr1[-1] + 5000))
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(picked):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def _expected(records, sites, contig="chr1") -> list[bytes]:
    return [
        line.encode()
        for c, pos, line in records
        if c == contig and any(lo <= pos <= hi for lo, hi in sites)
    ]


@pytest.mark.parametrize("remote", [True, False], ids=["http", "local file"])
def test_records_at_the_sites_are_those_of_the_whole_file_from_far_fewer_bytes(
    indexed, remote
):
    vcf, records = indexed
    sites = _sites(records, random.Random(1), 20)
    with serving(
        None,
        {"s.vcf.gz": vcf.read_bytes(), "s.vcf.gz.tbi": Path(f"{vcf}.tbi").read_bytes()},
    ) as server:
        server.etags = True
        location = server.url_for("s.vcf.gz") if remote else str(vcf)
        file = TabixFile(location)

        found = file.records_at("chr1", sites, workers=4)

        assert found == _expected(records, sites)
        assert file.header().decode() == HEADER
        assert file.bytes_read < vcf.stat().st_size / 3
        # A site past chr1's last record reads none of chr2's.
        assert not any(line.startswith(b"chr2") for line in found)


def test_a_contig_with_no_records_at_its_sites_still_has_a_first_record(indexed):
    vcf, records = indexed
    file = TabixFile(str(vcf))
    assert file.records_at("chr2", [(1, 1)]) == []
    first = next(line for contig, _, line in records if contig == "chr2")
    assert file.first_record("chr2") == first.encode()


@pytest.mark.parametrize(
    "newline", ["\n", "\r\n", "mixed"], ids=["lf", "crlf", "mixed"]
)
def test_1507_dense_local_lookup_keeps_pos_order_and_duplicate_records(
    tmp_path: Path, newline: str
) -> None:
    # Both an END span and multiple REF bases can overlap a target whose
    # position is different. Neither should be added to the site database.
    variants = (
        (101, "chr1\t101\tspan\tA\t<DEL>\t.\tPASS\tEND=500"),
        (198, "chr1\t198\tdel\tAAA\tA\t.\tPASS\t."),
        (200, "chr1\t200\tins\tA\tAT\t.\tPASS\t."),
        (200, "chr1\t200\tins\tA\tAT\t.\tPASS\t."),
        (200, "chr1\t200\tsnv\tA\tG\t.\tPASS\t."),
        (350, "chr1\t350\tcontained\tA\tT\t.\tPASS\t."),
        (600, "chr1\t600\tlast\tA\tC\t.\tPASS\t."),
        (650, "chr1\t650\toutside\tA\tG\t.\tPASS\t."),
    )
    plain = tmp_path / "sites.vcf"
    header = HEADER.replace(
        "#CHROM", '##INFO=<ID=END,Number=1,Type=Integer,Description="End">\n#CHROM'
    )
    lines = [
        (
            line + ("\r" if newline == "\r\n" or (newline == "mixed" and i % 2) else "")
        ).encode()
        for i, (_, line) in enumerate(variants)
    ]
    plain.write_bytes(header.encode() + b"\n".join(lines) + b"\n")
    vcf = bgzip_index(plain)
    sites = [(p, p) for p in range(200, 601, 2)]
    staged: list[bytes] = []
    TabixFile(vcf).records_at("chr1", sites, on_group=staged.extend, sweep_dir=tmp_path)
    expected = [
        line
        for (pos, _), line in zip(variants, lines, strict=True)
        if (pos, pos) in sites
    ]
    assert staged == expected == TabixFile(vcf).records_at("chr1", sites)


def test_1507_dense_local_lookup_seeks_near_targets_in_a_multiblock_vcf(
    indexed: tuple[Path, list[tuple[str, int, str]]], tmp_path: Path
) -> None:
    vcf, variants = indexed
    positions = {
        pos
        for chrom, pos, _ in variants
        if chrom == "chr1" and 6_000_000 < pos < 6_050_000
    }
    sites = [(pos, pos) for pos in sorted(positions)]
    reader = TabixFile(str(vcf))

    found = reader.records_at("chr1", sites, sweep_dir=tmp_path)

    assert found == [
        line.encode()
        for chrom, pos, line in variants
        if chrom == "chr1" and pos in positions
    ]
    assert reader.bytes_read < vcf.stat().st_size / 5


def test_629_windows_stat_and_fstat_difference_does_not_change_the_source(
    indexed: tuple[Path, list[tuple[str, int, str]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    vcf, records = indexed
    fstat = os.fstat

    def descriptor_stat(fd: int) -> os.stat_result:
        stat = fstat(fd)
        values = list(stat)
        # Windows pathname and descriptor queries need not agree on the
        # device ID or whether ctime is creation time or metadata-change time.
        values[2] = stat.st_dev ^ 1
        values[9] += 1
        return os.stat_result(
            values,
            {
                "st_atime_ns": stat.st_atime_ns,
                "st_mtime_ns": stat.st_mtime_ns,
                "st_ctime_ns": stat.st_ctime_ns + 1_000_000_000,
            },
        )

    monkeypatch.setattr(os, "fstat", descriptor_stat)
    file = TabixFile(str(vcf))
    sites = _sites(records, random.Random(4), 5)
    assert file.header().decode() == HEADER
    assert file.records_at("chr1", sites, workers=2) == _expected(records, sites)


def test_a_server_that_answers_a_range_with_the_whole_file_is_an_error(indexed):
    # A file replaced during a run is answered whole (If-Range); its records
    # must not be mixed with the old one's.
    vcf, records = indexed
    with serving(
        None,
        {"s.vcf.gz": vcf.read_bytes(), "s.vcf.gz.tbi": Path(f"{vcf}.tbi").read_bytes()},
    ) as server:
        server.etags = True
        file = TabixFile(server.url_for("s.vcf.gz"))
        assert file.index.contigs  # read before the file changes
        server.ignore_ranges = True
        with pytest.raises(WGSExtractError, match="changed during the run"):
            file.records_at("chr1", _sites(records, random.Random(2), 5))


def test_a_damaged_block_holding_a_sites_records_is_an_error(indexed, tmp_path):
    vcf, records = indexed
    data = bytearray(vcf.read_bytes())
    middle = len(data) // 2
    data[middle] ^= 0xFF  # inside some block's compressed records
    damaged = tmp_path / "damaged.vcf.gz"
    damaged.write_bytes(bytes(data))
    Path(f"{damaged}.tbi").write_bytes(Path(f"{vcf}.tbi").read_bytes())
    chr1 = [pos for contig, pos, _ in records if contig == "chr1"]
    with pytest.raises(WGSExtractError, match="no valid BGZF block"):
        # Every record's site: one of them is in the damaged block.
        TabixFile(str(damaged)).records_at("chr1", [(chr1[0], chr1[-1])])


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"ETag": 'W/"weak"'},
        {"ETag": "unquoted"},
        {"Last-Modified": "not a date"},
    ],
)
def test_629_unversioned_remote_file_is_refused_before_range_reads(headers):
    with serving(b"source") as server:
        server.headers["source"] = headers
        with pytest.raises(WGSExtractError, match="validator"):
            TabixFile(server.url_for("source"))
        assert not server.gets()


@pytest.mark.parametrize("replace", [True, False], ids=["replacement", "in-place"])
def test_629_changed_local_source_cannot_mix_releases(indexed, tmp_path, replace):
    vcf, _ = indexed
    local = tmp_path / "source.vcf.gz"
    local.write_bytes(vcf.read_bytes())
    Path(f"{local}.tbi").write_bytes(Path(f"{vcf}.tbi").read_bytes())
    # A release file predates the run; on Windows an edit within the same
    # clock tick as its creation would leave its mtime unchanged.
    old = local.stat().st_mtime_ns - 10**9
    os.utime(local, ns=(old, old))
    file = TabixFile(str(local))
    file.header()
    if replace:
        other = tmp_path / "replacement"
        other.write_bytes(vcf.read_bytes())
        other.replace(local)
    else:
        with local.open("r+b") as output:
            output.write(b"changed")
    with pytest.raises(WGSExtractError, match="changed during the run"):
        file.records_at("chr1", [(100, 100)])


def test_629_local_index_symlink_cannot_escape_read_scope(indexed, tmp_path):
    vcf, _ = indexed
    local = tmp_path / "source.vcf.gz"
    local.write_bytes(vcf.read_bytes())
    Path(f"{local}.tbi").symlink_to(Path(f"{vcf}.tbi"))
    with read_scope.limited_to(lambda p: Path(p).resolve().is_relative_to(tmp_path)):
        file = TabixFile(str(local))
        with pytest.raises(WGSExtractError, match="outside the folders"):
            file.records_at("chr1", [(100, 100)])


def test_629_range_reads_honor_proxy_configuration(indexed, monkeypatch):
    from urllib.request import build_opener

    from wgsextract_cli.core import tabix_sites

    vcf, records = indexed
    files = {"source": vcf.read_bytes(), "source.tbi": Path(f"{vcf}.tbi").read_bytes()}
    # A proxy receives an absolute URL. The fixture server accepts its path;
    # origin.invalid is deliberately unreachable without the proxy.
    with serving(None, files) as proxy:
        proxy.etags = True
        monkeypatch.setenv("http_proxy", proxy.base.rsplit("/", 1)[0])
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setattr(tabix_sites, "urlopen", build_opener().open)
        file = TabixFile(f"http://origin.invalid{proxy.prefix}source")
        sites = _sites(records, random.Random(3), 5)
        assert file.records_at("chr1", sites, workers=2) == _expected(records, sites)
        assert proxy.gets()
