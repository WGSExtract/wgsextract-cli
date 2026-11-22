"""#1225: an interrupted download resumes only the version of the file it
started, against a real local HTTP server. Nothing here reaches the network."""

import hashlib
import json
import os

import pytest

from tests.http_server import RangeServer
from wgsextract_cli.core.ref_library import downloads, segmented
from wgsextract_cli.core.ref_library.segmented import ranged_revision

DATA = os.urandom(600_000)


@pytest.fixture
def server(monkeypatch):
    """One-connection urllib downloads (no curl, no GitHub lookup, no sleeping)."""
    srv = RangeServer(DATA)
    monkeypatch.setattr(
        downloads, "resolve_github_release_asset_sha256", lambda u: None
    )
    monkeypatch.setattr(downloads, "curl_progress_args", lambda: ["--silent"])
    monkeypatch.setattr(downloads.time, "sleep", lambda s: None)
    yield srv
    srv.close()


@pytest.fixture
def segmented_server(server, monkeypatch):
    """Ranged downloads in 1000-byte pieces, one attempt each."""
    server.data = DATA[:10_000]
    monkeypatch.setattr(segmented, "ranged_revision", ranged_revision)
    monkeypatch.setattr(segmented, "SEGMENTED_MIN_SIZE", 1)
    monkeypatch.setattr(segmented, "SEGMENT_SIZE", 1000)
    monkeypatch.setattr(segmented, "ATTEMPTS", 1)
    return server


# #1225: a partial was resumed by its URL alone, so a file replaced at that URL
# by same-sized bytes was installed as the old version's head and the new one's
# tail. Same size: only the version tells the two apart.
NEW = os.urandom(len(DATA))


def _validators(server: RangeServer, kind: str) -> None:
    server.etags = kind.startswith("etag")
    server.dates = kind == "date"
    server.ignore_if_range = kind == "etag, If-Range ignored"


@pytest.mark.parametrize("kind", ["etag", "date", "etag, If-Range ignored", "none"])
def test_1225_a_resume_after_the_file_changed_installs_only_the_new_version(
    server, tmp_path, kind
):
    _validators(server, kind)
    dest = tmp_path / "hs38.fa.gz"
    server.leave_partial(server.url, dest, DATA[: len(DATA) // 2])
    server.data = NEW

    assert downloads.download_file(server.url, str(dest))

    assert dest.read_bytes() == NEW


@pytest.mark.parametrize("kind", ["etag", "date"])
def test_1225_an_unchanged_file_still_resumes_where_it_stopped(server, tmp_path, kind):
    _validators(server, kind)
    dest = tmp_path / "hs38.fa.gz"
    server.leave_partial(server.url, dest, DATA[: len(DATA) // 2])

    assert downloads.download_file(server.url, str(dest))

    assert dest.read_bytes() == DATA
    assert server.gets() == [f"bytes={len(DATA) // 2}-"]


@pytest.mark.parametrize("changed", [False, True])
def test_1225_without_validators_only_a_pinned_checksum_allows_a_resume(
    server, tmp_path, monkeypatch, changed
):
    """Nothing names the version the partial came from, but a mix can't pass
    the checksum: it fails, leaving the installed file as it was."""
    _validators(server, "none")
    digest = hashlib.sha256(DATA).hexdigest()
    monkeypatch.setattr(
        downloads, "resolve_github_release_asset_sha256", lambda _: digest
    )
    dest = tmp_path / "hs38.fa.gz"
    server.leave_partial(server.url, dest, DATA[: len(DATA) // 2])
    dest.write_bytes(b"installed earlier")
    if changed:
        server.data = NEW

    assert downloads.download_file(server.url, str(dest)) is not changed

    assert dest.read_bytes() == (b"installed earlier" if changed else DATA)
    assert server.gets() == [f"bytes={len(DATA) // 2}-"]


@pytest.mark.parametrize("kind", ["etag", "date", "etag, If-Range ignored"])
def test_1225_a_segmented_resume_after_the_file_changed_installs_only_the_new_version(
    segmented_server, tmp_path, kind
):
    srv = segmented_server
    _validators(srv, kind)
    srv.drop_starts = {5000}
    dest = tmp_path / "hs38.fa.gz"
    assert not downloads.download_file(srv.url, str(dest))
    srv.drop_starts = set()
    srv.data = new = NEW[: len(srv.data)]

    assert downloads.download_file(srv.url, str(dest))

    assert dest.read_bytes() == new


def test_1225_a_file_that_changes_between_pieces_is_never_installed(
    segmented_server, tmp_path
):
    """The size probe fails, so the pieces left are fetched by the version the
    first run recorded: the server's newer file is refused, not mixed in."""
    srv = segmented_server
    srv.etags = True
    srv.drop_starts = {5000}
    dest = tmp_path / "hs38.fa.gz"
    assert not downloads.download_file(srv.url, str(dest))
    srv.drop_starts = set()
    srv.data = new = NEW[: len(srv.data)]
    srv.head_status = 405

    assert not downloads.download_file(srv.url, str(dest))
    assert not dest.exists()

    srv.head_status = None
    assert downloads.download_file(srv.url, str(dest))
    assert dest.read_bytes() == new


def test_1225_an_unchanged_segmented_download_resumes_only_its_missing_pieces(
    segmented_server, tmp_path
):
    srv = segmented_server
    srv.dates = True
    srv.drop_starts = {5000}
    dest = tmp_path / "hs38.fa.gz"
    assert not downloads.download_file(srv.url, str(dest))
    state = json.loads((tmp_path / "hs38.fa.gz.partial.segments").read_text())
    srv.drop_starts = set()
    srv.requests.clear()

    assert downloads.download_file(srv.url, str(dest))

    assert dest.read_bytes() == srv.data
    missing = [
        f"bytes={int(start) + count}-{int(start) + 999}"
        for start, count in state["received"].items()
        if count < 1000
    ]
    assert "bytes=5500-5999" in missing
    assert sorted(r for r in srv.gets() if r) == sorted(missing)
