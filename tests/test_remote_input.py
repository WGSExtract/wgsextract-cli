"""Byte ranges of a remote CRAM (#655): the unplaced reads' containers of
a CRAM read over https are copied by range, and must arrive whole."""

import io

import pytest

from tests.http_server import serving
from wgsextract_cli.core.remote_input import copy_range
from wgsextract_cli.core.utils import WGSExtractError

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
