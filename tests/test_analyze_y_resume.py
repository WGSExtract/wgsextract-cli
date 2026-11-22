"""Y-lineage decisions and resume in the comprehensive workflow."""

import json
from pathlib import Path

import pytest

from tests.test_analyze_workflows import FakeSteps
from tests.test_analyze_workflows import bam as bam
from tests.test_analyze_workflows import fake as fake
from wgsextract_cli.ydna.yleaf_native.trees import tree_metadata


def test_1215_stale_info_cache_does_not_skip_y_lineage(
    fake: FakeSteps, bam: str, tmp_path: Path
) -> None:
    out = tmp_path / "o"
    out.mkdir()
    cache = out / f"{Path(bam).name}.wgse_info.json"
    cache.write_text(json.dumps({"gender": "Female"}))
    fake.leave_info_cache = True
    run = fake.run("--input", bam, "--outdir", str(out), "--skip-calling")
    assert run.code == 0
    assert run.ran == ["info", "mt-haplogroup", "y-haplogroup"]
    assert json.loads(cache.read_text())["gender"] == "Female"


@pytest.mark.parametrize("tree_id", ["ftdna", "isogg"])
def test_issue1168_updated_polarity_pin_reruns_y_lineage_on_resume(
    fake: FakeSteps,
    bam: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tree_id: str,
) -> None:
    fake.settings["y_tree"] = tree_id
    args = ("--input", bam, "--outdir", str(tmp_path / "out"), "--skip-calling")
    polarity = tree_metadata(tree_id)["modules"]["polarity"]
    first = fake.run(*args)
    assert first.code == 0
    assert first.ran == ["info", "mt-haplogroup", "y-haplogroup"]
    assert fake.run(*args).ran == []

    # Simulate upgrading only the bundled module pin, not the underlying tree.
    monkeypatch.setitem(polarity, "sha256", "0" * 64)
    resumed = fake.run(*args)
    assert resumed.code == 0
    assert resumed.ran == ["y-haplogroup"]
    assert fake.run(*args).ran == []
