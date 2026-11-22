"""`wgsextract deps ready -- COMMAND` (#1183, offline readiness): what a
command line needs on this computer, checked the way its run checks it, with
the evidence (versions, digests, image identity, terms) and the explicit
step that provides anything missing. It must never install, pull or
download, nor make any network request."""

import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, snp_sample
from wgsextract_cli.api import deps
from wgsextract_cli.main import main


@pytest.fixture
def offline(monkeypatch):
    """A computer with no network: any connection attempt fails the test."""
    attempts: list[object] = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise OSError("the network is off")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield
    assert attempts == [], "deps ready tried to reach the network"


def ready(capsys, *command: str) -> tuple[int, dict]:
    """`wgsextract --json deps ready -- COMMAND`: its exit code and details."""
    capsys.readouterr()
    try:
        main(["--json", "deps", "ready", "--", *command])
        code = 0
    except SystemExit as exit_:
        code = int(exit_.code or 0)
    return code, json.loads(capsys.readouterr().out)["details"]


def item(details: dict, kind: str, name: str | None = None) -> dict:
    return next(
        i
        for i in details["items"]
        if i["kind"] == kind and (name is None or i["name"] == name)
    )


@pytest.fixture
def sample(tmp_path):
    return snp_sample(tmp_path)


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_a_samtools_bcftools_call_is_ready_offline(offline, sample, capsys):
    code, details = ready(
        capsys, "vcf", "snp", "--input", sample.bam, "--ref", sample.ref
    )

    assert (code, details["status"], details["blocking"]) == (0, "ready", [])
    tools = {i["name"]: i for i in details["items"] if i["kind"] == "tool"}
    assert set(tools) == {"bcftools", "samtools", "tabix"}
    assert all(t["status"] == "runnable" and t["version"] for t in tools.values())
    assert all("terms" in t["evidence"] for t in tools.values())
    reference = item(details, "reference")
    assert reference["status"] == "runnable"
    assert reference["evidence"]["chosen"] is True
    assert item(details, "index", "ref.fa.fai")["status"] == "runnable"
    assert item(details, "index", "s.bam.bai")["status"] == "runnable"


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_stale_and_missing_indexes_block_and_name_their_offline_fix(
    offline, sample, capsys
):
    bai, fai = Path(f"{sample.bam}.bai"), Path(f"{sample.ref}.fai")
    hour_ago = time.time() - 3600
    os.utime(bai, (hour_ago, hour_ago))
    fai.unlink()

    code, details = ready(
        capsys, "vcf", "snp", "--input", sample.bam, "--ref", sample.ref
    )

    assert (code, details["status"]) == (3, "not_ready")
    assert set(details["blocking"]) == {"s.bam.bai", "ref.fa.fai"}
    stale, missing = (
        item(details, "index", "s.bam.bai"),
        item(details, "index", "ref.fa.fai"),
    )
    assert (stale["status"], missing["status"]) == ("stale", "missing")
    # Without a .fai the reference's fit to the input can't be read.
    assert item(details, "reference")["status"] == "unverified"

    for fix in (stale["fix_arguments"], missing["fix_arguments"]):
        main(fix)
    code, details = ready(
        capsys, "vcf", "snp", "--input", sample.bam, "--ref", sample.ref
    )
    assert (code, details["status"]) == (0, "ready")


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_api_and_cli_report_the_same_readiness(offline, sample, capsys):
    command = ("vcf", "snp", "--input", sample.bam, "--ref", sample.ref)

    _, cli = ready(capsys, *command)
    api = deps.ready(deps.DepsReadyParams(command_line=command)).details

    assert api == cli


def test_1183_the_server_does_not_offer_deps_ready():
    """The command line names host paths a server's folder checks can't vet."""
    from wgsextract_cli.server.operations import operations

    assert "deps/ready" not in operations()
    assert "deps/check" in operations()


@needs_tools("samtools")
def test_1183_a_cram_without_its_genome_names_the_explicit_download(
    offline, tmp_path, capsys
):
    from tests.test_ref_needs import HS37D5_LIKE, _cram

    library = tmp_path / "reference"
    (library / "genomes").mkdir(parents=True)
    config = tmp_path / "config.toml"
    config.write_text(f"reference_library = {json.dumps(str(library))}\n")
    cram = _cram(tmp_path, HS37D5_LIKE)

    code, details = ready(capsys, "--config", str(config), "info", "--input", cram)

    reference = item(details, "reference")
    assert (code, reference["status"], reference["blocking"]) == (3, "missing", True)
    assert reference["fix_arguments"] == ["ref", "library", "--install", "hs37d5"]
    assert list((library / "genomes").iterdir()) == []


def test_1183_a_download_command_is_not_ready_offline(offline, capsys):
    code, details = ready(capsys, "ref", "cytobands", "--download")

    assert code == 3
    assert item(details, "network")["status"] == "unsupported"


def test_1183_remote_inputs_and_credentials_are_redacted(offline, capsys):
    url = "https://reader:hunter2@example.org/s.bam?X-Amz-Signature=abc123"

    code, details = ready(capsys, "bam", "identify", "--input", url)

    text = json.dumps(details)
    assert "hunter2" not in text and "abc123" not in text
    remote = item(details, "input")
    assert (code, remote["status"]) == (3, "unsupported")


def test_1183_absent_java_blocks_mt_haplogroups(sample, offline, fake_pixi, capsys):
    """Only the host's own programs, none of them Java."""
    code, details = ready(capsys, "lineage", "mt-haplogroup", "--input", sample.bam)

    java = item(details, "tool", "java")
    assert (code, java["status"], java["blocking"]) == (3, "missing", True)
    assert "java" in details["blocking"]


def test_1183_unparseable_command_lines_are_usage_errors(capsys):
    capsys.readouterr()
    with pytest.raises(SystemExit) as exit_:
        main(["deps", "ready", "--", "vcf", "snp", "--no-such-option"])
    assert exit_.value.code == 2


def test_1183_clair3_names_hosts_it_cant_run_on(offline, monkeypatch, capsys, sample):
    """An Intel Mac: Bioconda has no Clair3 2.x for it."""
    import platform

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")

    code, details = ready(capsys, "vcf", "clair3", "--input", sample.bam)

    clair3_host = item(details, "platform", "Clair3 host")
    assert (code, clair3_host["status"]) == (3, "unsupported")
    assert "Apple Silicon" in clair3_host["detail"]
