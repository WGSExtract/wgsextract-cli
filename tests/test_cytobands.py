"""#1148: public cytobands install explicitly, validate, and remain offline."""

import gzip
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.http_server import RangeServer, serving
from wgsextract_cli import api
from wgsextract_cli.api.ref import RefCytobandsParams
from wgsextract_cli.core import cytobands
from wgsextract_cli.main import main


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
        target = tmp_path / "downloads" / f"cytobands_{build}.tsv"
        assert target.read_bytes() == body
        assert any(output.role == "reference_resource" for output in result.outputs)
        server.requests.clear()
        manifest = Path(str(target) + ".manifest.json")
        record = manifest.read_bytes()
        reused = api.ref.cytobands(params)
        assert not reused.outputs
        assert any(item.path == str(target) for item in reused.inputs)
        assert manifest.read_bytes() == record
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
        assert not (tmp_path / "downloads" / "cytobands_hg38.tsv").exists()


def test_1148_truncated_gzip_and_checksum_mismatch_preserve_installed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "downloads" / "cytobands_hg38.tsv"
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
        assert list((tmp_path / "downloads").glob("*.partial"))
        server.drops = 0
        source(monkeypatch, server, "hg38", compressed, "second.gz")
        api.ref.cytobands(params)
        assert (tmp_path / "downloads" / "cytobands_hg38.tsv").read_bytes() == table(
            "hg38"
        )
        assert "second.gz" in server.get_paths
        assert not list((tmp_path / "downloads").glob("*.partial*"))


def test_1153_install_preserves_user_archive_and_public_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "downloads" / "cytobands_hg38.txt.gz"
    archive.parent.mkdir()
    archive.write_bytes(b"user archive")
    sibling = archive.parent / "ordinary-public-file"
    sibling.write_bytes(b"public")
    with serving(data=None) as server:
        source(monkeypatch, server, "hg38", gzip.compress(table("hg38"), mtime=0))
        result = api.ref.cytobands(RefCytobandsParams(ref=tmp_path, download=True))
    target = tmp_path / "downloads" / "cytobands_hg38.tsv"
    assert target.read_bytes() == table("hg38")
    assert archive.read_bytes() == b"user archive"
    assert not list(archive.parent.glob(".cytobands-*"))
    assert result.outputs
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == sibling.stat().st_mode & 0o777


def test_1153_independent_installers_publish_once(tmp_path: Path) -> None:
    compressed = gzip.compress(table("hg38"), mtime=0)
    with serving(data=None) as server:
        server.files["bands.gz"] = compressed
        server.delays["bands.gz"] = 1
        script = (
            "import sys; from wgsextract_cli.core import cytobands as c; "
            "c.CYTOBAND_SOURCES['hg38']=(sys.argv[2],sys.argv[3]); "
            "print(c.install_cytobands(sys.argv[1],'hg38',download=True)[1])"
        )
        args = [
            sys.executable,
            "-c",
            script,
            str(tmp_path),
            server.url_for("bands.gz"),
            hashlib.sha256(compressed).hexdigest(),
        ]
        processes = [
            subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(2)
        ]
        try:
            results = [process.communicate(timeout=30) for process in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        assert all(process.returncode == 0 for process in processes), results
        assert sorted(stdout.strip() for stdout, _ in results) == [b"False", b"True"]
        assert server.get_paths == ["bands.gz"]
    assert (tmp_path / "downloads" / "cytobands_hg38.tsv").read_bytes() == table("hg38")


def test_1153_cli_repair_hint_keeps_selected_ref_and_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "selected config.toml"
    library = tmp_path / "library with 'quotes'"
    config.write_text("")
    with pytest.raises(SystemExit):
        main(["--config", str(config), "--ref", str(library), "ref", "cytobands"])
    error = capsys.readouterr().err
    assert shlex.quote(str(config)) in error
    assert shlex.quote(str(library)) in error
    assert "--download" in error


def test_1153_default_library_ignores_configured_external_fasta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    library = tmp_path / "library"
    fasta = tmp_path / "external" / "reference.fa"
    fasta.parent.mkdir()
    fasta.write_text(">chr1\nACGT\n")
    config = tmp_path / "config.toml"
    config.write_text(
        f"reference_library = {json.dumps(str(library))}\n"
        f"reference_fasta = {json.dumps(str(fasta))}\n"
    )
    with api.scoped_config(config), serving(data=None) as server:
        source(monkeypatch, server, "hg38", gzip.compress(table("hg38"), mtime=0))
        api.ref.cytobands(RefCytobandsParams(download=True))
        assert (library / "downloads" / "cytobands_hg38.tsv").read_bytes() == table(
            "hg38"
        )
        assert not (fasta.parent / "downloads").exists()
        target = library / "downloads" / "cytobands_hg38.tsv"
        manifest = Path(str(target) + ".manifest.json")
        record = manifest.read_bytes()
        main(["--config", str(config), "ref", "cytobands"])
        assert str(target) in capsys.readouterr().out
        assert manifest.read_bytes() == record
        api.ref.cytobands(RefCytobandsParams(ref=fasta, download=True))
        assert (
            fasta.parent / "downloads" / "cytobands_hg38.tsv"
        ).read_bytes() == table("hg38")


def many_bands(count: int) -> bytes:
    rows = [
        f"chr1\t{i}\t{i + 1}\tp{i // 1000}.{i % 1000:03}\tgneg"
        for i in range(count - 24)
    ]
    rows.append(f"chr1\t{count - 24}\t248956422\tq44\tgpos100")
    return ("\n".join(rows) + "\n").encode() + b"\n".join(
        table("hg38").split(b"\n")[2:]
    )


@pytest.mark.parametrize(
    "bad",
    [
        table("hg38").replace(b"\n", b"\r") + b"\n",
        many_bands(10_001),
    ],
)
def test_1153_viewer_invalid_existing_table_is_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: bytes
) -> None:
    target = tmp_path / "downloads" / "cytobands_hg38.tsv"
    target.parent.mkdir()
    target.write_bytes(bad)
    with serving(data=None) as server:
        source(monkeypatch, server, "hg38", gzip.compress(table("hg38"), mtime=0))
        with pytest.raises(api.CommandFailed, match="--download"):
            api.ref.cytobands(RefCytobandsParams(ref=tmp_path))
        assert not server.requests
        api.ref.cytobands(RefCytobandsParams(ref=tmp_path, download=True))
    assert target.read_bytes() == table("hg38")


@pytest.mark.parametrize(
    "body",
    [
        table("hg38").replace(b"\n", b"\r\n"),
        many_bands(10_000),
    ],
)
def test_1153_viewer_valid_newlines_and_band_limit_reuse_offline(
    tmp_path: Path, body: bytes
) -> None:
    target = tmp_path / "downloads" / "cytobands_hg38.tsv"
    target.parent.mkdir()
    target.write_bytes(body)
    assert not api.ref.cytobands(RefCytobandsParams(ref=tmp_path)).outputs
    assert target.read_bytes() == body


def test_1153_escaping_table_symlink_can_be_reinstalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.tsv"
    outside.write_bytes(table("hg38"))
    library = tmp_path / "library"
    target = library / "downloads" / "cytobands_hg38.tsv"
    target.parent.mkdir(parents=True)
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable.")
    with serving(data=None) as server:
        source(monkeypatch, server, "hg38", gzip.compress(table("hg38"), mtime=0))
        with pytest.raises(api.CommandFailed, match="--download"):
            api.ref.cytobands(RefCytobandsParams(ref=library))
        assert not server.requests
        api.ref.cytobands(RefCytobandsParams(ref=library, download=True))
    assert not target.is_symlink()
    assert target.read_bytes() == outside.read_bytes() == table("hg38")


def test_1141_older_library_cytobands_in_ref_still_validate_offline(tmp_path):
    """#1141 (ref/ renamed downloads/): an older library's ref/ table stays
    installed instead of asking for --download."""
    legacy = tmp_path / "ref" / "cytobands_hg38.tsv"
    legacy.parent.mkdir()
    legacy.write_bytes(table("hg38"))

    result = api.ref.cytobands(RefCytobandsParams(ref=tmp_path, build="hg38"))

    assert any(item.path == str(legacy) for item in result.inputs)
    assert not (tmp_path / "downloads").exists()
