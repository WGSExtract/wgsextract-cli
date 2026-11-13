"""Hosted markers and browser API outages must never redirect notes to localStorage."""

import shutil
import subprocess
from pathlib import Path

import pytest
from test_mockup_review_server import SCRIPT, read, running
from test_mockup_review_server import bare_repo as bare_repo


def test_776_hosted_pages_and_versions_mark_shared_storage(
    tmp_path: Path, bare_repo: Path
) -> None:
    with running(
        tmp_path, "--db", str(tmp_path / "notes.db"), "--repo", str(bare_repo)
    ) as base:
        for prefix in ("", "/preview/x"):
            assert 'name="wgsm-review-server" content="hosted"' in read(
                base, prefix + "/"
            )
            assert '"hosted": true' in read(base, prefix + "/shared/version.js")


def test_776_api_outage_does_not_write_browser_notes() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the mockup browser storage regressions")
    subprocess.run(
        [node, "--test", str(SCRIPT.parents[2] / "tests/mockup_store_checks.cjs")],
        check=True,
        capture_output=True,
        timeout=30,
    )
