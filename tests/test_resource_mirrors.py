"""Issue 1046: observable mirror preference, verification and safe fallbacks."""

import gzip
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from scripts import check_remote_resources as checks
from scripts import mirror_download_resources as publisher
from scripts import mirror_resource_transfer as transfer
from tests.htslib_helpers import needs_tools
from tests.http_server import RangeServer
from wgsextract_cli import api
from wgsextract_cli.core import gnomad_af, resource_mirrors
from wgsextract_cli.core.ref_library.downloads import download_file


@pytest.fixture
def mirror_catalog(tmp_path, monkeypatch):
    manifest = tmp_path / "resource_mirrors.json"
    monkeypatch.setattr(resource_mirrors, "MANIFEST", manifest)
    resource_mirrors.resources.cache_clear()

    def install(server, payload, *, allowed=True, split=None, mutable=False):
        chunks = [payload] if split is None else [payload[:split], payload[split:]]
        entry = {
            "id": "test-resource",
            "upstream_url": server.url_for("upstream"),
            "redistributable": allowed,
            "upstream_mutable": mutable,
            "reason": "Non-commercial source terms" if not allowed else "Public data",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "parts": [
                {
                    "url": server.url_for(f"mirror-{i}"),
                    "size": len(chunk),
                    "sha256": hashlib.sha256(chunk).hexdigest(),
                }
                for i, chunk in enumerate(chunks)
            ],
        }
        server.files.update({f"mirror-{i}": chunk for i, chunk in enumerate(chunks)})
        server.files["upstream"] = payload
        manifest.write_text(json.dumps({"resources": [entry]}))
        resource_mirrors.resources.cache_clear()
        return entry

    yield install
    resource_mirrors.resources.cache_clear()


@pytest.fixture
def source():
    server = RangeServer(None)
    yield server
    server.close()


@pytest.mark.parametrize("split", [None, 4])
def test_1046_prefers_verified_mirror_and_reassembles_parts(
    source, mirror_catalog, tmp_path, split
):
    payload = b"original complete resource"
    entry = mirror_catalog(source, payload, split=split)
    target = tmp_path / "download"
    orphan = Path(str(target) + ".partial.source")
    orphan.write_text("stale source marker")
    assert download_file(entry["upstream_url"], str(target), use_cache=False)
    assert target.read_bytes() == payload
    assert "upstream" not in source.paths
    assert not Path(str(target) + ".mirror").exists()
    assert not orphan.exists()


@pytest.mark.parametrize("failure", ["checksum", "404", "second-part"])
def test_1046_bad_mirror_warns_and_installs_verified_upstream(
    source, mirror_catalog, tmp_path, caplog, failure
):
    payload = b"original complete resource"
    entry = mirror_catalog(
        source, payload, split=4 if failure == "second-part" else None
    )
    if failure == "404":
        source.statuses["mirror-0"] = 404
    else:
        source.files["mirror-1" if failure == "second-part" else "mirror-0"] = (
            b"corrupt"
        )
    target = tmp_path / "download"
    if failure == "404":
        work = Path(str(target) + ".mirror")
        work.mkdir()
        (work / "part0000.partial").write_bytes(payload[:4])
        (work / "part0000.partial.source").write_text(
            hashlib.sha256(entry["parts"][0]["url"].encode()).hexdigest()
        )
    with caplog.at_level(logging.WARNING):
        assert download_file(entry["upstream_url"], str(target), use_cache=False)
    assert target.read_bytes() == payload
    assert "upstream" in source.paths
    assert "MIRROR_FALLBACK" in caplog.text
    assert not Path(str(target) + ".mirror").exists()


def test_1046_changed_upstream_cannot_replace_pinned_snapshot(
    source, mirror_catalog, tmp_path, caplog
):
    entry = mirror_catalog(source, b"pinned resource")
    source.statuses["mirror-0"] = 404
    source.files["upstream"] = b"new upstream snapshot"
    target = tmp_path / "download"
    target.write_bytes(b"previous installed data")
    assert not download_file(entry["upstream_url"], str(target), use_cache=False)
    assert target.read_bytes() == b"previous installed data"
    assert "checksum mismatch" in caplog.text


@pytest.mark.parametrize(
    ("changed", "explicit_pin"), [(True, False), (False, False), (True, True)]
)
def test_1046_mutable_upstream_can_advance_without_breaking_fallback(
    source, mirror_catalog, tmp_path, caplog, changed, explicit_pin
):
    payload = b"pinned snapshot"
    entry = mirror_catalog(source, payload, mutable=True)
    source.statuses["mirror-0"] = 404
    upstream = b"new weekly upstream snapshot" if changed else payload
    source.files["upstream"] = upstream
    target = tmp_path / "download"
    accepted = not (changed and explicit_pin)
    assert (
        download_file(
            entry["upstream_url"],
            str(target),
            use_cache=False,
            expected_sha256=entry["sha256"] if explicit_pin else None,
        )
        is accepted
    )
    if accepted:
        assert target.read_bytes() == upstream
    else:
        assert not target.exists()
    assert ("UPSTREAM_VERSION_DRIFT" in caplog.text) is (changed and not explicit_pin)


@pytest.mark.parametrize(
    ("mirror", "split"),
    [(True, None), (True, 4), (False, None)],
    ids=["mirror-resume", "later-part-resume", "upstream-resume"],
)
def test_1046_interrupted_download_resumes_its_original_source(
    source, mirror_catalog, tmp_path, mirror, split
):
    payload = bytes(range(256)) * 3000
    entry = mirror_catalog(source, payload, split=split)
    target = tmp_path / "download"
    cancel = threading.Event()

    def interrupt(done, total, speed):
        if done >= 256 * 1024:
            cancel.set()

    assert not download_file(
        entry["upstream_url"],
        str(target),
        interrupt,
        cancel,
        use_cache=False,
        use_mirrors=mirror,
    )
    partial = (
        Path(str(target) + f".mirror/part{1 if split else 0:04d}.partial")
        if mirror
        else Path(str(target) + ".partial")
    )
    received = partial.stat().st_size
    first_part_gets = source.get_paths.count("mirror-0")
    assert 0 < received < len(payload)
    assert not target.exists()
    cancel.clear()
    assert download_file(
        entry["upstream_url"], str(target), cancel_event=cancel, use_cache=False
    )
    assert target.read_bytes() == payload
    if mirror and split:
        assert first_part_gets > 0
        assert source.get_paths.count("mirror-0") == first_part_gets
    assert f"bytes={received}-" in source.gets()
    assert set(source.paths) == (
        ({"mirror-0", "mirror-1"} if split else {"mirror-0"})
        if mirror
        else {"upstream"}
    )
    assert not partial.exists()
    assert not Path(str(partial) + ".source").exists()
    assert not Path(str(target) + ".mirror").exists()


@pytest.mark.parametrize(
    "damage", ["torn-tail", "corrupt-first", "corrupt-second", "extra-tail"]
)
def test_1046_resume_verifies_assembled_prefix_before_skipping_parts(
    source, mirror_catalog, tmp_path, damage
):
    payload = b"first part and the rest of the resource"
    entry = mirror_catalog(source, payload, split=10)
    target = tmp_path / "download"
    work = Path(str(target) + ".mirror")
    work.mkdir()
    if damage == "torn-tail":
        saved = payload[:15]
    elif damage == "extra-tail":
        saved = payload + b"uncommitted trailing bytes"
    else:
        index = 0 if damage == "corrupt-first" else 12
        saved = payload[:index] + b"!" + payload[index + 1 :]
    (work / "assembled").write_bytes(saved)
    assert download_file(entry["upstream_url"], str(target), use_cache=False)
    assert target.read_bytes() == payload
    expected_gets = {
        "torn-tail": ["mirror-1"],
        "extra-tail": [],
        "corrupt-first": ["mirror-0", "mirror-1"],
        "corrupt-second": ["mirror-1"],
    }
    assert source.get_paths == expected_gets[damage]
    assert not work.exists()


@pytest.mark.parametrize("available", [True, False])
def test_1046_restricted_resource_never_uses_even_an_accidental_mirror(
    source, mirror_catalog, tmp_path, caplog, available
):
    entry = mirror_catalog(source, b"licensed upstream data", allowed=False)
    if not available:
        source.statuses["upstream"] = 404
    target = tmp_path / "download"
    assert (
        download_file(entry["upstream_url"], str(target), use_cache=False) is available
    )
    assert source.paths and set(source.paths) == {"upstream"}
    if available:
        assert target.read_bytes() == b"licensed upstream data"
    else:
        assert not target.exists()
        assert "UPSTREAM_ONLY" in caplog.text and "Non-commercial" in caplog.text


def test_1046_cancelling_mirror_never_starts_upstream(source, mirror_catalog, tmp_path):
    entry = mirror_catalog(source, b"resource")
    cancel = threading.Event()
    cancel.set()
    assert not download_file(
        entry["upstream_url"], str(tmp_path / "download"), cancel_event=cancel
    )
    assert not source.paths


@needs_tools("bcftools", "bgzip", "tabix")
@pytest.mark.parametrize("corrupt_second_part", [False, True])
def test_1046_gnomad_stream_restarts_without_duplicate_af_rows(
    source, mirror_catalog, tmp_path, monkeypatch, corrupt_second_part
):
    vcf = (
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n"
        '##INFO=<ID=AF_joint,Number=A,Type=Float,Description="AF">\n'
        '##INFO=<ID=AF_grpmax_joint,Number=A,Type=Float,Description="grpmax AF">\n'
        '##INFO=<ID=nhomalt_joint,Number=A,Type=Integer,Description="homozygotes">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tA\tG\t.\tPASS\tAF_joint=0.25;AF_grpmax_joint=0.5;nhomalt_joint=1\n"
        "chr1\t200\t.\tC\tT\t.\tPASS\tAF_joint=0.5\n"
    )
    data = gzip.compress(vcf.encode())
    entry = mirror_catalog(source, data, split=len(data) // 2)
    if corrupt_second_part:
        source.files["mirror-1"] = b"broken"
    monkeypatch.setattr(gnomad_af, "GNOMAD_V4_URL", entry["upstream_url"])
    monkeypatch.setattr(gnomad_af, "GNOMAD_V4_CONTIGS", ["chr1"])
    assert gnomad_af.build_gnomad_af(
        str(tmp_path / "library"), workers=1, attempts=1, download=True
    )
    table = tmp_path / "library/downloads/gnomad_hg38.tsv.gz"
    with gzip.open(table, "rt") as output:
        assert output.read().splitlines()[-2:] == [
            "chr1\t100\tA\tG\t0.25\t0.5\t1",
            "chr1\t200\tC\tT\t0.5\t.\t.",
        ]
    assert ("upstream" in source.paths) is corrupt_second_part
    assert table.with_suffix(table.suffix + ".tbi").is_file()


@needs_tools("bcftools", "bgzip")
@pytest.mark.parametrize("stall", ["body", "headers", "mirror", "segmented-mirror"])
def test_1213_cancelled_gnomad_disconnects_stalled_download(
    source, mirror_catalog, tmp_path, monkeypatch, stall
):
    from wgsextract_cli.core.ref_library import segmented

    data = b"a" * (2 << 20)
    entry = mirror_catalog(source, data, allowed=stall.endswith("mirror"))
    path = "mirror-0" if stall.endswith("mirror") else "upstream"
    if stall == "segmented-mirror":
        from wgsextract_cli.core.ref_library.segmented import ranged_revision

        source.etags = True
        monkeypatch.setattr(segmented, "ranged_revision", ranged_revision)
        monkeypatch.setattr(segmented, "SEGMENTED_MIN_SIZE", len(data))
    started, release, disconnected = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    cancel = api.CancelToken()
    source.body_pauses[path] = (None if stall == "headers" else 1, started, release)
    source.body_disconnects[path] = disconnected
    monkeypatch.setattr(gnomad_af, "GNOMAD_V4_URL", entry["upstream_url"])
    monkeypatch.setattr(gnomad_af, "GNOMAD_V4_CONTIGS", ["chr1"])
    result = []

    def build():
        result.append(
            api.ref.gnomad(
                api.RefGnomadParams(
                    ref=tmp_path, release="4.1", threads=1, download=True
                ),
                cancel=cancel,
                check=False,
            )
        )

    worker = threading.Thread(target=build, daemon=True)
    worker.start()
    try:
        assert started.wait(5), "gnomAD did not begin streaming"
        cancel.cancel()
        worker.join(3)
        assert not worker.is_alive(), "cancelled gnomAD stream did not return promptly"
        assert result[0].exit_code == 130
        assert disconnected.wait(1), "client did not shut down the stalled response"
        assert source.get_paths == [path]
        assert not [file for file in tmp_path.rglob("*.gz*") if file.is_file()]
        if stall.endswith("mirror"):
            assert list(tmp_path.rglob("part0000.partial*"))
    finally:
        release.set()
        worker.join(5)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX SIGTERM")
@needs_tools("bcftools", "bgzip", "tabix")
def test_1213_cli_sigterm_disconnects_gnomad(source, tmp_path):
    source.files["upstream"] = b"a" * (2 << 20)
    started, release, disconnected = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    source.body_pauses["upstream"] = (None, started, release)
    source.body_disconnects["upstream"] = disconnected
    script = (
        "import sys; from wgsextract_cli.core import gnomad_af; "
        "from wgsextract_cli.main import main; "
        "gnomad_af.GNOMAD_V4_URL = sys.argv[1]; "
        "gnomad_af.GNOMAD_V4_CONTIGS = ['chr1']; main(sys.argv[2:])"
    )
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            source.url_for("upstream"),
            "ref",
            "gnomad",
            "--release",
            "4.1",
            "--download",
            "--threads",
            "1",
            "--ref",
            str(tmp_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as cli:
        try:
            assert started.wait(10), (
                f"gnomAD did not request the VCF (exit {cli.poll()})"
            )
            cli.send_signal(signal.SIGTERM)
            assert cli.wait(timeout=4) == 128 + signal.SIGTERM
            assert disconnected.wait(1), "CLI left the stalled response open"
            assert source.get_paths == ["upstream"]
        finally:
            release.set()
            if cli.poll() is None:
                cli.kill()


def test_1046_pr_code_only_edits_probe_no_unrelated_hosts():
    before = {
        "https://upstream/stable-v1": {},
        "https://mirror/a": {"sha256": "a", "size": 2},
    }
    assert checks.changed_urls(before, dict(before)) == {}
    after = {
        "https://upstream/stable-v2": {},
        "https://mirror/a": {"sha256": "b", "size": 2},
    }
    assert checks.changed_urls(before, after) == after


def test_1046_snapshot_resolves_all_contigs_and_keeps_restricted_sources():
    urls = checks.snapshot(checks.ROOT)
    assert all(
        gnomad_af.GNOMAD_V4_URL.format(contig=c) in urls
        for c in gnomad_af.GNOMAD_V4_CONTIGS
    )
    for entry in resource_mirrors.resources().values():
        assert entry["upstream_url"] in urls


@pytest.mark.parametrize("truncated", [False, True])
@pytest.mark.parametrize("licence", ["Public data", "CC0-1.0", "CC-BY-4.0"])
def test_1046_publisher_splits_records_and_keeps_attribution(
    source, tmp_path, monkeypatch, truncated, licence
):
    monkeypatch.setattr(transfer.time, "sleep", lambda seconds: None)
    payload = b"redistributable source bytes"
    source.files.update(upstream=payload, licence=b"Public data source licence")
    entry = {
        "id": "public-data",
        "redistributable": True,
        "upstream_url": source.url_for("upstream"),
        "licence": licence,
        "licence_url": source.url_for("licence"),
        "attribution": "Synthetic contributor",
        "reason": "Unrestricted",
        "version": "1.2.3",
    }
    release = {}

    def github(*args):
        # GitHub is the true external boundary; exercise real HTTP and file IO.
        if args[:2] == ("release", "view"):
            return json.dumps({"assets": list(release.values())})
        if args[:2] == ("release", "upload"):
            path = Path(args[3])
            data = path.read_bytes()
            release[path.name] = {
                "name": path.name,
                "size": len(data),
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "content": data.decode(errors="replace"),
            }
            return ""
        if args[:2] == ("release", "download"):
            directory = Path(args[args.index("--dir") + 1])
            name = args[args.index("--pattern") + 1]
            (directory / name).write_text(release[name]["content"])
            return ""
        pytest.fail(f"Unexpected GitHub operation: {args[:2]}")

    monkeypatch.setattr(publisher, "gh", github)
    monkeypatch.setattr(publisher, "MAX_ASSET_BYTES", 10)
    if licence == "Public data" and not truncated:
        entry.update(upstream_mutable=True, sha256="0" * 64)
    if truncated:
        source.drop_paths.add("upstream")
    record = publisher.publish(entry, "download-resources-test", tmp_path / "publish")
    if truncated:
        assert ("GET", "bytes=5-9") in source.requests
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    assert record["size"] == len(payload) and record["retrieved_at"]
    parts = record["parts"]
    assert (
        "".join(release[p["url"].rsplit("/", 1)[-1]]["content"] for p in parts)
        == payload.decode()
    )
    assert all(p["size"] <= 10 for p in parts)
    assert "Synthetic contributor" in release["public-data.NOTICE.txt"]["content"]
    if licence == "Public data":
        assert (
            release["public-data.LICENCE.txt"]["content"]
            == "Public data source licence"
        )
    else:
        assert "licence" not in source.paths
        for suffix, key in (
            ("LICENCE", "licence_snapshot_provenance"),
            ("LEGALCODE", "legalcode_snapshot_provenance"),
        ):
            content = release[f"public-data.{suffix}.txt"]["content"].encode()
            assert hashlib.sha256(content).hexdigest() == record[key]["sha256"]
            assert record[key]["source_url"] and record[key]["retrieved_at"]
    assert not list((tmp_path / "publish").glob("*.part*"))
    assert json.loads(release["public-data.record.json"]["content"]) == record
    before_assets = dict(release)
    before_requests = list(source.paths)
    source.statuses.update(upstream=403, licence=403)
    assert (
        publisher.publish(entry, "download-resources-test", tmp_path / "publish")
        == record
    )
    assert release == before_assets
    assert source.paths == before_requests
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"resources": [entry]}))
    monkeypatch.setattr(publisher, "MANIFEST", manifest)
    publisher.activate(tmp_path / "publish", "download-resources-test")
    activated = json.loads(manifest.read_text())["resources"][0]
    assert activated["parts"] == record["parts"]
    assert activated["sha256"] == hashlib.sha256(payload).hexdigest()
    manifest.write_text(json.dumps({"resources": [dict(entry, redistributable=False)]}))
    with pytest.raises(ValueError, match="upstream-only"):
        publisher.activate(tmp_path / "publish", "download-resources-test")
    with pytest.raises(ValueError, match="upstream-only"):
        publisher.publish(
            dict(entry, redistributable=False), "download-resources-test", tmp_path
        )


def test_1046_pr_inventory_compares_composed_urls_not_changed_code(tmp_path):
    snapshots = []
    for version in ("1", "2"):
        root = tmp_path / version
        (root / "src").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "src/urls.py").write_text(f'VERSION = "{version}"\n')
        (root / "tests/test_remote_downloads.py").write_text(
            "from urls import VERSION\n"
            "def _download_urls():\n"
            '    return [f"https://upstream/data-v{VERSION}", "https://outage/unchanged"]\n'
        )
        snapshots.append(checks.snapshot(root))
    assert checks.changed_urls(*snapshots) == {"https://upstream/data-v2": {}}


@pytest.mark.parametrize("changed", [None, "checksum", "size"])
def test_1046_nightly_detects_replaced_release_assets(source, monkeypatch, changed):
    source.files["part"] = b"mirrored data"
    digest = hashlib.sha256(b"mirrored data").hexdigest()
    url = "https://github.com/WGSExtract/wgsextract-cli/releases/download/download-resources-test/part"
    metadata = {
        "name": "part",
        "digest": "sha256:" + digest,
        "size": len(b"mirrored data"),
    }
    if changed == "checksum":
        metadata["digest"] = "sha256:" + "0" * 64
    elif changed == "size":
        metadata["size"] += 1
    monkeypatch.setattr(
        checks,
        "urlopen",
        lambda req, timeout: urlopen(source.url_for("part"), timeout=timeout),
    )
    monkeypatch.setattr(
        checks.subprocess,
        "check_output",
        lambda argv, **kwargs: "123" if "--jq" in argv else json.dumps([[metadata]]),
    )
    checks.github_assets.cache_clear()
    pin = {"sha256": digest, "size": len(b"mirrored data")}
    try:
        if changed:
            with pytest.raises(ValueError, match="MIRROR_INTEGRITY"):
                checks.check_url(url, pin)
        else:
            checks.check_url(url, pin)
    finally:
        checks.github_assets.cache_clear()
