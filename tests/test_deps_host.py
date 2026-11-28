"""`deps list`'s account of the computer it runs on, for the desktop app's
Tools page: the OS and CPU named as people say them, why a tool can't be had
here, and the computer's memory and disks."""

import json
import platform
import subprocess
import sys

import pytest

from wgsextract_cli.core import native_support, windows_arm64
from wgsextract_cli.core.tool_catalog import (
    HOST_ONLY_TOOLS,
    PLATFORM_ONLY_TOOLS,
    UNAVAILABLE_WHY,
    host_is_arm64,
    host_label,
    platform_unavailable_tools,
)
from wgsextract_cli.main import main


@pytest.fixture(autouse=True)
def _x64_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows ARM64 hosts accept only qualified native tools, not batch files.
    monkeypatch.setattr(windows_arm64, "windows_native_machine", lambda: 0x8664)


@pytest.fixture
def host(fake_pixi, monkeypatch):
    """The CLI in the default environment, which has none of the tools."""
    prefix = fake_pixi.env("default")
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "default")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    return fake_pixi.host_bin


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


def test_list_describes_the_computer(host, capsys):
    """The Tools page shows the computer the CLI runs on."""
    _, document = _json(capsys, "deps", "list")

    computer = document["details"]["host"]
    assert computer["architecture"] in {"x64", "ARM64"}
    assert computer["os"] and "win32" not in computer["os"]
    assert computer["cpu_threads"] >= 1
    assert 0 < computer["memory_available"] <= computer["memory_total"]
    assert computer["disks"]
    assert "workspace" in [p for disk in computer["disks"] for p in disk["used_for"]]
    assert all(0 <= disk["free"] <= disk["total"] for disk in computer["disks"])


def test_list_reports_the_disks_holding_the_genome_libraries(host, capsys, tmp_path):
    """Reference genomes and My Genomes can sit on their own drive."""
    cfg = tmp_path / "config.toml"
    genomes = (tmp_path / "g").as_posix()
    cfg.write_text(f'reference_genome_library = "{genomes}"\n', encoding="utf-8")
    _, document = _json(capsys, "--config", str(cfg), "deps", "list")

    disks = document["details"]["host"]["disks"]
    places = [p for disk in disks for p in disk["used_for"]]
    assert "reference genomes" in places
    assert "My Genomes" in places


def test_list_still_lists_tools_when_the_computer_wont_describe_itself(
    host, capsys, monkeypatch
):
    import psutil

    def refuse():
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "virtual_memory", refuse)
    code, document = _json(capsys, "deps", "list")

    assert code == 0
    assert document["details"]["host"] == {}
    assert document["details"]["tools"]


def test_list_marks_tools_this_platform_cannot_have(host, fake_pixi, capsys):
    """A tool not built for this OS, or whose environment's lockfile lacks it
    here (PacBio ships pbmm2 for Linux only), is "unavailable", with why."""
    fake_pixi.env("pacbio", locked=("sniffles",), installed=False)

    _, document = _json(capsys, "deps", "list")

    pbmm2 = _row(document, "pbmm2")
    assert pbmm2["status"] == "unavailable" and not pbmm2["can_install"]
    assert pbmm2["note"] == (
        f"pbmm2 isn't available on {host_label()}: "
        "PacBio publishes it on Bioconda for Linux x64 only."
    )
    for tool in platform_unavailable_tools():
        row = _row(document, tool)
        assert row["status"] == "unavailable" and not row["can_install"], tool
        assert row["note"].startswith(f"{tool} isn't available on {host_label()}: ")


def test_every_platform_limited_tool_says_why():
    """The Tools page explains "not available" with the reason, not just the
    platform, so every tool some computer can't have needs one."""
    limited = {
        tool
        for tool, platforms in PLATFORM_ONLY_TOOLS.items()
        if set(platforms) != {"linux", "darwin", "win32"}
    } | set(HOST_ONLY_TOOLS)
    assert limited <= set(UNAVAILABLE_WHY)


@pytest.mark.parametrize(
    ("system", "machine", "label"),
    [
        ("win32", "AMD64", "Windows"),
        ("win32", "ARM64", "Windows ARM64"),
        ("darwin", "arm64", "macOS on Apple silicon"),
        ("darwin", "x86_64", "macOS on Intel"),
        ("linux", "x86_64", "Linux x64"),
        ("linux", "aarch64", "Linux ARM64"),
    ],
)
def test_host_label_names_the_computer_as_people_do(
    monkeypatch, system, machine, label
):
    """WGS Extract is 64-bit only, so Windows is never "win32". The computer's
    own CPU counts, so emulated x64 Python on ARM64 still says ARM64."""
    arm = machine.lower() in {"arm64", "aarch64"}
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(
        windows_arm64, "windows_native_machine", lambda: 0xAA64 if arm else 0x8664
    )
    # macOS asks sysctl for the CPU: x86-64 Python under Rosetta reports x86_64.
    monkeypatch.setattr(
        native_support.subprocess,
        "run",
        lambda args, **_: subprocess.CompletedProcess(args, 0, "1\n" if arm else "0\n"),
    )
    if system == "linux":
        monkeypatch.setattr(platform, "machine", lambda: machine)
    host_is_arm64.cache_clear()
    assert host_label() == label
    host_is_arm64.cache_clear()
