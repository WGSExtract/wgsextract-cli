"""The generated-reference conflict workflow uses real Git and generators."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.home_isolation import isolated_home_env

ROOT = Path(__file__).resolve().parents[1]
GENERATED = (
    "src/wgsextract_cli/assets/schemas/wgsextract.sample.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.steps.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.result.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.roh.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.recheck.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.delta.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.methylation-regions-hp.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.paraphase.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.panel.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.report.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.server.responses.1.schema.json",
    "gui/app/shared/generated/result-records.ts",
    "gui/app/shared/generated/report-records.ts",
)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Regeneration test",
            "-c",
            "user.email=regen@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def regen_repo(tmp_path: Path) -> Path:
    for relative in (
        "scripts/regen.py",
        "scripts/gen_output_schemas.py",
        "scripts/gen_report_schema.py",
        "scripts/gen_server_contracts.py",
        "scripts/gen_server_openapi.py",
        "scripts/json_lines.py",
        "scripts/server_transport_typescript.py",
        "site_src/cli.md",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    for relative in GENERATED:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("base\n", encoding="utf-8")
    git(tmp_path, "init", "--initial-branch=base")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    return tmp_path


def conflict(repo: Path, paths: tuple[str, ...]) -> None:
    for branch in ("left", "right"):
        git(repo, "switch", "-c", branch, "base")
        for relative in paths:
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{branch}\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", branch)
    assert git(repo, "merge", "--no-commit", "left", check=False).returncode == 1


def resolve(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/regen.py", "--resolve"],
        cwd=repo,
        env={**os.environ, **isolated_home_env(repo / "home")},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("source", ["src/problem.py", "site_src/cli.md"])
def test_regen_resolve_refuses_unresolved_sources(
    regen_repo: Path, source: str
) -> None:
    conflict(regen_repo, (source,))
    before = {name: (regen_repo / name).read_bytes() for name in GENERATED}

    result = resolve(regen_repo)

    assert result.returncode == 2
    assert source in result.stderr and "conflicts" in result.stderr
    assert {name: (regen_repo / name).read_bytes() for name in GENERATED} == before
    assert (
        git(regen_repo, "diff", "--name-only", "--diff-filter=U").stdout.strip()
        == source
    )


def test_regen_resolve_rebuilds_and_stages_all_generated_outputs(
    regen_repo: Path,
) -> None:
    conflict(regen_repo, GENERATED)

    # #1471: regenerate to LF even if the existing files use CRLF.
    crlf_artifacts = [
        regen_repo
        / "src/wgsextract_cli/assets/schemas/wgsextract.sample.1.schema.json",
    ]
    for path in crlf_artifacts:
        path.write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8", newline="\r\n"
        )

    result = resolve(regen_repo)

    assert result.returncode == 0, result.stderr
    for path in crlf_artifacts:
        output = path.read_bytes()
        assert b"\r" not in output and output.count(b"\n") > 1
    assert not git(regen_repo, "diff", "--name-only", "--diff-filter=U").stdout
    staged = git(regen_repo, "diff", "--cached", "--name-only").stdout.splitlines()
    assert set(staged) == set(GENERATED)
    assert not git(regen_repo, "diff", "--name-only").stdout
    [cache] = (regen_repo / "home").rglob("help-index.json")
    index = json.loads(cache.read_bytes())["index"]
    assert any(item["path"] == "vcf snp" for item in index["commands"])
    document = json.loads(
        (regen_repo / "out/contracts/wgsextract.server.openapi.json").read_text(
            encoding="utf-8"
        )
    )
    assert "/v1/ops/microarray" in document["paths"]
    assert "export interface Job {" in (
        regen_repo / "gui/app/shared/generated/server-transport.ts"
    ).read_text(encoding="utf-8")
    for generator in (
        "gen_output_schemas.py",
        "gen_report_schema.py",
        "gen_server_contracts.py",
    ):
        checked = subprocess.run(
            [sys.executable, f"scripts/{generator}", "--check"],
            cwd=regen_repo,
            capture_output=True,
            text=True,
        )
        assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize(
    "retired", ["docs/commands.md", "src/wgsextract_cli/assets/cli/help-index.json"]
)
def test_1531_regen_resolve_deletes_retired_reference_from_older_branch(
    regen_repo: Path,
    retired: str,
) -> None:
    old_reference = regen_repo / retired
    old_reference.parent.mkdir(parents=True, exist_ok=True)
    old_reference.write_text("before\n", encoding="utf-8")
    git(regen_repo, "add", retired)
    git(regen_repo, "commit", "-m", "Original reference")
    git(regen_repo, "switch", "-c", "prior")
    old_reference.write_text("prior change\n", encoding="utf-8")
    git(regen_repo, "commit", "-am", "Update original reference")
    git(regen_repo, "switch", "base")
    git(regen_repo, "rm", retired)
    git(regen_repo, "commit", "-m", "Retire original reference")
    assert git(regen_repo, "merge", "--no-commit", "prior", check=False).returncode == 1

    result = resolve(regen_repo)

    assert result.returncode == 0, result.stderr
    assert not old_reference.exists()
    assert not git(regen_repo, "diff", "--name-only", "--diff-filter=U").stdout


def test_989_stale_server_response_schema_reports_readable_field_diff(
    regen_repo: Path,
) -> None:
    regenerated = resolve(regen_repo)
    assert regenerated.returncode == 0, regenerated.stderr
    path = (
        regen_repo
        / "src/wgsextract_cli/assets/schemas/wgsextract.server.responses.1.schema.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    document["$id"] = "stale-schema-id"
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    checked = subprocess.run(
        [sys.executable, "scripts/gen_server_contracts.py", "--check"],
        cwd=regen_repo,
        capture_output=True,
        text=True,
    )

    assert checked.returncode == 1
    assert (
        "committed/src/wgsextract_cli/assets/schemas/wgsextract.server.responses.1.schema.json"
        in checked.stderr
    )
    assert '-  "$id": "stale-schema-id",' in checked.stderr
    assert '+  "$id": "urn:wgsextract:server:responses:1",' in checked.stderr
    assert len(checked.stderr) < 5000
    assert json.loads(path.read_text(encoding="utf-8"))["$id"] == "stale-schema-id"
