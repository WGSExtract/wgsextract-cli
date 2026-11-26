"""#1591: network mode limits transfers independently of their usual opt-ins."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, write_fasta
from tests.http_server import serving
from wgsextract_cli import api
from wgsextract_cli.core import bwa_mem2
from wgsextract_cli.main import main
from wgsextract_cli.server.jobs import JobManager
from wgsextract_cli.server.operations import find, params_from_json


def test_1591_help_exposes_network_options_to_cli_callers(capsys) -> None:
    main(["help", "--all", "--json"])
    index = json.loads(capsys.readouterr().out)
    options = {option["dest"]: option for option in index["global_options"]}
    assert options["network"]["choices"] == ["full", "low", "off"]
    assert "--allow-large-download" in options["allow_large_download"]["flags"]

    main(["help", "ref", "download", "--json"])
    props = json.loads(capsys.readouterr().out)["commands"][0]["input_schema"][
        "properties"
    ]
    assert props["network"]["x-cli-flag"] == "--network"
    assert props["allow_large_download"]["x-cli-flag"] == "--allow-large-download"


@pytest.mark.parametrize(
    ("configured", "flags", "refusal"),
    [
        ("off", ["--allow-large-download"], "--network off"),
        ("low", [], "--allow-large-download"),
        ("full", ["--network", "off"], "--network off"),
        ("off", ["--network", "full"], None),
        ("low", ["--allow-large-download"], None),
    ],
)
def test_1591_cli_limits_reference_transfers(
    tmp_path: Path, capsys, configured: str, flags: list[str], refusal: str | None
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f'network_mode = "{configured}"\nstate_directory = '
        f'"{tmp_path.as_posix()}/state"\n',
        encoding="utf-8",
    )
    dest = tmp_path / "ref.fa"
    with serving(data=b">chr1\nACGT\n") as http:
        args = [
            "--config", str(config), "--json", "ref", "download",
            "--url", http.url_for("ref.fa"), "--out", str(dest), *flags,
        ]  # fmt: skip
        if refusal:
            with pytest.raises(SystemExit) as error:
                main(args)
            assert error.value.code == 2
            assert refusal in json.loads(capsys.readouterr().out)["error"]["message"]
            assert not dest.exists()
            assert not http.requests
        else:
            main(args)
            assert json.loads(capsys.readouterr().out)["status"] == "ok"
            assert dest.read_bytes() == b">chr1\nACGT\n"
            assert "ref.fa" in http.get_paths


def test_1591_server_params_allow_small_probes_in_low_but_not_off(
    tmp_path: Path, monkeypatch
) -> None:
    from wgsextract_cli.commands.ref import core_commands

    op = find("ref/library-list")
    with serving(data=b">chr1\nACGT\n") as http:
        monkeypatch.setattr(
            core_commands,
            "get_available_genomes",
            lambda: [
                {"code": "test", "final": "test.fa", "source": "test",
                 "label": "test", "url": http.url_for("test.fa")}
            ],
        )  # fmt: skip
        off = params_from_json(
            op, {"ref": str(tmp_path), "remote_sizes": True, "network": "off",
                 "allow_large_download": True},
        )  # fmt: skip
        assert isinstance(off, api.RefLibraryListParams)
        blocked = api.ref.library_list(off, check=False)
        assert blocked.exit_code == 2
        assert blocked.error and "--network off" in blocked.error.message
        assert not http.requests

        low = params_from_json(
            op, {"ref": str(tmp_path), "remote_sizes": True, "network": "low"}
        )
        assert isinstance(low, api.RefLibraryListParams)
        allowed = api.ref.library_list(low)
        assert allowed.details["genomes"][0]["remote_bytes"] == 11
        assert ("HEAD", None) in http.requests
        assert not http.get_paths


@pytest.mark.parametrize("mode", ["low", "off"])
def test_1591_realign_checks_mode_before_remote_input_preflight(
    tmp_path: Path, capsys, mode: str
) -> None:
    with serving(data=b"not a BAM") as http:
        with pytest.raises(SystemExit) as error:
            main(
                [
                    "--network",
                    mode,
                    "--json",
                    "realign",
                    "--input",
                    http.url_for("reads.bam"),
                    "--outdir",
                    str(tmp_path),
                ]  # fmt: skip
            )
        assert error.value.code == 2
        assert (
            f"--network {mode}"
            in json.loads(capsys.readouterr().out)["error"]["message"]
        )
        assert not http.requests


def test_1591_server_worker_cannot_relax_operator_mode(tmp_path: Path) -> None:
    with serving(data=b"reference") as http:
        catalog = [
            {
                "code": "test",
                "final": "test.fa",
                "source": "test",
                "label": "test",
                "url": http.url_for("test.fa"),
            }
        ]
        manager = JobManager(
            tmp_path / "jobs",
            config_path=None,
            network="off",
            worker_command=[
                sys.executable,
                "-c",
                "from wgsextract_cli.commands.ref import core_commands\n"
                "from wgsextract_cli.server.worker import main\n"
                f"core_commands.get_available_genomes = lambda: {catalog!r}\n"
                "raise SystemExit(main())",
            ],
        )
        try:
            job = manager.submit(
                find("ref/library-list"),
                {"ref": str(tmp_path), "remote_sizes": True, "network": "full"},
            )
            done = manager.wait(job.id, 15)
            assert done is not None and done.exit_code == 2
            assert done.error and "--network off" in done.error["message"]
            assert not http.requests
        finally:
            manager.shutdown()


@needs_tools("samtools", "bwa-mem2")
def test_1591_prebuilt_index_reports_low_mode_as_usage(tmp_path, monkeypatch):
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "ACGT" * 100})
    body = b"index"
    with serving(data=body) as http:
        fixture = bwa_mem2.PrebuiltIndex(
            "fixture index",
            http.url_for("index"),
            {".0123": (len(body), hashlib.sha256(body).hexdigest())},
        )
        monkeypatch.setitem(
            bwa_mem2.PREBUILT_INDEXES, bwa_mem2.contig_fingerprint(str(ref)), fixture
        )
        result = api.ref.index(
            api.RefIndexParams(
                ref=ref, aligner="bwa-mem2", download=True, network="low"
            ),
            check=False,
        )
        assert result.exit_code == 2
        assert result.error and "--allow-large-download" in result.error.message
        assert not http.requests
