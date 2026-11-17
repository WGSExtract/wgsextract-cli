"""Publisher outage regressions from the first download-resources-1 dispatch."""

import hashlib
import json
from urllib.request import Request, urlopen

import pytest

from scripts import mirror_resource_terms as terms
from scripts import mirror_resource_transfer as transfer
from tests.http_server import RangeServer


@pytest.fixture
def source(monkeypatch):
    server = RangeServer(None)

    def open_http(request, **kwargs):
        assert isinstance(request, Request)
        assert request.get_header("User-agent") == transfer.USER_AGENT
        return urlopen(request, **kwargs)

    monkeypatch.setattr(terms, "urlopen", open_http)
    monkeypatch.setattr(transfer, "urlopen", open_http)
    yield server
    server.close()


@pytest.mark.parametrize("prefer_snapshot", [False, True])
def test_1046_terms_403_uses_committed_audit_text(
    source, tmp_path, monkeypatch, prefer_snapshot
):
    source.statuses["terms"] = 403
    url = source.url_for("terms")
    content = b"Audited source terms: unrestricted primary data."
    (tmp_path / "terms.txt").write_bytes(content)
    index = tmp_path / "sources.json"
    index.write_text(
        json.dumps(
            {
                "sources": {
                    url: {
                        "source_url": url,
                        "retrieved_at": "2026-11-17",
                        "file": "terms.txt",
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                }
            }
        )
    )
    monkeypatch.setattr(terms, "SNAPSHOT_INDEX", index)
    output = tmp_path / "LICENCE.txt"
    terms.save_terms(url, output, prefer_snapshot=prefer_snapshot)
    assert output.read_bytes() == content
    assert source.get_paths == ([] if prefer_snapshot else ["terms"])


def test_1046_live_readme_fetch_uses_raw_content_and_payload_agent(
    source, tmp_path, monkeypatch
):
    source.files["terms"] = b"Source README licence grant"

    def github(request, **kwargs):
        assert (
            request.full_url
            == "https://raw.githubusercontent.com/example/data/revision/README.md"
        )
        assert request.get_header("User-agent") == transfer.USER_AGENT
        return urlopen(
            Request(source.url_for("terms"), headers=dict(request.header_items())),
            **kwargs,
        )

    monkeypatch.setattr(terms, "urlopen", github)
    target = tmp_path / "LICENCE.txt"
    terms.save_terms(
        "https://github.com/example/data/blob/revision/README.md#licence", target
    )
    assert target.read_bytes() == source.files["terms"]


@pytest.mark.parametrize("drop", [False, True])
def test_1046_upload_gaps_do_not_hold_an_upstream_stream_open(source, tmp_path, drop):
    payload = bytes(range(256)) * 600
    source.files["upstream"] = payload
    source.etags = True
    if drop:
        source.drop_paths.add("upstream")
    parts = []

    def upload(path, checksum, size):
        data = path.read_bytes()
        assert len(data) == size
        assert hashlib.sha256(data).hexdigest() == checksum
        parts.append(data)

    record = transfer.transfer_resource(
        source.url_for("upstream"),
        tmp_path,
        "source",
        32768,
        upload,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert b"".join(parts) == payload
    assert record["size"] == len(payload)
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    assert all(request_range is not None for request_range in source.gets())
    if drop:
        assert ("GET", "bytes=16384-32767") in source.requests
    assert not list(tmp_path.iterdir())


def test_1046_non_range_source_is_staged_within_disk_budget(source, tmp_path):
    payload = b"small non-ranged source"
    source.files["upstream"] = payload
    source.ignore_ranges = True
    parts = []
    record = transfer.transfer_resource(
        source.url_for("upstream"),
        tmp_path,
        "source",
        8,
        lambda path, digest, size: parts.append(path.read_bytes()),
    )
    assert b"".join(parts) == payload
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    assert None in source.gets()
    assert not list(tmp_path.iterdir())


def test_1046_oversized_non_range_source_fails_before_full_download(
    source, tmp_path, monkeypatch
):
    source.files["upstream"] = b"larger than the configured staging limit"
    source.ignore_ranges = True
    monkeypatch.setattr(transfer, "MAX_FULL_DOWNLOAD_BYTES", 10)
    with pytest.raises(ValueError, match="RANGE_REQUIRED"):
        transfer.transfer_resource(
            source.url_for("upstream"),
            tmp_path,
            "source",
            8,
            lambda *args: pytest.fail("No incomplete part may be published"),
        )
    assert None not in source.gets()
    assert not list(tmp_path.iterdir())


def test_1046_repeatedly_dropped_part_fails_without_publishing(source, tmp_path):
    source.files["upstream"] = b"x" * 32768
    source.drop_starts = {0, 16384, 24576, 28672, 30720}
    with pytest.raises(OSError, match="Could not finish Range"):
        transfer.transfer_resource(
            source.url_for("upstream"),
            tmp_path,
            "source",
            32768,
            lambda *args: pytest.fail("Incomplete data must not be published"),
        )
    assert not list(tmp_path.iterdir())


def test_1046_unbound_stale_part_cannot_poison_a_new_publication(source, tmp_path):
    payload = b"current immutable source data"
    source.files["upstream"] = payload
    (tmp_path / "source.part0000").write_bytes(b"stale old bytes")
    parts = []
    transfer.transfer_resource(
        source.url_for("upstream"),
        tmp_path,
        "source",
        16,
        lambda path, digest, size: parts.append(path.read_bytes()),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert b"".join(parts) == payload
