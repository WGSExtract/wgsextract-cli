"""`deps list`, `deps install` and `deps uninstall`, behind the desktop app's
Tools page, and the missing optional tools `ref needs` names so the app can
offer "Install and Continue" before a run, as it does for a missing genome."""

import json
import sys

import pytest

from tests.fake_tools import python_tool
from wgsextract_cli.core import windows_arm64
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.tool_catalog import INSTALL_HINTS, platform_unavailable_tools
from wgsextract_cli.main import main

# Finding a tool in another environment sends pixi a multi-line `python -c`
# argument, which a batch-file pixi stand-in can't pass on.
posix_pixi = pytest.mark.skipif(
    sys.platform == "win32", reason="a batch file can't take a multi-line argument"
)


@pytest.fixture(autouse=True)
def _x64_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows ARM64 hosts accept only qualified native tools, not batch files.
    monkeypatch.setattr(windows_arm64, "windows_native_machine", lambda: 0x8664)


@pytest.fixture
def host(fake_pixi, monkeypatch):
    """The CLI in the default environment, which has none of the tools; the
    host's own programs folder alone on PATH."""
    prefix = fake_pixi.env("default")
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "default")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    return fake_pixi.host_bin


@pytest.fixture
def multiqc_env(fake_pixi):
    """MultiQC's own environment, in the lockfile but not installed."""
    prefix = fake_pixi.env("multiqc", locked=("multiqc",), installed=False)
    python_tool(prefix / "bin", "multiqc", "print('multiqc, version 1.33')")
    return prefix


def _json(capsys, *argv: str) -> tuple[int, dict]:
    capsys.readouterr()
    try:
        main(["--json", *argv])
        code = 0
    except SystemExit as exit_:
        code = int(exit_.code or 0)
    return code, json.loads(capsys.readouterr().out)


def _row(document: dict, tool: str) -> dict:
    return next(row for row in document["details"]["tools"] if row["name"] == tool)


def test_list_succeeds_where_check_fails_on_a_missing_mandatory_tool(host, capsys):
    """The Tools page shows what's missing rather than an error."""
    check, _ = _json(capsys, "deps", "check")
    code, document = _json(capsys, "deps", "list")

    assert check == 3
    assert code == 0
    samtools = _row(document, "samtools")
    assert samtools["group"] == "mandatory" and samtools["status"] == "missing"
    assert not samtools["can_install"] and not samtools["can_uninstall"]


def test_list_marks_tools_this_platform_cannot_have(host, fake_pixi, capsys):
    """A tool not built for this OS, or whose environment's lockfile lacks it
    here (PacBio ships pbmm2 for Linux only), is "unavailable", with why."""
    fake_pixi.env("pacbio", locked=("sniffles",), installed=False)

    _, document = _json(capsys, "deps", "list")

    pbmm2 = _row(document, "pbmm2")
    assert pbmm2["status"] == "unavailable" and not pbmm2["can_install"]
    assert "pbmm2" in pbmm2["note"]
    for tool in platform_unavailable_tools():
        row = _row(document, tool)
        assert row["status"] == "unavailable" and not row["can_install"], tool


def test_list_tells_how_to_install_a_tool_the_app_cannot(host, capsys):
    _, document = _json(capsys, "deps", "list")

    plink2 = _row(document, "plink2")
    assert plink2["status"] == "missing" and not plink2["can_install"]
    assert plink2["note"] == INSTALL_HINTS["plink2"]


@pytest.mark.parametrize(
    ("tool", "code", "fact"),
    [
        ("samtools", 2, "mandatory"),
        ("fastp", 1, "default Pixi environment"),
        ("plink2", 1, "cog-genomics.org"),
        ("no-such-tool", 2, "deps list"),
    ],
)
def test_install_refuses_what_it_cannot_install(host, capsys, tool, code, fact):
    exit_code, document = _json(capsys, "deps", "install", tool)

    assert exit_code == code
    assert fact in document["error"]["message"]


def test_uninstall_never_removes_the_environment_the_cli_runs_in(
    host, fake_pixi, monkeypatch, capsys
):
    fake_pixi.env("multiqc", locked=("multiqc",))
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "multiqc")

    code, document = _json(capsys, "deps", "uninstall", "multiqc")

    assert code == 1
    assert "runs in the Pixi environment 'multiqc'" in document["error"]["message"]
    assert fake_pixi.installed("multiqc")


@posix_pixi
def test_install_then_uninstall_an_optional_tool(host, fake_pixi, multiqc_env, capsys):
    _, before = _json(capsys, "deps", "list")
    assert _row(before, "multiqc")["can_install"]

    code, installed = _json(capsys, "deps", "install", "multiqc")
    assert code == 0 and fake_pixi.installed("multiqc")
    assert installed["details"]["environment"] == "multiqc"
    clear_tool_caches()  # as the app's next CLI process starts afresh

    _, after = _json(capsys, "deps", "list")
    row = _row(after, "multiqc")
    assert row["status"] == "installed" and row["can_uninstall"]
    assert row["environment_tools"] == ["multiqc"]

    code, _ = _json(capsys, "deps", "uninstall", "multiqc")
    assert code == 0 and not fake_pixi.installed("multiqc")
    clear_tool_caches()
    assert _row(_json(capsys, "deps", "list")[1], "multiqc")["status"] == "missing"


def test_a_tool_on_path_is_the_users_not_uninstallable(host, fake_pixi, capsys):
    """Only the copy in its own environment is the app's to remove."""
    fake_pixi.env("multiqc", locked=("multiqc",), installed=False)
    python_tool(host, "multiqc", "print('multiqc, version 1.33')")

    _, document = _json(capsys, "deps", "list")

    row = _row(document, "multiqc")
    assert row["status"] == "installed"
    assert not row["can_install"] and not row["can_uninstall"]


def _needs(capsys, *command: str) -> list[dict]:
    capsys.readouterr()
    main(["--json", "ref", "needs", "--", *command])
    return json.loads(capsys.readouterr().out)["details"]["missing"]


def test_needs_names_a_missing_tool_with_its_install(host, multiqc_env, capsys):
    (item,) = _needs(capsys, "qc", "multiqc", "-i", ".")

    assert item["kind"] == "tool" and item["name"] == "multiqc"
    assert item["installArguments"] == ["deps", "install", "multiqc"]


def test_needs_names_a_tool_an_option_asks_for(host, multiqc_env, capsys):
    """`qc fastqc --multiqc` needs MultiQC too; without the flag it doesn't."""
    python_tool(host, "fastqc", "print('FastQC v0.13.0')")

    with_flag = _needs(capsys, "qc", "fastqc", "--multiqc", "-i", "x.bam")
    without = _needs(capsys, "qc", "fastqc", "-i", "x.bam")

    assert [item["name"] for item in with_flag] == ["multiqc"]
    assert without == []


def test_needs_leaves_a_tool_the_user_installs_to_them(host, capsys):
    """plink2 has no environment: the app can only ask the user to install it
    and continue."""
    (item,) = _needs(
        capsys, "score", "pgs", "--build", "GRCh38", "--pgs-id", "PGS000001",
        "--plink2-check", "--input", "x.vcf.gz",
    )  # fmt: skip

    assert item["name"] == "plink2" and item["installArguments"] is None


@posix_pixi
def test_running_the_reported_install_satisfies_the_check(host, multiqc_env, capsys):
    (item,) = _needs(capsys, "qc", "multiqc", "-i", ".")

    main(item["installArguments"])
    clear_tool_caches()

    assert _needs(capsys, "qc", "multiqc", "-i", ".") == []
