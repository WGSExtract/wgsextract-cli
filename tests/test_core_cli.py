"""Regression tests for CLI bootstrap, config, and core runtime helpers."""

import csv
import logging
import subprocess
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock

import pytest
import tomllib

from wgsextract_cli import api
from wgsextract_cli.cli import bootstrap, parser
from wgsextract_cli.commands import (
    _deps_status,
    _vep_engines,
    examples,
    methylation,
    serve,
)
from wgsextract_cli.core import (
    config,
    mimalloc,
    native_aligners,
    native_deepvariant,
    process_registry,
    resource_policy,
)
from wgsextract_cli.core.alignment import profiles as output_profiles
from wgsextract_cli.core.microarray import quirks as microarray_quirks
from wgsextract_cli.core.settings_schema import SETTINGS
from wgsextract_cli.core.utils import WGSExtractError


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "get_config_path", lambda: config_path)
    saved = dict(config.settings)
    yield config_path
    config.settings.clear()
    config.settings.update(saved)


@pytest.mark.parametrize("value", [None, "auto", "AUTO", " auto "])
def test_parse_thread_count_auto(value):
    assert resource_policy.parse_thread_count(value) is None


@pytest.mark.parametrize("value", ["0", "-2", "many", "1.5"])
def test_parse_thread_count_rejects_invalid(value):
    with pytest.raises(ValueError):
        resource_policy.parse_thread_count(value)


def test_resource_defaults_accept_auto():
    threads, memory = resource_policy.get_resource_defaults(
        "auto", "auto", lambda: Namespace(threads=6)
    )
    assert threads == "6"
    assert memory.endswith("G")


def test_config_threads_auto_becomes_none_default(temp_config):
    temp_config.write_text('threads = "auto"\n')
    config.reload_settings()
    cli_parser, base_parser = parser.build_parser()

    args, _ = parser.parse_args(cli_parser, base_parser, ["info"])
    assert args.threads is None

    args, explicit = parser.parse_args(
        cli_parser, base_parser, ["--threads", "auto", "info"]
    )
    assert args.threads is None
    assert "threads" in explicit

    args, _ = parser.parse_args(cli_parser, base_parser, ["info", "-t", "3"])
    assert args.threads == 3


def test_invalid_threads_flag_is_a_usage_error(temp_config):
    config.reload_settings()
    cli_parser, base_parser = parser.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(cli_parser, base_parser, ["info", "--threads", "0"])
    assert exc.value.code == 2


@pytest.mark.parametrize("value", ['"max"', '"4.0"', "true", "0", "1.5"])
def test_invalid_config_threads_warns_and_allows_cli_override(
    temp_config, caplog, value
):
    temp_config.write_text(f"threads = {value}\n")
    config.set_config_path(temp_config)
    config.reload_settings()
    cli_parser, base_parser = parser.build_parser()
    assert "Ignoring invalid cpu_threads" in caplog.text
    assert str(temp_config) in caplog.text
    args, _ = parser.parse_args(cli_parser, base_parser, ["info"])
    assert args.threads is None
    args, explicit = parser.parse_args(
        cli_parser, base_parser, ["info", "--threads", "4"]
    )
    assert args.threads == 4 and "threads" in explicit


@pytest.mark.parametrize(
    "key",
    [key for key, setting in SETTINGS.items() if setting.kind == "bool"],
)
@pytest.mark.parametrize("value", ['"false"', "0", "[]", "{}"])
def test_boolean_config_settings_require_toml_booleans(temp_config, key, value):
    temp_config.write_text(f"{key} = {value}\n")
    with pytest.raises(WGSExtractError, match=rf"{key}.*TOML boolean"):
        config.load_config()


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("microarray_min_dp", '"15"', "TOML integer"),
        ("microarray_min_gq", "false", "TOML integer"),
        ("memory", "2", "TOML string"),
        ("ref", "false", "TOML string"),
        ("output_directory", "[]", "TOML string"),
        ("vcf_input_paths", '["sample.vcf", 1]', "array of TOML strings"),
        ("qc_min_diploid_depth", '"8"', "non-negative TOML number"),
        ("qc_min_haploid_depth", "-1", "non-negative TOML number"),
        ("qc_max_no_call_rate", "1.5", "TOML number from 0 to 1"),
        ("server_port", "70000", "TOML integer from 0 to 65535"),
        ("server_max_jobs", "0", "TOML integer of at least 1"),
        ("server_mcp_transport", '"tcp"', "one of stdio, http"),
        ("microarray_quirks", '"Vendor"', "one of corrected, vendor"),
        ("dev", "true", r"\[dev\] table"),
        ("dev", "{ download_cache_ttl_seconds = 1.5 }", "non-negative TOML integer"),
    ],
)
def test_other_config_types_are_validated_at_load(temp_config, key, value, expected):
    temp_config.write_text(f"{key} = {value}\n")
    with pytest.raises(WGSExtractError, match=rf"{key}.*{expected}"):
        config.load_config()


def test_valid_config_types_preserve_values_and_defaults(temp_config):
    temp_config.write_text(
        'atomic_outputs = false\nkeep_awake = true\nthreads = 4\nmemory = "512M"\n'
        'ref = "~/reference.fa"\nvcf_input_paths = ["a.vcf", "b.vcf"]\n'
        "microarray_min_dp = 15\nqc_min_diploid_depth = 4\n"
        'qc_max_no_call_rate = 0.25\nnative_aligners = "OFF"\n'
        "[dev]\ndownload_cache_ttl_seconds = 0\n"
    )
    loaded = config.load_config()
    assert loaded["atomic_outputs"] is False and loaded["keep_awake"] is True
    assert loaded["cpu_threads"] == 4 and loaded["memory_limit"] == "512M"
    assert loaded["reference_fasta"] == str(Path.home() / "reference.fa")
    assert loaded["vcf_input_paths"] == ["a.vcf", "b.vcf"]
    assert loaded["microarray_min_dp"] == 15
    assert loaded["dev"] == {"download_cache_ttl_seconds": 0}
    assert loaded["native_aligners"] == "OFF"
    assert loaded["qc_min_diploid_depth"] == 4
    assert loaded["qc_max_no_call_rate"] == 0.25
    assert "quiet_mode" not in loaded
    temp_config.write_text("")
    assert config.load_config() == {}


@pytest.mark.parametrize(
    ("key", "values"),
    [
        ("server_mcp_transport", serve.MCP_TRANSPORTS),
        ("methylation_context", methylation.CONTEXTS),
        ("vep_engine", _vep_engines.ENGINE_NAMES),
        ("native_aligners", native_aligners.SETTINGS),
        ("mimalloc", mimalloc.SETTINGS),
        ("deepvariant_native", native_deepvariant.ENGINE_CHOICES),
        ("microarray_quirks", microarray_quirks.MODES),
        ("output_profile", output_profiles.OUTPUT_PROFILES),
    ],
)
def test_config_choices_are_what_their_commands_accept(key, values):
    # contract: loading must accept exactly the values the command runs with.
    assert set(SETTINGS[key].info.choices) == set(values)


def test_param_config_key_must_name_a_setting():
    from wgsextract_cli.api._params import param

    assert param(None, config_key="server_port") is not None
    with pytest.raises(ValueError, match="server_prot"):
        param(None, config_key="server_prot")


def test_configuration_docs_are_up_to_date():
    script = Path(__file__).resolve().parents[1] / "scripts" / "gen_config_docs.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--check"], capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("key", "moved"),
    [
        ("dev_download_cache", "download_cache"),
        ("real_genome_test_path", "real_genome_test_path"),
    ],
)
def test_old_top_level_dev_setting_names_its_dev_table(temp_config, caplog, key, moved):
    # Issue #823: developer and test settings moved into a [dev] table; an old
    # config must say where, not silently drop the setting.
    temp_config.write_text(f"{key} = true\n")
    with pytest.raises(WGSExtractError, match=rf"{key}.*\[dev\].*{moved} ="):
        config.load_config()
    assert "Unknown setting" not in caplog.text
    with pytest.raises(WGSExtractError, match=r"\[dev\]"):
        config.save_config({"cpu_threads": 4})
    assert temp_config.read_text() == f"{key} = true\n"


def test_typed_settings_read_the_loaded_config(temp_config, caplog):
    # Issue #823: typed attribute access to checked config.toml values.
    temp_config.write_text(
        'threads = 6\nqc_min_diploid_depth = 8\nvcf_input_paths = ["~/a.vcf"]\n'
        'reference_library = "~/reflib"\n'
        '[dev]\ndownload_cache = true\npet_r1_fastq = "~/r1.fq"\nreal_genome = 1\n'
    )
    config.reload_settings()
    typed = config.typed_settings()
    assert typed.cpu_threads == 6 and typed.qc_min_diploid_depth == 8.0
    assert typed.vcf_input_paths == (str(Path.home() / "a.vcf"),)
    assert typed.reference_library == str(Path.home() / "reflib")
    assert typed.dev.download_cache is True
    assert typed.dev.pet_r1_fastq == str(Path.home() / "r1.fq")
    assert "Unknown setting 'real_genome'" in caplog.text
    assert "Did you mean 'real_genome_test_path'?" in caplog.text


def test_typed_settings_reject_invalid_host_values(temp_config, monkeypatch):
    monkeypatch.setitem(config.settings, "qc_max_no_call_rate", "high")
    with pytest.raises(WGSExtractError, match="qc_max_no_call_rate.*0 to 1"):
        config.typed_settings()


@pytest.mark.parametrize("value", ['"max"', "0", '"-2"'])
def test_typed_settings_read_invalid_config_threads_as_auto(temp_config, value):
    # PR #934 review: typed settings returned cpu_threads = 0 or "max" as-is
    # instead of the documented auto fallback.
    temp_config.write_text(f"cpu_threads = {value}\n")
    config.reload_settings()
    assert config.typed_settings().cpu_threads == "auto"


def test_save_config_writes_and_clears_the_dev_table(temp_config):
    # PR #934 review: save_config silently dropped [dev] updates, which were
    # writable as top-level dev_* settings before the move.
    temp_config.write_text('[dev]\npet_r2_fastq = "/r2.fq"\n')
    config.save_config({"dev": {"download_cache": True, "pet_r1_fastq": "~/r1.fq"}})
    dev = tomllib.loads(temp_config.read_text())["dev"]
    assert dev == {
        "pet_r2_fastq": "/r2.fq",
        "download_cache": True,
        "pet_r1_fastq": str(Path.home() / "r1.fq"),
    }
    assert config.typed_settings().dev.download_cache is True

    with pytest.raises(WGSExtractError, match=r"dev\.download_cache.*boolean"):
        config.save_config({"dev": {"download_cache": "yes"}})
    assert tomllib.loads(temp_config.read_text())["dev"]["download_cache"] is True

    config.save_config({"dev": None})
    assert "dev" not in tomllib.loads(temp_config.read_text())
    assert config.typed_settings().dev.download_cache is False


def test_save_config_names_the_dev_table_for_old_dev_keys(temp_config):
    # PR #934 review: an old top-level dev key passed to save_config was
    # silently discarded; it fails like the same key in config.toml.
    with pytest.raises(WGSExtractError, match=r"\[dev\].*download_cache ="):
        config.save_config({"dev_download_cache": True})
    assert not temp_config.exists()


def test_config_threads_retains_supported_numeric_string(temp_config):
    temp_config.write_text('cpu_threads = "4"\n')
    config.reload_settings()
    assert config.settings["cpu_threads"] == "4"
    cli_parser, base_parser = parser.build_parser()
    args, _ = parser.parse_args(cli_parser, base_parser, ["info"])
    assert args.threads == 4


def test_save_config_rejects_invalid_types_before_publication(temp_config):
    temp_config.write_text("atomic_outputs = false\n")
    config.reload_settings()
    with pytest.raises(WGSExtractError, match="atomic_outputs.*TOML boolean"):
        config.save_config({"atomic_outputs": "false"})
    assert temp_config.read_text() == "atomic_outputs = false\n"
    assert config.settings["atomic_outputs"] is False


def test_save_config_rejects_invalid_threads_before_publication(temp_config):
    temp_config.write_text("threads = 4\n")
    with pytest.raises(WGSExtractError, match="cpu_threads.*positive TOML integer"):
        config.save_config({"cpu_threads": "max"})
    assert temp_config.read_text() == "threads = 4\n"


@pytest.mark.parametrize("command", [["--version"], ["help"], ["config"], ["info"]])
def test_config_load_errors_are_one_line_without_traceback(tmp_path, capsys, command):
    from wgsextract_cli.main import main

    path = tmp_path / "bad.toml"
    path.write_text('keep_awake = "false"\n')
    with pytest.raises(SystemExit) as exc:
        main(["--config", str(path), *command])
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert len(output.err.splitlines()) == 1
    assert "keep_awake" in output.err and str(path) in output.err
    assert "TOML boolean" in output.err and "Traceback" not in output.err
    assert "config set/show are not supported" in output.err


def test_startup_selects_alternate_config_before_validating_default(
    tmp_path, monkeypatch
):
    from tests.smoke_utils import isolate_home

    home = tmp_path / "home"
    isolate_home(monkeypatch, home)
    default = config.get_config_path()
    assert default.is_relative_to(home)
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_text('atomic_outputs = "false"\n')
    alternate = tmp_path / "alternate.toml"
    alternate.write_text("atomic_outputs = false\nkeep_awake = false\n")
    command = [sys.executable, "-m", "wgsextract_cli.main"]

    selected = subprocess.run(
        [*command, "--config", str(alternate), "help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert selected.returncode == 0, selected.stderr
    assert "COMMAND TREE:" in selected.stdout
    rejected = subprocess.run(
        [*command, "help"], capture_output=True, text=True, check=False
    )
    assert rejected.returncode != 0
    assert "atomic_outputs" in rejected.stderr and "TOML boolean" in rejected.stderr
    assert "Traceback" not in rejected.stderr
    assert len(rejected.stderr.splitlines()) == 1


def test_save_config_clears_aliases_of_cleared_key(temp_config):
    temp_config.write_text('reflib = "/old/reference"\nthreads = 4\n')
    config.reload_settings()
    assert config.settings["reference_library"] == "/old/reference"

    config.save_config({"reference_library": None})

    assert "reference_library" not in config.settings
    assert "reflib" not in temp_config.read_text()
    assert config.settings["cpu_threads"] == 4


def test_save_config_refuses_to_overwrite_invalid_toml(temp_config):
    temp_config.write_text("this is = = not toml\n")
    with pytest.raises(WGSExtractError):
        config.save_config({"reference_library": "/x"})
    assert temp_config.read_text() == "this is = = not toml\n"


def test_config_with_utf8_bom_is_read_and_updated(temp_config):
    # Issue #554: Windows PowerShell 5 and older Notepad save UTF-8 with a BOM;
    # such a config.toml was ignored and `ref bootstrap` refused to update it.
    temp_config.write_bytes(b"\xef\xbb\xbfreference_library = 'D:\\wgse\\reflib'\r\n")
    config.reload_settings()
    assert config.settings["reference_library"] == "D:\\wgse\\reflib"

    config.save_config({"cpu_threads": 4})

    saved = tomllib.loads(temp_config.read_text(encoding="utf-8"))
    assert saved == {"reference_library": "D:\\wgse\\reflib", "cpu_threads": 4}


def test_full_help_unescapes_percent_signs(capsys):
    cli_parser, _ = parser.build_parser()
    parser.print_full_help(cli_parser)
    out = capsys.readouterr().out
    assert "%%" not in out
    assert "smaller than BAM" in out


def test_emoji_formatter_does_not_mutate_record():
    record = logging.LogRecord("x", logging.WARNING, __file__, 1, "msg", None, None)
    formatted = bootstrap.EmojiFormatter("%(levelname)s %(message)s").format(record)
    assert formatted.endswith("msg")
    assert "WARNING" not in formatted
    assert record.levelname == "WARNING"


def test_registry_cleanup_reenters_while_lock_held():
    registry = process_registry.ProcessRegistry()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        **process_registry.process_group_kwargs(),
    )
    registry.register_process("sleep", proc)
    done = threading.Event()

    def cleanup_under_lock():
        # Simulates a signal handler interrupting register/unregister.
        with registry._lock:
            registry.cleanup()
        done.set()

    worker = threading.Thread(target=cleanup_under_lock, daemon=True)
    worker.start()
    assert done.wait(5), "cleanup deadlocked while the registry lock was held"
    assert proc.wait(5) is not None
    assert registry.processes == {}


def test_command_echo_and_streamed_stderr_redact_urls_but_execute_original(
    tmp_path, monkeypatch, caplog, capsys
):
    from wgsextract_cli.core import utils

    url = "https://private-user:private-password@example.test/a?token=private-token"
    received = tmp_path / "request-url"
    recorded = []
    monkeypatch.setattr(utils, "record_command", recorded.append)
    script = (
        "import sys,time;from pathlib import Path;"
        "Path(sys.argv[2]).write_text(sys.argv[1]);"
        "sys.stderr.write(sys.argv[1][:35]);sys.stderr.flush();time.sleep(.03);"
        "sys.stderr.write(sys.argv[1][35:]+'\\n');"
        "sys.stderr.write('signature rejected '+sys.argv[1].rsplit('=',1)[-1]+'\\n');"
        "sys.stderr.flush();sys.exit(1)"
    )
    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(subprocess.CalledProcessError) as error,
    ):
        utils.run_command(
            [sys.executable, "-c", script, url, str(received)],
            redact_output_urls=True,
        )
    assert received.read_text() == url
    diagnostics = (
        caplog.text + capsys.readouterr().err + str(error.value) + str(recorded)
    )
    assert "example.test/a" in diagnostics
    for secret in ("private-user", "private-password", "private-token"):
        assert secret not in diagnostics


def test_windows_cleanup_terminates_tree_before_launcher_exits(monkeypatch):
    registry = process_registry.ProcessRegistry()
    proc = Mock(pid=1234)
    proc.poll.side_effect = [None, 0]
    registry.register_process("gatk", proc)
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(process_registry.sys, "platform", "win32")
    monkeypatch.setattr(process_registry.subprocess, "run", run)
    monkeypatch.setattr(process_registry.time, "sleep", lambda _: None)

    registry.cleanup()

    run.assert_called_once_with(
        ["taskkill", "/F", "/T", "/PID", "1234"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    proc.send_signal.assert_not_called()
    proc.kill.assert_not_called()
    assert registry.processes == {}


def test_windows_cleanup_falls_back_when_taskkill_times_out(monkeypatch, caplog):
    registry = process_registry.ProcessRegistry()
    proc = Mock(pid=1234)
    proc.poll.return_value = None
    registry.register_process("gatk", proc)
    monkeypatch.setattr(process_registry.sys, "platform", "win32")
    monkeypatch.setattr(
        process_registry.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired("taskkill", 10)),
    )
    monkeypatch.setattr(process_registry.time, "sleep", lambda _: None)

    registry.cleanup()

    proc.kill.assert_called_once()
    assert "Process 1234 could not be terminated" in caplog.text
    assert registry.processes == {}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-tree regression")
def test_windows_cleanup_kills_break_ignoring_descendants(tmp_path):
    child_pid = tmp_path / "child.pid"
    grandchild_pid = tmp_path / "grandchild.pid"
    grandchild = (
        "import os, signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
        f"Path({str(grandchild_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    child = (
        "import os, signal, subprocess, sys, time; from pathlib import Path; "
        "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
        f"Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)",
        ],
        **process_registry.process_group_kwargs(),
    )
    registry = process_registry.ProcessRegistry()
    registry.register_process("launcher", parent)
    descendants = []
    try:
        deadline = time.monotonic() + 15
        while not all(
            path.exists() and path.stat().st_size > 0
            for path in (child_pid, grandchild_pid)
        ):
            assert parent.poll() is None
            assert time.monotonic() < deadline, "descendants did not initialize"
            time.sleep(0.05)
        descendants = [int(path.read_text()) for path in (child_pid, grandchild_pid)]
        registry.cleanup()
        parent.wait(timeout=5)
        for pid in descendants:
            listing = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            assert not any(
                len(row) > 1 and row[1] == str(pid)
                for row in csv.reader(listing.stdout.splitlines())
            ), f"descendant {pid} survived cleanup"
        descendants.clear()
    finally:
        remaining = descendants + ([parent.pid] if parent.poll() is None else [])
        for pid in remaining:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                timeout=10,
            )
        parent.wait(timeout=5)


def test_deps_check_exits_nonzero_when_mandatory_missing(monkeypatch, capsys):
    monkeypatch.setattr(
        _deps_status,
        "check_all_dependencies",
        lambda: {
            "mandatory": [
                {
                    "name": "samtools",
                    "path": None,
                    "runtime": "missing",
                    "version": None,
                }
            ],
            "optional": [],
        },
    )
    with pytest.raises(api.CommandFailed, match="samtools") as failed:
        api.deps.check(api.DepsCheckParams())
    assert failed.value.exit_code == 3


def _download(root: Path, force: bool) -> None:
    api.examples.download(
        api.ExampleGenomeDownloadParams(
            example_ids=("phase3-chrmt-vcf",),
            method="https",
            target_root=root,
            force=force,
        )
    )


def _fake_http_download(calls):
    def download(_source, destination, **_kwargs):
        output = Path(destination)
        partial = examples._partial_path(output)
        calls.append(partial.read_bytes() if partial.exists() else None)
        with partial.open("ab") as f:
            f.write(b"rest")
        partial.replace(output)
        return True

    return download


def test_example_download_resumes_partial_and_skips_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(examples.shutil, "which", lambda _name: "/usr/bin/curl")
    calls: list[bytes | None] = []
    monkeypatch.setattr(examples, "download_file", _fake_http_download(calls))
    example = examples._select_examples(["phase3-chrmt-vcf"], False)[0]
    example_dir = tmp_path / examples.COLLECTION_DIR / example.example_id
    example_dir.mkdir(parents=True)
    planned = examples._planned_downloads(example, example_dir, "https")
    first, second = (destination for _s, destination, _r, _m in planned)
    first.write_bytes(b"complete")
    examples._partial_path(second).write_bytes(b"half-")

    _download(tmp_path, force=False)

    assert first.read_bytes() == b"complete"
    assert second.read_bytes() == b"half-rest"
    assert not examples._partial_path(second).exists()
    assert calls[0] == b"half-"


def test_example_download_force_discards_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(examples.shutil, "which", lambda _name: "/usr/bin/curl")
    calls: list[bytes | None] = []
    monkeypatch.setattr(examples, "download_file", _fake_http_download(calls))
    example = examples._select_examples(["phase3-chrmt-vcf"], False)[0]
    example_dir = tmp_path / examples.COLLECTION_DIR / example.example_id
    example_dir.mkdir(parents=True)
    planned = examples._planned_downloads(example, example_dir, "https")
    destinations = [destination for _s, destination, _r, _m in planned]
    destinations[0].write_bytes(b"old")
    examples._partial_path(destinations[1]).write_bytes(b"stale-")

    _download(tmp_path, force=True)

    assert calls == [None] * len(destinations)
    assert [d.read_bytes() for d in destinations] == [b"rest"] * len(destinations)


@pytest.mark.parametrize("configured", [False, True])
def test_example_download_says_how_to_select_it_with_genome(
    tmp_path, monkeypatch, caplog, configured
):
    monkeypatch.setattr(examples.shutil, "which", lambda _name: "/usr/bin/curl")
    monkeypatch.setattr(examples, "download_file", _fake_http_download([]))
    root = tmp_path / "it's lib"
    root.mkdir()
    config.settings["genome_library"] = str(root) if configured else None

    with caplog.at_level(logging.INFO):
        _download(root, force=False)

    use = "Use --genome test-1000genomes/phase3-chrmt-vcf"
    message = next(r.getMessage() for r in caplog.records if use in r.getMessage())
    hint = message.split(use, 1)[1]
    if configured:
        assert hint == ""
    else:
        assignment = hint.removeprefix(" after setting ").removesuffix(
            " in config.toml"
        )
        assert tomllib.loads(assignment) == {"mygenomes_library": str(root.resolve())}


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["info", "--input", "a.cram", "coverage-sample"], "a.cram"),
        (["--input", "a.cram", "info", "coverage-sample"], "a.cram"),
        (["info", "coverage-sample", "--input", "a.cram"], "a.cram"),
        (["--input", "a.cram", "info", "coverage-sample", "-i", "a.cram"], "a.cram"),
    ],
)
def test_shared_options_apply_at_every_subcommand_level(argv, expected):
    cli_parser, base_parser = parser.build_parser()
    args, explicit = parser.parse_args(cli_parser, base_parser, argv)
    assert args.input == expected
    assert "input" in explicit


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["info", "--input", "x.bam"], None),
        (["--config", "a.toml", "info"], "a.toml"),
        (["info", "--config=b.toml"], "b.toml"),
        (["--config", "a.toml", "info", "--config", "c.toml"], "c.toml"),
        (["info", "--", "--config", "d.toml"], None),
    ],
)
def test_config_path_from_argv(argv, expected):
    assert parser.config_path_from_argv(argv) == expected


def test_config_flag_reads_and_saves_only_that_file(tmp_path):
    own = tmp_path / "own.toml"
    own.write_text('output_directory = "/from/own/config"\n')
    user_config = config.get_config_path()
    user_config.unlink()
    try:
        config.set_config_path(own)
        config.reload_settings()
        assert config.settings.get("output_directory") == "/from/own/config"
        config.save_config({"reference_library": str(tmp_path / "ref")})
        assert config.config_args() == ["--config", str(own.resolve())]
    finally:
        config.set_config_path(None)
        config.reload_settings()
    assert "reference_library" in own.read_text()
    assert not user_config.exists()


def test_config_flag_reaches_the_cli(tmp_path):
    own = tmp_path / "own.toml"
    outdir = tmp_path / "from-own-config"
    own.write_text(f"output_directory = '{outdir}'\n")
    result = subprocess.run(
        [sys.executable, "-m", "wgsextract_cli.main", "--config", str(own), "config"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert f"Config File:      {own.resolve()}" in result.stdout
    assert "from-own-config" in result.stdout


def test_version_flag(capsys):
    from wgsextract_cli import __version__
    from wgsextract_cli.main import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"wgsextract {__version__}"
