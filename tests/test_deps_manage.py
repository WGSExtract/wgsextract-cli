"""`deps list`, `deps install` and `deps uninstall`, behind the desktop app's
Tools page, and the missing optional tools `ref needs` names so the app can
offer "Install and Continue" before a run, as it does for a missing genome."""

import json
import sys

import pytest

from tests.fake_tools import python_tool
from wgsextract_cli.core import runtime, windows_arm64
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.optional_tools import managed_binary
from wgsextract_cli.core.tool_catalog import (
    INSTALL_HINTS,
    PIXI_TOOL_ENVS,
    optional_dependency_tools,
    platform_unavailable_tools,
)
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

    dorado = _row(document, "dorado")
    assert dorado["status"] == "missing" and not dorado["can_install"]
    assert dorado["note"] == INSTALL_HINTS["dorado"]


@pytest.mark.parametrize(
    ("tool", "code", "fact"),
    [
        ("samtools", 2, "mandatory"),
        ("fastp", 1, "default Pixi environment"),
        ("dorado", 1, "nanoporetech/dorado"),
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
    """dorado has no environment: the app can only ask the user to install it
    and continue."""
    (item,) = _needs(capsys, "basecall", "--input", "pod5")

    assert item["name"] == "dorado" and item["installArguments"] is None


_PLINK2_CHECK = (
    "score", "pgs", "--build", "GRCh38", "--pgs-id", "PGS000001",
    "--plink2-check", "--input", "x.vcf.gz",
)  # fmt: skip


@pytest.mark.skipif(sys.platform == "win32", reason="Windows downloads plink2")
def test_plink2_installs_from_its_own_environment(host, fake_pixi, capsys):
    """score pgs --plink2-check offers plink2's Pixi environment (Linux, macOS)."""
    fake_pixi.env("plink2", locked=("plink2",), installed=False)

    row = _row(_json(capsys, "deps", "list")[1], "plink2")
    (item,) = _needs(capsys, *_PLINK2_CHECK)

    assert row["status"] == "missing" and row["can_install"]
    assert row["environment"] == "plink2"
    assert item["installArguments"] == ["deps", "install", "plink2"]


@posix_pixi
def test_running_the_reported_install_satisfies_the_check(host, multiqc_env, capsys):
    (item,) = _needs(capsys, "qc", "multiqc", "-i", ".")

    main(item["installArguments"])
    clear_tool_caches()

    assert _needs(capsys, "qc", "multiqc", "-i", ".") == []


# Review of the Tools page PR: uninstall must not remove what isn't the
# app's, and `ref needs` must ask for what the run will really use.


def test_uninstall_refuses_the_environment_running_the_cli_by_prefix(
    host, fake_pixi, monkeypatch, capsys
):
    """Started as .pixi/envs/multiqc/bin/wgsextract, Pixi names no environment;
    the running Python's prefix still shows it."""
    prefix = fake_pixi.env("multiqc", locked=("multiqc",))
    monkeypatch.delenv("PIXI_ENVIRONMENT_NAME")
    monkeypatch.setattr(sys, "prefix", str(prefix))

    code, document = _json(capsys, "deps", "uninstall", "multiqc")

    assert code == 1 and "runs in the Pixi environment" in document["error"]["message"]
    assert fake_pixi.installed("multiqc")


def test_management_needs_this_installations_own_manifest(
    host, fake_pixi, monkeypatch, capsys
):
    """Without one, Pixi would act on whatever workspace the current folder
    is in, another project's included."""
    fake_pixi.env("multiqc", locked=("multiqc",))
    monkeypatch.setattr(runtime, "source_checkout_root", lambda: None)

    code, document = _json(capsys, "deps", "uninstall", "multiqc")

    assert code != 0 and "pixi.toml" in document["error"]["message"]
    assert fake_pixi.installed("multiqc")


def test_a_copy_in_another_environment_is_not_uninstallable(
    host, fake_pixi, monkeypatch, capsys
):
    """MultiQC from the active (dev) environment is reported as Pixi's, but
    cleaning the multiqc environment wouldn't remove it."""
    fake_pixi.env("multiqc", locked=("multiqc",))
    python_tool(host, "multiqc", "print('multiqc, version 1.33')")
    monkeypatch.setenv("PIXI_PREFIX", str(host.parent))

    _, document = _json(capsys, "deps", "list")

    row = _row(document, "multiqc")
    assert row["status"] == "installed" and not row["can_uninstall"]


def test_an_unanswered_lockfile_query_does_not_make_a_tool_unavailable(
    host, fake_pixi, capsys
):
    fake_pixi.env("multiqc", locked=("multiqc",), installed=False)
    fake_pixi.set(list_stdout="", list_returncode=1)

    _, document = _json(capsys, "deps", "list")

    row = _row(document, "multiqc")
    assert row["status"] == "missing" and row["can_install"]


# Tools with a Pixi environment and a native release the user installs.
MANUAL_ELSEWHERE = [tool for tool in INSTALL_HINTS if tool in PIXI_TOOL_ENVS]


@pytest.mark.parametrize("tool", MANUAL_ELSEWHERE)
def test_a_native_release_without_a_pixi_build_is_installed_by_hand(
    host, fake_pixi, capsys, tool
):
    """wgse-deepvariant on Apple Silicon has no Pixi build there, but a
    release the user installs: missing, with how, not unavailable."""
    if tool not in optional_dependency_tools():
        pytest.skip(f"{tool} isn't supported on this host")
    if managed_binary(tool):
        pytest.skip(f"deps install downloads {tool}'s release on this host")
    fake_pixi.env(PIXI_TOOL_ENVS[tool], locked=(), installed=False)

    _, document = _json(capsys, "deps", "list")

    row = _row(document, tool)
    assert row["status"] == "missing" and not row["can_install"]
    assert row["note"] == INSTALL_HINTS[tool]


def test_needs_ignores_a_path_named_like_a_tool(host, fake_pixi, capsys):
    """`qc multiqc --input fastqc` reads a folder called fastqc; it doesn't
    need FastQC."""
    python_tool(host, "multiqc", "print('multiqc, version 1.33')")

    assert _needs(capsys, "qc", "multiqc", "--input", "fastqc") == []


@posix_pixi
def test_needs_matches_a_caller_whatever_its_case(host, fake_pixi, capsys):
    """--caller expansionhunter runs ExpansionHunter."""
    fake_pixi.env("expansionhunter", locked=("expansionhunter",), installed=False)

    missing = _needs(
        capsys, "typing", "str", "--caller", "expansionhunter", "--input", "x.bam"
    )

    assert [item["name"] for item in missing] == ["ExpansionHunter"]


def test_needs_follows_the_vep_engine_chosen(host, fake_pixi, capsys):
    """Ensembl VEP's cache is a download (curl, tar); vep-rs builds its own."""
    fake_pixi.env("vep-rs", locked=("vep-rs",), installed=False)

    ensembl = _needs(capsys, "vep", "download", "--engine", "ensembl")
    vep_rs = _needs(capsys, "vep", "download", "--engine", "vep-rs")

    assert "vep-cache-builder" not in [item["name"] for item in ensembl]
    assert "vep-cache-builder" in [item["name"] for item in vep_rs]


def test_needs_glimpse2_only_to_impute_from_reads(host, fake_pixi, capsys):
    if "GLIMPSE2_phase" in platform_unavailable_tools():
        pytest.skip("no GLIMPSE2 build for this platform")
    fake_pixi.env("imputation", locked=("glimpse-bio",), installed=False)

    from_reads = _needs(capsys, "vcf", "impute", "--input", "x.bam")
    from_vcf = _needs(capsys, "vcf", "impute", "--vcf-input", "x.vcf.gz")

    assert {"GLIMPSE2_phase", "GLIMPSE2_ligate"} <= {i["name"] for i in from_reads}
    assert not {"GLIMPSE2_phase", "GLIMPSE2_ligate"} & {i["name"] for i in from_vcf}


@posix_pixi
def test_needs_whatshap_when_no_phaser_is_installed(host, fake_pixi, capsys):
    if "whatshap" in platform_unavailable_tools():
        pytest.skip("no WhatsHap build for this platform")
    fake_pixi.env("phasing", locked=("whatshap", "longphase"), installed=False)

    missing = _needs(capsys, "vcf", "phase", "--input", "x.bam")

    assert "whatshap" in [item["name"] for item in missing]


def test_needs_pharmcats_own_environment(host, fake_pixi, capsys):
    fake_pixi.env("pharmcat", locked=("pandas",), installed=False)

    missing = _needs(capsys, "vcf", "pharmcat", "--vcf-input", "x.vcf.gz")

    (item,) = [item for item in missing if item["name"] == "pharmcat_python"]
    assert item["installArguments"] == ["deps", "install", "pharmcat_python"]


def test_needs_trusts_an_executable_the_user_named(host, capsys):
    """basecall --dorado-path is checked by basecall itself."""
    named = _needs(capsys, "basecall", "--dorado-path", "dorado", "--input", "pod5")
    unnamed = _needs(capsys, "basecall", "--input", "pod5")

    assert "dorado" not in [item["name"] for item in named]
    assert "dorado" in [item["name"] for item in unnamed]


def test_no_install_offer_where_the_cli_cannot_manage_pixi(
    host, fake_pixi, monkeypatch, capsys
):
    """A CLI without its own pixi.toml (pip-installed) can't run `deps
    install`: the Tools page and the dialog send the user to install it."""
    fake_pixi.env("multiqc", locked=("multiqc",), installed=False)
    monkeypatch.setattr(runtime, "source_checkout_root", lambda: None)

    _, document = _json(capsys, "deps", "list")
    (item,) = _needs(capsys, "qc", "multiqc", "-i", ".")

    assert not _row(document, "multiqc")["can_install"]
    assert item["name"] == "multiqc" and item["installArguments"] is None


def test_deleting_imputation_panels_needs_no_glimpse2(host, fake_pixi, capsys):
    if "GLIMPSE2_chunk" in platform_unavailable_tools():
        pytest.skip("no GLIMPSE2 build for this platform")
    fake_pixi.env("imputation", locked=("glimpse-bio",), installed=False)

    deleting = _needs(capsys, "ref", "imputation", "--delete", "--chromosomes", "chr20")
    installing = _needs(
        capsys, "ref", "imputation", "--install", "--chromosomes", "chr20"
    )

    assert deleting == []
    assert "GLIMPSE2_chunk" in [item["name"] for item in installing]


def test_discvrseq_install_and_uninstall_managed_jar(
    host, fake_pixi, tmp_path, monkeypatch, capsys
):
    """discvrseq installs as a managed JAR into jar_directory, ref needs offers
    both java17 and discvrseq for Install and Continue, and discvrseq uninstalls cleanly."""
    from pathlib import Path

    from wgsextract_cli.core.optional_tools import MANAGED_JAR_TOOLS

    fake_pixi.env("java17", locked=("openjdk",), installed=False)
    jar_dir = tmp_path / "jartools"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'jar_directory = "{jar_dir.as_posix()}"\n', encoding="utf-8")
    python_tool(
        host,
        "java",
        "import sys; print('Version: 1.3.92' if '-jar' in sys.argv else 'openjdk version \"25.0.2\"')",
    )
    clear_tool_caches()

    # Before install: both java17 and discvrseq are missing and installable, and
    # ref needs asks for BOTH for `vcf variantqc`.
    _, before = _json(capsys, "--config", str(cfg), "deps", "list")
    row = _row(before, "discvrseq")
    assert (
        row["status"] == "missing" and row["can_install"] and not row["can_uninstall"]
    )
    assert row["environment"] == "jartools"
    java17_row = _row(before, "java17")
    assert java17_row["status"] == "missing" and java17_row["can_install"]

    missing = _needs(
        capsys, "--config", str(cfg), "vcf", "variantqc", "--vcf-input", "x.vcf.gz"
    )
    by_name = {item["name"]: item for item in missing}
    assert by_name["java17"]["installArguments"][-3:] == ["deps", "install", "java17"]
    assert by_name["discvrseq"]["installArguments"][-3:] == [
        "deps",
        "install",
        "discvrseq",
    ]

    # Mock only the network download boundary to place the JAR file.
    def fake_download(url, dest, **_kwargs):
        assert url == MANAGED_JAR_TOOLS["discvrseq"].url
        Path(dest).write_bytes(b"PK\x03\x04fake-discvrseq-jar")
        return True

    monkeypatch.setattr(
        "wgsextract_cli.core.ref_library.downloads.download_file", fake_download
    )

    code, installed = _json(
        capsys, "--config", str(cfg), "deps", "install", "discvrseq"
    )
    assert code == 0
    jar_path = Path(installed["details"]["jar_path"])
    assert jar_path.is_file() and jar_path.parent == jar_dir
    clear_tool_caches()

    # After install: installed, version probed, uninstallable, and ref needs satisfied.
    _, after = _json(capsys, "--config", str(cfg), "deps", "list")
    row = _row(after, "discvrseq")
    assert (
        row["status"] == "installed" and row["can_uninstall"] and not row["can_install"]
    )
    assert row["version"] == "Version: 1.3.92"
    assert not any(
        item["name"] == "discvrseq"
        for item in _needs(
            capsys, "--config", str(cfg), "vcf", "variantqc", "--vcf-input", "x.vcf.gz"
        )
    )

    # Uninstall removes only the managed JAR.
    code, removed = _json(
        capsys, "--config", str(cfg), "deps", "uninstall", "discvrseq"
    )
    assert code == 0 and not jar_path.exists()
    assert removed["details"]["jar_path"] == str(jar_path)
    clear_tool_caches()
    assert (
        _row(_json(capsys, "--config", str(cfg), "deps", "list")[1], "discvrseq")[
            "status"
        ]
        == "missing"
    )


def test_user_configured_discvrseq_jar_is_not_uninstallable(host, tmp_path, capsys):
    """A user-supplied discvrseq_jar outside managed_jar_dir() is never removed."""
    managed = tmp_path / "managed"
    custom = tmp_path / "custom" / "DISCVRSeq.jar"
    custom.parent.mkdir()
    custom.write_bytes(b"PK\x03\x04custom")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'jar_directory = "{managed.as_posix()}"\n'
        f'discvrseq_jar = "{custom.as_posix()}"\n',
        encoding="utf-8",
    )
    python_tool(
        host,
        "java",
        "import sys; print('Version: 1.3.92' if '-jar' in sys.argv else 'openjdk 25')",
    )
    clear_tool_caches()

    _, document = _json(capsys, "--config", str(cfg), "deps", "list")
    row = _row(document, "discvrseq")
    assert row["status"] == "installed"
    assert not row["can_install"] and not row["can_uninstall"]

    code, _ = _json(capsys, "--config", str(cfg), "deps", "uninstall", "discvrseq")
    assert code == 1 and custom.is_file()


def test_needs_offers_granite_install_for_vcf_granite_qc(host, fake_pixi, capsys):
    """`vcf granite-qc` reports granite as a missing installable tool on Unix."""
    if "granite" in platform_unavailable_tools():
        pytest.skip("granite isn't built for this platform")
    fake_pixi.env("granite", locked=("granite_suite",), installed=False)

    _, before = _json(capsys, "deps", "list")
    row = _row(before, "granite")
    assert row["status"] == "missing" and row["can_install"]
    assert row["environment"] == "granite"

    (item,) = _needs(capsys, "vcf", "granite-qc", "--vcf-input", "x.vcf.gz")
    assert item["name"] == "granite"
    assert item["installArguments"] == ["deps", "install", "granite"]


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows plink2 release")
def test_plink2_installs_and_uninstalls_from_its_windows_release(
    host, fake_pixi, tmp_path, monkeypatch, capsys
):
    """Bioconda has no win-64 plink2: deps install unpacks the official
    Windows build into the managed tools folder, and uninstall removes it."""
    import os
    import zipfile
    from pathlib import Path

    from wgsextract_cli.core.optional_tools import MANAGED_BINARIES

    # deps list runs what it finds: a Windows program that runs anywhere
    # stands in for plink2.exe.
    runnable = Path(os.environ["SystemRoot"], "System32", "whoami.exe").read_bytes()

    fake_pixi.env("plink2", locked=(), installed=False)
    jar_dir = tmp_path / "jartools"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'jar_directory = "{jar_dir.as_posix()}"\n', encoding="utf-8")
    release = MANAGED_BINARIES["plink2"]["win-64"]

    _, before = _json(capsys, "--config", str(cfg), "deps", "list")
    row = _row(before, "plink2")
    assert row["status"] == "missing" and row["can_install"]
    assert release.version in row["note"]
    (item,) = _needs(capsys, "--config", str(cfg), *_PLINK2_CHECK)
    assert item["installArguments"][-3:] == ["deps", "install", "plink2"]

    # Mock only the network download boundary: the release's zip holds more
    # programs than plink2, which alone is unpacked.
    def fake_download(url, dest, **kwargs):
        assert url == release.url and kwargs["expected_sha256"] == release.sha256
        with zipfile.ZipFile(dest, "w") as bundle:
            bundle.writestr("plink2.exe", runnable)
            bundle.writestr("ldsc.exe", b"MZ-ldsc")
        return True

    monkeypatch.setattr(
        "wgsextract_cli.core.ref_library.downloads.download_file", fake_download
    )
    code, installed = _json(capsys, "--config", str(cfg), "deps", "install", "plink2")
    assert code == 0
    assert Path(installed["details"]["path"]).parent == jar_dir / "plink2"
    assert sorted(p.name for p in (jar_dir / "plink2").iterdir()) == ["plink2.exe"]
    clear_tool_caches()

    row = _row(_json(capsys, "--config", str(cfg), "deps", "list")[1], "plink2")
    assert row["status"] == "installed" and row["can_uninstall"]
    assert _needs(capsys, "--config", str(cfg), *_PLINK2_CHECK) == []

    # Uninstall removes what it unpacked, never the user's files beside it.
    users = jar_dir / "plink2" / "scores.txt"
    users.write_text("mine", encoding="utf-8")
    code, _ = _json(capsys, "--config", str(cfg), "deps", "uninstall", "plink2")
    assert code == 0 and users.is_file()
    assert not (jar_dir / "plink2" / "plink2.exe").exists()
    clear_tool_caches()
    row = _row(_json(capsys, "--config", str(cfg), "deps", "list")[1], "plink2")
    assert row["status"] == "missing" and row["can_install"]
