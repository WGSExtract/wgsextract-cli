"""#1148: public cytobands install explicitly, validate, and remain offline."""

import gzip
import hashlib
from pathlib import Path

import pytest

from tests.http_server import RangeServer, serving
from wgsextract_cli import api
from wgsextract_cli.api.ref import RefCytobandsParams
from wgsextract_cli.core import cytobands


def table(build: str) -> bytes:
    length = 248956422 if build == "hg38" else 249250621
    rows = [
        "chr1\t0\t100\tp36.33\tgneg",
        f"chr1\t100\t{length}\tq44\tgpos100",
    ]
    rows.extend(
        f"chr{chrom}\t0\t{size}\t{'q11.221' if chrom == 'Y' else 'p11'}\tgneg"
        for chrom, size in zip(
            [*range(2, 23), "X", "Y"],
            cytobands.CHROMOSOME_LENGTHS[build][1:],
            strict=True,
        )
    )
    return ("\n".join(rows) + "\n").encode()


def source(
    monkeypatch: pytest.MonkeyPatch,
    server: RangeServer,
    build: str,
    data: bytes,
    name: str = "cytoBand.txt.gz",
) -> None:
    monkeypatch.setitem(
        cytobands.CYTOBAND_SOURCES,
        build,
        (server.url_for(name), hashlib.sha256(data).hexdigest()),
    )
    server.files[name] = data


@pytest.mark.parametrize("build", ["hg19", "hg38"])
def test_1148_cytobands_require_explicit_download_and_reuse_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build: str
) -> None:
    body = table(build)
    compressed = gzip.compress(body + b"chr1_alt\t0\t50\t\tgneg\n", mtime=0)
    with serving(data=None) as server:
        source(monkeypatch, server, build, compressed)
        params = RefCytobandsParams(ref=tmp_path, build=build)
        with pytest.raises(api.CommandFailed, match=f"cytobands_{build}.tsv"):
            api.ref.cytobands(params)
        assert server.requests == []
        result = api.ref.cytobands(
            RefCytobandsParams(ref=tmp_path, build=build, download=True)
        )
        target = tmp_path / "ref" / f"cytobands_{build}.tsv"
        assert target.read_bytes() == body
        assert any(output.role == "reference_resource" for output in result.outputs)
        server.requests.clear()
        api.ref.cytobands(params)
        assert server.requests == []
        assert not list(target.parent.glob("*.partial*"))
        assert not list(target.parent.glob("*.txt.gz"))


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        table("hg38")[:-1],
        table("hg38").replace(b"gneg", b"unknown", 1),
        table("hg38").replace(b"\t0\t100\t", b"\t-1\t100\t", 1),
        table("hg38").replace(b"chr1\t100\t", b"chr1\t99\t", 1),
        table("hg38").replace(b"chr1\t100\t", b"chr1\t101\t", 1),
        table("hg38").replace(b"p36.33\tgneg", b"p36.33\tgneg\textra", 1),
        b"\n".join(table("hg38").splitlines()[:-1]) + b"\n",
        table("hg19"),
    ],
    ids=[
        "empty",
        "truncated-row",
        "stain",
        "negative",
        "overlap",
        "gap",
        "columns",
        "missing-chromosome",
        "wrong-build",
    ],
)
def test_1148_malformed_cytoband_download_never_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: bytes
) -> None:
    with serving(data=None) as server:
        source(monkeypatch, server, "hg38", gzip.compress(bad, mtime=0))
        with pytest.raises(api.CommandFailed, match="Invalid cytoband"):
            api.ref.cytobands(
                RefCytobandsParams(ref=tmp_path, build="hg38", download=True)
            )
        assert not (tmp_path / "ref" / "cytobands_hg38.tsv").exists()


def test_1148_truncated_gzip_and_checksum_mismatch_preserve_installed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "ref" / "cytobands_hg38.tsv"
    target.parent.mkdir()
    original = b"previous invalid table\n"
    target.write_bytes(original)
    compressed = gzip.compress(table("hg38"), mtime=0)
    with serving(data=None) as server:
        source(monkeypatch, server, "hg38", compressed[:-8])
        with pytest.raises(api.CommandFailed, match="Invalid cytoband archive"):
            api.ref.cytobands(
                RefCytobandsParams(ref=tmp_path, build="hg38", download=True)
            )
        assert target.read_bytes() == original
        source(monkeypatch, server, "hg38", compressed)
        server.files["cytoBand.txt.gz"] = compressed[:-1] + b"x"
        with pytest.raises(api.CommandFailed, match="Failed to download"):
            api.ref.cytobands(
                RefCytobandsParams(ref=tmp_path, build="hg38", download=True)
            )
        assert target.read_bytes() == original


def test_1148_cytobands_resume_partial_and_changed_url_cannot_mix_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compressed = gzip.compress(table("hg38"), mtime=0)
    with serving(data=None) as server:
        source(monkeypatch, server, "hg38", compressed, "first.gz")
        server.drops = 10
        params = RefCytobandsParams(ref=tmp_path, build="hg38", download=True)
        with pytest.raises(api.CommandFailed, match="Failed to download"):
            api.ref.cytobands(params)
        assert list((tmp_path / "ref").glob("*.partial"))
        server.drops = 0
        source(monkeypatch, server, "hg38", compressed, "second.gz")
        api.ref.cytobands(params)
        assert (tmp_path / "ref" / "cytobands_hg38.tsv").read_bytes() == table("hg38")
        assert "second.gz" in server.get_paths
        assert not list((tmp_path / "ref").glob("*.partial*"))
