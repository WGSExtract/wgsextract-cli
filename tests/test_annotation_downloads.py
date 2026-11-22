"""Integrity of annotation-score downloads (#1245), against a loopback server."""

import gzip
import hashlib
import sys
from pathlib import Path

import pytest

from tests.http_server import serving
from wgsextract_cli import api
from wgsextract_cli.core import annotation_resources
from wgsextract_cli.core.annotation_resources import REVEL_URLS


@pytest.fixture
def server(monkeypatch):
    """Serves server.files; any other path is a 404."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    with serving(None) as srv:
        yield srv


def _serve_cadd(server, name: str, data: bytes, index: bytes) -> None:
    for suffix, body in (("", data), (".tbi", index)):
        path = f"GRCh37/{name}{suffix}"
        server.files[path] = body
        md5 = hashlib.md5(body, usedforsecurity=False).hexdigest()
        server.files[f"{path}.md5"] = f"{md5}  {name}{suffix}\n".encode()


def _cadd_with_table_only(tmp_path, monkeypatch, server, table_data: bytes) -> Path:
    """Indels installed; the SNV table in place but not its index."""
    monkeypatch.setattr(annotation_resources, "CADD_BASE_URL", server.base)
    _serve_cadd(server, "gnomad.genomes-exomes.r4.0.indel.tsv.gz", b"i", b"ii")
    _serve_cadd(server, "whole_genome_SNVs.tsv.gz", b"snv table", b"snv index")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    for name in ("cadd_indel_hg19.tsv.gz", "cadd_indel_hg19.tsv.gz.tbi"):
        (downloads / name).write_bytes(b"installed")
    table = downloads / "cadd_hg19.tsv.gz"
    table.write_bytes(table_data)
    return table


def test_1245_cadd_fetches_only_the_missing_index_of_a_complete_table(
    tmp_path, monkeypatch, server
):
    """#1245: a complete SNV table whose .tbi fetch failed was fetched again
    (85 GB) on the next run; now it is checked and only the index fetched."""
    table = _cadd_with_table_only(tmp_path, monkeypatch, server, b"snv table")

    api.ref.cadd(api.RefCaddParams(ref=tmp_path, build="hg19"))

    assert "GRCh37/whole_genome_SNVs.tsv.gz" not in server.get_paths
    assert "GRCh37/whole_genome_SNVs.tsv.gz.tbi" in server.get_paths
    assert table.read_bytes() == b"snv table"
    assert Path(f"{table}.tbi").read_bytes() == b"snv index"


def test_1245_cadd_keeps_a_table_that_fails_its_md5_and_fetches_nothing(
    tmp_path, monkeypatch, server
):
    """A table this run didn't download may be the user's own: it is reported,
    never replaced or removed."""
    table = _cadd_with_table_only(tmp_path, monkeypatch, server, b"another table")

    with pytest.raises(api.CommandFailed, match="CADD"):
        api.ref.cadd(api.RefCaddParams(ref=tmp_path, build="hg19"))

    assert not any(
        p.startswith("GRCh37/whole_genome_SNVs.tsv.gz") and not p.endswith(".md5")
        for p in server.get_paths
    )
    assert table.read_bytes() == b"another table"
    assert not Path(f"{table}.tbi").exists()


def test_1245_revel_that_differs_from_its_pinned_digest_is_not_installed(
    tmp_path, monkeypatch, server
):
    """#1245: REVEL was installed from plain http with nothing to check it
    against; bytes that differ from the pinned SHA-256 are now refused."""
    for build in REVEL_URLS:
        server.files[f"{build}/revel"] = gzip.compress(b"#Chr\tStart\n1\t100\n")
        url = server.url_for(f"{build}/revel")
        monkeypatch.setitem(REVEL_URLS, build, url)
        monkeypatch.setitem(annotation_resources.REVEL_SHA256, url, "0" * 64)

    with pytest.raises(api.CommandFailed):
        api.ref.revel(api.RefRevelParams(ref=tmp_path))

    for build in REVEL_URLS:
        assert not (tmp_path / "downloads" / f"revel_{build}.tsv.gz").exists()
