"""Exercise repair through the shared downloader with real BGZF and HTTP."""

import hashlib
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools
from tests.http_server import RangeServer, serving
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.ref_library import downloads, segmented
from wgsextract_cli.core.ref_library.segmented import ranged_revision

pytestmark = needs_tools("bgzip")

Panel = tuple[bytes, list[tuple[int, int]]]


@pytest.fixture
def bgzf_panel() -> Panel:
    bgzip = get_tool_path("bgzip")
    assert bgzip
    data = subprocess.run(
        [bgzip, "-c"], input=os.urandom(160_000), capture_output=True, check=True
    ).stdout
    blocks = []
    start = 0
    while start < len(data):
        end = start + int.from_bytes(data[start + 16 : start + 18], "little") + 1
        blocks.append((start, end))
        start = end
    return data, blocks


@pytest.fixture
def server(bgzf_panel: Panel) -> Iterator[RangeServer]:
    with serving(bgzf_panel[0]) as srv:
        srv.etags = True
        yield srv


def _flip_bytes(start: int, body: bytes, *positions: int) -> bytes:
    damaged = bytearray(body)
    for pos in positions:
        if start <= pos < start + len(body):
            damaged[pos - start] ^= 1
    return bytes(damaged)


@pytest.mark.parametrize("kind", ["urllib", "curl", "segmented"])
def test_1619_repairs_only_damaged_bgzf_blocks(
    server: RangeServer,
    bgzf_panel: Panel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    segment = 100_000
    if kind == "curl":
        if shutil.which("curl") is None:
            pytest.skip("needs curl")
        monkeypatch.setattr(downloads, "curl_progress_args", lambda: ["--progress-bar"])
    elif kind == "segmented":
        monkeypatch.setattr(segmented, "ranged_revision", ranged_revision)
        monkeypatch.setattr(segmented, "SEGMENTED_MIN_SIZE", 1)
        monkeypatch.setattr(segmented, "SEGMENT_SIZE", segment)

    data, (_, (first, first_end), (second, second_end), *_) = bgzf_panel
    failed_once = False

    def corrupt(start: int, body: bytes) -> bytes:
        nonlocal failed_once
        if start not in {first, second}:
            # The initial full file or segments damage one CRC and one header.
            return _flip_bytes(start, body, first_end - 8, second)
        if start == first and not failed_once:
            failed_once = True
            return _flip_bytes(start, body, first_end - 8)
        return body

    server.transform_get = corrupt
    dest = tmp_path / "panel.vcf.gz"
    assert downloads.download_file(
        server.url, str(dest), expected_sha256=hashlib.sha256(data).hexdigest()
    )
    assert dest.read_bytes() == data
    gets = server.gets()
    if kind == "segmented":
        assert set(gets[:2]) == {
            f"bytes=0-{segment - 1}",
            f"bytes={segment}-{len(data) - 1}",
        }
        gets = gets[2:]
    else:
        assert gets.pop(0) is None
    assert gets == [
        f"bytes={first}-{first_end - 1}",
        f"bytes={first}-{first_end - 1}",
        f"bytes={second}-{second + 17}",
        f"bytes={second}-{second_end - 1}",
    ]


def test_1619_retries_a_corrupted_header_probe_at_eof(
    server: RangeServer, bgzf_panel: Panel, tmp_path: Path
) -> None:
    data, blocks = bgzf_panel
    eof, end = blocks[-1]
    probe = f"bytes={eof}-{eof + 17}"

    def corrupt(start: int, body: bytes) -> bytes:
        if len(body) == len(data):
            # Damage both magic values, leaving BC intact in the opening block.
            return _flip_bytes(start, body, 0, eof)
        if start == eof and server.gets().count(probe) == 1:
            return body[:16] + b"\xff\xff"
        return body

    server.transform_get = corrupt
    dest = tmp_path / "panel.vcf.gz"
    assert downloads.download_file(
        server.url, str(dest), expected_sha256=hashlib.sha256(data).hexdigest()
    )
    assert dest.read_bytes() == data
    assert server.gets() == [
        None,
        "bytes=0-17",
        f"bytes=0-{blocks[0][1] - 1}",
        probe,
        probe,
        f"bytes={eof}-{end - 1}",
    ]


def test_1619_persistently_corrupt_bgzf_is_not_installed(
    server: RangeServer,
    bgzf_panel: Panel,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data, (_, (start, end), *_) = bgzf_panel
    server.transform_get = lambda offset, body: _flip_bytes(offset, body, end - 8)
    dest = tmp_path / "panel.vcf.gz"
    dest.write_bytes(b"previous verified panel")
    assert not downloads.download_file(
        server.url, str(dest), expected_sha256=hashlib.sha256(data).hexdigest()
    )
    assert dest.read_bytes() == b"previous verified panel"
    assert not Path(f"{dest}.partial").exists()
    assert server.gets() == [None, *([f"bytes={start}-{end - 1}"] * 3)]
    assert server.url in caplog.text
    assert f"bytes {start}-{end - 1}" in caplog.text
    assert "Retry the download" in caplog.text


@pytest.mark.parametrize("pinned", [True, False])
def test_1619_crc_valid_bgzf_uses_agreement_only_when_pinned(
    server: RangeServer, bgzf_panel: Panel, tmp_path: Path, pinned: bool
) -> None:
    data, (_, (start, _), *_) = bgzf_panel
    # gzip's MTIME is not covered by the CRC of the decompressed payload.
    changed = _flip_bytes(0, data, start + 4)
    server.transform_get = lambda offset, body: (
        changed if server.gets() == [None] else body
    )
    dest = tmp_path / "panel.vcf.gz"
    assert downloads.download_file(
        server.url,
        str(dest),
        expected_sha256=hashlib.sha256(data).hexdigest() if pinned else None,
    )
    assert dest.read_bytes() == (data if pinned else changed)
    whole_range = f"bytes=0-{len(data) - 1}"
    assert server.gets() == ([None, whole_range, whole_range] if pinned else [None])
