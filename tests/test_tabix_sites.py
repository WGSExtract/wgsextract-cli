"""Reading a bgzipped, tabix-indexed VCF at a list of sites only (issue 629:
per-run gnomAD and SpliceAI for `vcf acmg`), from a local server standing in
for gnomAD's bucket (the network is the boundary) or a local file. The VCF is
made with the real bgzip and tabix: two contigs over many 16 kb windows and
BGZF blocks, with records dense in places and sparse in others."""

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
