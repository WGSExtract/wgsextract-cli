"""Certification discovery, evidence and execution contracts (#1596)."""

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from jsonschema import Draft202012Validator

from tests.htslib_helpers import needs_htslib
from wgsextract_cli.commands._certification import (
    certification_paths,
)
from wgsextract_cli.core.certification import CertificationLevel
from wgsextract_cli.core.results import load_schema
from wgsextract_cli.main import main


def test_1596_all_commands_have_reviewed_certification(capsys):
    main(["help", "--all", "--json", "--full"])
    document = json.loads(capsys.readouterr().out)
    commands = document["commands"]
    assert set(certification_paths()) == {command["path"] for command in commands}
    assert set(document["certification_evidence"]) == set(certification_paths())
    for command in commands:
        level = CertificationLevel(command["certification"])
        evidence = document["certification_evidence"][command["path"]]
        assert evidence["summary"] and date.fromisoformat(evidence["reviewed"])
        if level in {CertificationLevel.CERTIFIED, CertificationLevel.VALIDATED}:
            assert evidence["platforms"] and evidence["links"], command["path"]
            assert all(
                urlsplit(link).scheme == "https" and urlsplit(link).netloc
                for link in evidence["links"]
            )
        if level is CertificationLevel.CERTIFIED:
            assert {"Linux", "macOS", "Windows"} <= set(evidence["platforms"])

    main(["help", "vcf", "compare"])
    assert "Certification: Validated" in capsys.readouterr().out
    main(["help", "vcf", "compare", "--json"])
    (compare,) = json.loads(capsys.readouterr().out)["commands"]
    assert compare["certification_evidence"]["links"]


@needs_htslib
@pytest.mark.parametrize("suppress", [False, True])
def test_1596_certification_notice_is_configurable_but_manifest_keeps_level(
    tmp_path, capsys, suppress
):
    config = tmp_path / "config.toml"
    config.write_text(
        "certification_notices = false\n" if suppress else "", encoding="utf-8"
    )
    ref = tmp_path / "sample.fa"
    ref.write_text(">chr1\nACGTNNACGT\n", encoding="utf-8")
    main(["--config", str(config), "--json", "ref", "count-ns", "--ref", str(ref)])
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    Draft202012Validator(load_schema()).validate(doc)
    assert doc["certification"] == "Tested"
    manifest = json.loads(Path(doc["manifest"]).read_text(encoding="utf-8"))
    assert manifest["certification"] == "Tested"
    assert doc["warnings"] == manifest["warnings"] == []
    notices = [
        line for line in captured.err.splitlines() if "Tested certification" in line
    ]
    assert len(notices) == (0 if suppress else 1)
