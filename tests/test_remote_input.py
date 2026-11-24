"""Byte ranges of a remote CRAM (#655): the unplaced reads' containers of
a CRAM read over https are copied by range, and must arrive whole."""

import io
import os

import pytest

from tests.fake_tools import python_tool, require_bare_name_fakes
from tests.http_server import serving
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.remote_input import check_url, copy_range, remote_file
from wgsextract_cli.core.utils import WGSExtractError


@pytest.mark.parametrize("length", ["not-a-number", "-1"])
def test_1210_malformed_content_length_reports_remote_file_error(length):
    with serving(b"bytes") as srv:
        srv.headers["sample.bam"] = {"Content-Length": length}
        url = srv.url_for("sample.bam") + "?sig=fixture-signature"

        with pytest.raises(WGSExtractError, match="Content-Length") as failure:
            remote_file(url)

    assert srv.url_for("sample.bam") in str(failure.value)
    assert "fixture-signature" not in str(failure.value)


def test_1210_clearing_tool_caches_rechecks_samtools_url_support(tmp_path, monkeypatch):
    require_bare_name_fakes()
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    python_tool(tmp_path, "samtools", "print('libcurl=no')")
    clear_tool_caches()
    try:
        with pytest.raises(WGSExtractError, match="libcurl"):
            check_url("https://example.org/sample.bam")

        python_tool(tmp_path, "samtools", "print('libcurl=yes')")
        clear_tool_caches()
        check_url("https://example.org/sample.bam")
    finally:
        clear_tool_caches()


DATA = bytes(range(256)) * 4096


@pytest.mark.parametrize("end", [None, 300_000])
def test_a_range_is_copied_whole(end):
    with serving(DATA) as server:
        output = io.BytesIO()
        copy_range(server.url, 1000, end, output)
    assert output.getvalue() == DATA[1000:end]


def test_a_range_cut_short_fails():
    """#655 review: a body cut off mid-transfer was accepted, losing the
    unplaced reads after the cut."""
    with serving(DATA) as server:
        server.drop_starts = {1000}
        with pytest.raises(WGSExtractError):
            copy_range(server.url, 1000, None, io.BytesIO())


def test_a_server_ignoring_the_range_fails():
    with serving(DATA) as server:
        server.ignore_ranges = True
        with pytest.raises(WGSExtractError, match="range"):
            copy_range(server.url, 1000, None, io.BytesIO())
