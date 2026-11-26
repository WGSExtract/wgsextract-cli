"""The site build publishes generated references without committed copies."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_1531_site_build_publishes_live_references_and_cli_cards(
    tmp_path: Path,
) -> None:
    subprocess.run(
        [sys.executable, "scripts/build_site.py", "--output", str(tmp_path)],
        cwd=ROOT,
        check=True,
    )
    pages = {
        name: (tmp_path / f"{name}.html").read_text(encoding="utf-8")
        for name in (
            "api",
            "commands",
            "configuration",
            "certification",
            "cli",
            "reference",
        )
    }
    assert "<table>" in pages["api"] and "MicroarrayParams" in pages["api"]
    assert "<code>vcf snp</code>" in pages["commands"]
    assert "<code>server_max_jobs</code>" in pages["configuration"]
    assert "docs/hardware.md#threads-and-memory" in pages["configuration"]
    assert 'id="journeys"' in pages["cli"] and 'href="commands.html"' in pages["cli"]
    assert "<code>vcf compare</code>" in pages["certification"]
    assert '<tr id="command-vcf-compare">' in pages["certification"]
    assert "issues/1295#issuecomment-" in pages["certification"]
    for name in ("api", "commands", "configuration", "certification"):
        assert f'href="{name}.html"' in pages["reference"]
