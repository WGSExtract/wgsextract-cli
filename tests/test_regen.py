"""The generated-reference conflict workflow uses real Git and generators."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cli_snapshot import GOLDEN, read_snapshot, snapshot, write_snapshot

from wgsextract_cli.cli.help import SITE_BLOCK_END, SITE_BLOCK_START

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "tests/fixtures/cli_snapshot/microarray.json"
STALE_SNAPSHOT = "tests/fixtures/cli_snapshot/removed/command.json"
GENERATED = (
    SNAPSHOT,
    STALE_SNAPSHOT,
    "docs/api.md",
    "docs/commands.md",
    "docs/configuration.md",
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
    "src/wgsextract_cli/assets/schemas/wgsextract.server.openapi.json",
    "gui/app/shared/generated/server-transport.ts",
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
        "scripts/gen_api_docs.py",
        "scripts/gen_command_docs.py",
        "scripts/gen_config_docs.py",
        "docs/data/configuration-guide.md",
        "scripts/gen_output_schemas.py",
        "scripts/gen_report_schema.py",
        "scripts/gen_server_contracts.py",
        "scripts/json_lines.py",
        "scripts/server_transport_typescript.py",
        "tests/cli_snapshot.py",
        "site_src/cli.md",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    for relative in GENERATED:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("base\n", encoding="utf-8")
    # Stale generated cards, but preserve the handwritten source around them.
    site = tmp_path / "site_src/cli.md"
    text = site.read_text(encoding="utf-8")
    start = text.index(SITE_BLOCK_START)
    end = text.index(SITE_BLOCK_END)
    site.write_text(
        text[:start] + SITE_BLOCK_START + "\nstale\n" + text[end:],
        encoding="utf-8",
    )
    git(tmp_path, "init", "--initial-branch=base")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    return tmp_path


def conflict(repo: Path, paths: tuple[str, ...], *, left_base: str = "base") -> None:
    for branch, base in (("left", left_base), ("right", "base")):
        git(repo, "switch", "-c", branch, base)
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
    retired = "tests/fixtures/api_cli_snapshot.json"
    (regen_repo / retired).write_text("old\n", encoding="utf-8")
    git(regen_repo, "add", retired)
    git(regen_repo, "commit", "-m", "Original snapshot")
    git(regen_repo, "switch", "-c", "prior")
    (regen_repo / retired).write_text("prior change\n", encoding="utf-8")
    git(regen_repo, "commit", "-am", "Update original snapshot")
    git(regen_repo, "switch", "base")
    git(regen_repo, "rm", retired)
    git(regen_repo, "commit", "-m", "Retire original snapshot")
    conflict(regen_repo, GENERATED, left_base="prior")
    assert retired in git(regen_repo, "diff", "--name-only", "--diff-filter=U").stdout
    non_snapshot = regen_repo / "tests/fixtures/cli_snapshot/README.md"
    non_snapshot.write_text("unrelated local note\n", encoding="utf-8")

    # #1471: regenerate to LF even if the existing files use CRLF.
    crlf_artifacts = [
        regen_repo / "docs/configuration.md",
        regen_repo / SNAPSHOT,
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
    snapshot_files = {
        path.relative_to(GOLDEN.parent.parent.parent).as_posix()
        for path in GOLDEN.rglob("*.json")
    }
    assert set(staged) == {*GENERATED, *snapshot_files, "site_src/cli.md"}
    assert not git(regen_repo, "diff", "--name-only").stdout
    assert not (regen_repo / STALE_SNAPSHOT).exists()
    assert not (regen_repo / retired).exists()
    microarray = json.loads((regen_repo / SNAPSHOT).read_text(encoding="utf-8"))
    assert microarray["help"]
    assert "MicroarrayParams" in (regen_repo / "docs/api.md").read_text(
        encoding="utf-8"
    )
    for name in ("docs/commands.md", "site_src/cli.md"):
        assert "microarray" in (regen_repo / name).read_text(encoding="utf-8")
    assert "server_max_jobs" in (regen_repo / "docs/configuration.md").read_text(
        encoding="utf-8"
    )
    for generator in (
        "gen_api_docs.py",
        "gen_command_docs.py",
        "gen_config_docs.py",
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


@pytest.mark.parametrize("first,second", [("align", "vcf snp"), ("vcf snp", "align")])
def test_1531_independent_command_snapshots_survive_merge_queue(
    tmp_path: Path, first: str, second: str
) -> None:
    expected = json.loads(json.dumps(snapshot()))
    folder = tmp_path / "fixtures" / "cli_snapshot"
    base = {
        name: data for name, data in expected.items() if name not in {first, second}
    }
    write_snapshot(base, folder)
    git(tmp_path, "init", "--initial-branch=base")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    for branch, command in (("first", first), ("second", second)):
        git(tmp_path, "switch", "-c", branch, "base")
        write_snapshot({**base, command: expected[command]}, folder)
        git(tmp_path, "add", ".")
        git(tmp_path, "commit", "-m", f"Add {command}")
    git(tmp_path, "switch", "first")
    git(tmp_path, "merge", "--no-edit", "second")
    assert read_snapshot(folder) == expected


def test_989_stale_compact_openapi_reports_readable_field_diff(
    regen_repo: Path,
) -> None:
    regenerated = resolve(regen_repo)
    assert regenerated.returncode == 0, regenerated.stderr
    path = (
        regen_repo / "src/wgsextract_cli/assets/schemas/wgsextract.server.openapi.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    document["info"]["version"] = "stale-version"
    path.write_text(
        json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    checked = subprocess.run(
        [sys.executable, "scripts/gen_server_contracts.py", "--check"],
        cwd=regen_repo,
        capture_output=True,
        text=True,
    )

    assert checked.returncode == 1
    assert (
        "committed/src/wgsextract_cli/assets/schemas/wgsextract.server.openapi.json"
        in checked.stderr
    )
    assert '-    "version": "stale-version",' in checked.stderr
    assert '+    "version":' in checked.stderr
    assert len(checked.stderr) < 5000
    assert (
        json.loads(path.read_text(encoding="utf-8"))["info"]["version"]
        == "stale-version"
    )
