"""Shareable result cards written by --card (<name>.card.html)."""

import argparse
import json
import re
import shutil
import threading
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tests.smoke_utils import ensure_fake_data, run_cli, state_directory_setting
from tests.test_server import Mcp, _serve
from wgsextract_cli import __version__
from wgsextract_cli.cli.dispatch import dispatch_command
from wgsextract_cli.core import read_scope, result_card, results
from wgsextract_cli.core.utils import WGSExtractError

CLI_ROOT = Path(__file__).resolve().parents[1]
FAKE_DIR = CLI_ROOT / "out" / "fake_30x"
VALIDATOR = Draft202012Validator(results.load_schema())


def dispatch(func, outdir: Path, **extra) -> int:
    args = argparse.Namespace(
        func=func, command="test", _command="test cmd", outdir=str(outdir), **extra
    )
    try:
        dispatch_command(args, set())
    except SystemExit as e:
        return int(e.code or 0)
    return 0


def assert_self_contained(html: str) -> None:
    """Opens offline and in a sandboxed frame: no scripts, nothing fetched."""
    assert "<script" not in html.lower()
    assert not re.search(r"\b(src|href)\s*=", html, re.I)
    assert "@import" not in html and "url(" not in html


def test_card_shows_result_and_provenance_beside_the_manifest(tmp_path):
    outdir, data = tmp_path / "out", tmp_path / "private" / "folder"
    outdir.mkdir()
    data.mkdir(parents=True)
    source = data / "sample.bam"
    source.write_bytes(b"reads")
    output = outdir / "sample_snps.vcf.gz"

    def command(args):
        result = results.current_result()
        result.add_input(source)
        output.write_bytes(b"data")
        result.add_output(output, "snps")
        result.add_metrics(variants=1234)
        results.warn("low_coverage", f"Low coverage in {source}.")
        results.human_output(f"Wrote {output}\nVariants: 1234")

    assert dispatch(command, outdir, card=True) == 0
    card = outdir / "sample_snps.vcf.card.html"
    html = card.read_text(encoding="utf-8")
    assert_self_contained(html)
    assert "test cmd" in html and "Completed" in html
    assert f"WGS Extract</th><td>{__version__}" in html
    assert "sample.bam" in html and "Variants: 1234" in html
    assert "low_coverage" in html and "1,234" in html
    # Shareable: files by name only; the manifest beside it has the paths.
    assert str(data) not in html and str(outdir) not in html
    assert "sample_snps.vcf.manifest.json" in html

    manifest = json.loads((outdir / "sample_snps.vcf.manifest.json").read_text())
    assert not list(VALIDATOR.iter_errors(manifest))
    roles = {Path(o["path"]).name: o["role"] for o in manifest["outputs"]}
    assert roles == {"sample_snps.vcf.gz": "snps", card.name: "result_card"}
    (record,) = [o for o in manifest["outputs"] if o["role"] == "result_card"]
    assert record["exists"] and record["size_bytes"] == card.stat().st_size
    # Source fingerprint is shown, abbreviated.
    fingerprint = manifest["inputs"][0]["fingerprint"].removeprefix("wgse-fp1:")
    assert fingerprint[:12] in html


def test_failed_attempt_card_shows_the_error_and_keeps_the_manifest_name(tmp_path):
    """PR #1143 review: the card, registered as an output, became the name the
    failed attempt's manifest took (s.vcf.failed.card.html.manifest.json), so
    the card named a manifest that didn't exist."""

    def command(args):
        results.current_result().expect_output(tmp_path / "s.vcf.gz")
        raise WGSExtractError("Bad <b>input</b> & more.")

    assert dispatch(command, tmp_path, card=True) == 1
    manifest = tmp_path / "s.vcf.failed.manifest.json"
    card = tmp_path / "s.vcf.failed.card.html"
    assert sorted(p.name for p in tmp_path.iterdir()) == [card.name, manifest.name]
    html = card.read_text(encoding="utf-8")
    assert "Failed" in html and manifest.name in html
    assert "Bad &lt;b&gt;input&lt;/b&gt; &amp; more." in html
    assert "<b>input</b>" not in html
    document = json.loads(manifest.read_text())
    assert [o["role"] for o in document["outputs"]] == ["result_card"]


def test_directory_output_keeps_its_manifest_name(tmp_path):
    """PR #1143 review: with only a directory output, the card became the
    manifest's namesake."""
    folder = tmp_path / "vep"

    def command(args):
        folder.mkdir()
        (folder / "a.txt").write_text("x")
        results.current_result().add_output(folder, "cache")

    assert dispatch(command, tmp_path, card=True) == 0
    assert (folder / "vep.card.html").is_file()
    document = json.loads((folder / "vep.manifest.json").read_text())
    assert {o["role"] for o in document["outputs"]} == {"cache", "result_card"}
    assert not list(folder.glob("*.card.html.manifest.json"))


def test_report_without_a_sample_writes_nothing(tmp_path):
    """PR #1143 review: the app passes --card to every run; a report about no
    sample (`ref status`) must not leave a card or manifest in the output
    folder."""

    def command(args):
        results.human_output("Reference library: 3 genomes")

    args = argparse.Namespace(
        func=command,
        command="ref",
        _command="ref status",
        outdir=str(tmp_path),
        card=True,
    )
    dispatch_command(args, set())
    assert list(tmp_path.iterdir()) == []


def test_card_hides_other_folders_and_url_secrets(tmp_path):
    """PR #1143 review: folders other than the run's own files (the reference
    library, a temp folder, a path in a metric) and URL query strings reached
    the shared card."""
    library = tmp_path / "private" / "reflib"
    library.mkdir(parents=True)
    other = tmp_path / "private" / "scratch" / "x.tmp"
    other.parent.mkdir()
    other.write_text("x")
    output = tmp_path / "out.txt"

    def command(args):
        result = results.current_result()
        output.write_text("x")
        result.add_output(output, "out")
        result.add_metrics(table=str(other))
        results.human_output(
            f"Library {library}\nScratch {other}\nFrom https://example.org/a.bam?token=s3cret"
        )

    assert dispatch(command, tmp_path, card=True, reflib=str(library)) == 0
    html = (tmp_path / "out.txt.card.html").read_text(encoding="utf-8")
    assert str(tmp_path / "private") not in html
    assert "Library reflib" in html and "Scratch x.tmp" in html
    assert "s3cret" not in html


def test_runs_without_card_write_no_card(tmp_path):
    def command(args):
        (tmp_path / "x.txt").write_text("x")
        results.current_result().add_output(tmp_path / "x.txt", "out")

    assert dispatch(command, tmp_path) == 0
    assert not list(tmp_path.glob("*.card.html"))


def test_settings_commands_write_no_card_even_when_asked(tmp_path):
    """result_card = true in config.toml must not litter the output folder
    with a card for every `config` or `help` run."""

    def command(args):
        results.human_output("setting = value")

    for name in ("config", "help"):
        args = argparse.Namespace(
            func=command, command=name, _command=name, outdir=str(tmp_path), card=True
        )
        dispatch_command(args, set())
    assert list(tmp_path.iterdir()) == []


def test_cli_report_only_info_gets_a_card_and_manifest(tmp_path):
    """`info` writes no manifest of its own; with --card it gets both, named
    after the input, so the card's full-path record sits beside it."""
    if not shutil.which("samtools"):
        pytest.skip("samtools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    rc, stdout, stderr = run_cli(
        [
            "info",
            "--input",
            str(FAKE_DIR / "fake.bam"),
            "--ref",
            str(FAKE_DIR / "fake_ref.fa"),
            "--outdir",
            str(tmp_path),
            "--card",
        ]
    )
    assert rc == 0, stderr
    html = (tmp_path / "fake.bam_info.card.html").read_text(encoding="utf-8")
    assert_self_contained(html)
    # The printed summary is on the card, and so is the tool that made it.
    first_line = stdout.strip().splitlines()[0]
    assert first_line.split()[0] in html
    assert "samtools" in html and str(FAKE_DIR) not in html
    manifest = json.loads((tmp_path / "fake.bam_info.manifest.json").read_text())
    assert [o["role"] for o in manifest["outputs"]] == ["result_card"]


def test_cli_result_card_setting_and_no_card_override(tmp_path):
    if not shutil.which("samtools"):
        pytest.skip("samtools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    config = tmp_path / "config.toml"
    config.write_text(
        state_directory_setting(tmp_path) + "result_card = true\n",
        encoding="utf-8",
    )
    base = ["--config", str(config), "extract", "mt-bam", "--input"]
    base += [str(FAKE_DIR / "fake.bam"), "--ref", str(FAKE_DIR / "fake_ref.fa")]

    rc, _, stderr = run_cli([*base, "--outdir", str(tmp_path / "on")])
    assert rc == 0, stderr
    assert (tmp_path / "on" / "fake_mtDNA.bam.card.html").is_file()

    rc, _, stderr = run_cli([*base, "--outdir", str(tmp_path / "off"), "--no-card"])
    assert rc == 0, stderr
    assert not list((tmp_path / "off").glob("*.card.html"))


def test_serve_jobs_write_a_card_when_the_request_asks(tmp_path):
    """The desktop app runs Detailed Coverage as a `wgsextract serve` job,
    which skips CLI dispatch, and asks for a card per job (`?card=1`): only
    that job gets one, and a rerun asks again; `?card=0` overrides
    `serve --card`."""
    if not shutil.which("samtools"):
        pytest.skip("samtools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    work, other = tmp_path / "work", tmp_path / "other"
    work.mkdir()
    other.mkdir()
    card = work / "fake.bam_info.card.html"
    with _serve(tmp_path, [work, other, FAKE_DIR]) as client:
        status, plain = client.request(
            "POST",
            "/ops/info?wait=120",
            {"input": str(FAKE_DIR / "fake.bam"), "outdir": str(other)},
        )
        assert (status, plain["status"]) == (200, "succeeded"), plain
        assert not list(other.glob("*.card.html"))
        params = {"input": str(FAKE_DIR / "fake.bam"), "outdir": str(work)}
        status, job = client.request("POST", "/ops/info?wait=120&card=1", params)
        assert (status, job["status"]) == (200, "succeeded"), job
        card.unlink()
        _, rerun = client.request("POST", f"/jobs/{job['id']}/rerun")
        client.events(rerun["id"])
    roles = {o["path"]: o["role"] for o in job["result"]["outputs"]}
    assert roles == {str(card): "result_card"}
    html = card.read_text(encoding="utf-8")
    assert_self_contained(html)
    assert "Avg Read Length" in html and str(FAKE_DIR) not in html

    with _serve(tmp_path / "s2", [other, FAKE_DIR], card=True) as client:
        params = {"input": str(FAKE_DIR / "fake.bam"), "outdir": str(other)}
        status, job = client.request("POST", "/ops/info?wait=120&card=0", params)
    assert (status, job["status"]) == (200, "succeeded"), job
    assert not list(other.glob("*.card.html"))


def test_cli_info_without_outdir_puts_its_card_in_the_default_wgse_folder(tmp_path):
    """The desktop app runs `info --card` without --outdir; the card was
    skipped ("this run has no output folder") instead of going where outputs
    go by default, wgse/ beside the input."""
    if not shutil.which("samtools"):
        pytest.skip("samtools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    lab = tmp_path / "lab"
    lab.mkdir()
    for name in ("fake.bam", "fake.bam.bai"):
        shutil.copy(FAKE_DIR / name, lab / name)
    rc, _, stderr = run_cli(["info", "--input", str(lab / "fake.bam"), "--card"])
    assert rc == 0, stderr
    assert "No result card written" not in stderr
    card = lab / "wgse" / "fake.bam_info.card.html"
    assert "Avg Read Length" in card.read_text(encoding="utf-8")
    manifest = json.loads((lab / "wgse" / "fake.bam_info.manifest.json").read_text())
    assert [o["path"] for o in manifest["outputs"]] == [str(card)]


def test_analyze_does_not_take_its_steps_cards(tmp_path):
    """PR #1143 review: analyze copied each step's result_card into its own
    outputs, so the app showed a step's card instead of the analyze card."""
    from wgsextract_cli.commands._analyze_workflows import record_step_result

    step = tmp_path / "step.json"
    step.write_text(
        json.dumps(
            {
                "command": "extract mt-bam",
                "outputs": [
                    {"path": str(tmp_path / "s_mtDNA.bam"), "role": "mt_bam"},
                    {
                        "path": str(tmp_path / "s_mtDNA.bam.card.html"),
                        "role": "result_card",
                    },
                ],
            }
        )
    )
    result = results.CommandResult(command="analyze")
    with results.collecting(result):
        record_step_result(step)
    assert [o.role for o in result.outputs] == ["mt_bam"]


def test_concurrent_runs_never_pair_a_card_with_another_runs_manifest(tmp_path):
    """PR #1143 review: concurrent `info` calls on one sample write the same
    card and manifest; interleaved, the card described another run."""
    card = tmp_path / "s_info.card.html"
    barrier = threading.Barrier(2)

    def run(command: str) -> None:
        result = results.CommandResult(command=command)
        result.set_manifest_stem(tmp_path / "s_info")
        barrier.wait()
        result_card.publish(result, card)

    for _ in range(10):
        threads = [
            threading.Thread(target=run, args=(c,)) for c in ("info a", "info b")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        manifest = json.loads((tmp_path / "s_info.manifest.json").read_text())
        assert f"<code>{manifest['command']}</code>" in card.read_text(encoding="utf-8")


def test_server_job_card_stays_inside_the_allowed_folders(tmp_path):
    """PR #1143 review: a server job's card went to a configured output folder
    outside the folders `serve --allow` names."""
    allowed, outside = tmp_path / "data", tmp_path / "outside"
    allowed.mkdir()
    (allowed / "s.bam").write_bytes(b"x")
    result = results.CommandResult(command="info")
    result.add_input(allowed / "s.bam")
    with read_scope.limited_to(lambda p: Path(p).is_relative_to(allowed)):
        assert result_card.card_path(result, outside) is None
        assert result.manifest_path() is None
        assert (
            result_card.card_path(result, allowed / "wgse")
            == allowed / "wgse" / "s.bam_info.card.html"
        )


def test_read_only_mcp_with_card_still_saves_nothing(tmp_path):
    """PR #1143 review: `serve --mcp --card` without --allow-jobs promised to
    save nothing but wrote an `info` card and manifest."""
    if not shutil.which("samtools"):
        pytest.skip("samtools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    data = tmp_path / "data"
    data.mkdir()
    for name in ("fake.bam", "fake.bam.bai"):
        shutil.copy2(FAKE_DIR / name, data / name)
    mcp = Mcp("--card", "--allow", str(data))
    assert mcp.call("info", input=str(data / "fake.bam"))["isError"] is False
    assert mcp.close() == 0
    assert sorted(p.name for p in data.rglob("*")) == ["fake.bam", "fake.bam.bai"]


def test_cli_card_never_replaces_the_run_log(tmp_path):
    """PR #1143 review: a --log-file named like the card was replaced by the
    card while the run still wrote to it."""
    if not shutil.which("samtools"):
        pytest.skip("samtools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    log = tmp_path / "fake.bam_info.card.html"
    rc, _, stderr = run_cli(
        [
            "info",
            "--input",
            str(FAKE_DIR / "fake.bam"),
            "--outdir",
            str(tmp_path),
            "--log-file",
            str(log),
            "--card",
        ]
    )
    assert rc == 0, stderr
    assert "<!doctype html>" not in log.read_text(encoding="utf-8")
    assert "Could not write the result card" in stderr


def test_card_hides_missing_relative_inputs_and_url_layout(tmp_path, monkeypatch):
    """PR #1143 review: a missing input's folder (relative or outside home)
    and a remote input's host and folders reached the shared card."""
    monkeypatch.chdir(tmp_path)
    missing = "lab/run7/sample.bam"

    def command(args):
        results.current_result().expect_output(tmp_path / "out.vcf.gz")
        results.human_output(
            "From https://host.example/project/patient/remote.bam?sig=1."
        )
        raise WGSExtractError(
            f"Input file not found: {missing} (/srv/secret/lab/other.bam)"
        )

    assert (
        dispatch(
            command, tmp_path, card=True, input=missing, ref="/srv/secret/lab/other.bam"
        )
        == 1
    )
    html = (tmp_path / "out.vcf.failed.card.html").read_text(encoding="utf-8")
    assert "Input file not found: sample.bam (other.bam)" in html
    assert "From remote.bam." in html
    for leak in ("run7", "/srv/secret", "host.example", "patient", "sig=1"):
        assert leak not in html, leak


def test_bam_and_cram_of_one_sample_get_cards_of_their_own(tmp_path):
    """PR #1143 review: `info` cards for s.bam and s.cram shared one name."""
    names = []
    for name in ("s.bam", "s.cram"):
        (tmp_path / name).write_bytes(b"x")
        result = results.CommandResult(command="info")
        result.add_input(tmp_path / name)
        card = result_card.card_path(result, tmp_path / "wgse")
        assert card is not None
        names.append(card.name)
    assert names == ["s.bam_info.card.html", "s.cram_info.card.html"]


def test_cli_status_runs_write_no_card(tmp_path):
    """PR #1143 review: `info calculate-coverage --status` promises to write
    nothing, but --card wrote a card and manifest."""
    if not shutil.which("samtools"):
        pytest.skip("samtools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    outdir = tmp_path / "out"
    run_cli(
        [
            "info",
            "calculate-coverage",
            "--status",
            "--input",
            str(FAKE_DIR / "fake.bam"),
            "--outdir",
            str(outdir),
            "--card",
        ]
    )
    assert not outdir.exists() or not list(outdir.iterdir())


def test_cli_missing_input_still_gets_a_failed_card(tmp_path):
    """PR #1143 review: a failed run on a missing input wrote no card, since
    the input was never recorded."""
    outdir = tmp_path / "results"
    rc, _, _ = run_cli(
        [
            "info",
            "--input",
            str(tmp_path / "missing.bam"),
            "--outdir",
            str(outdir),
            "--card",
        ]
    )
    assert rc != 0
    html = (outdir / "missing.bam_info.card.html").read_text(encoding="utf-8")
    assert "Failed" in html and str(tmp_path) not in html


def test_a_successful_retry_retires_the_failed_card(tmp_path):
    """PR #1143 review: after a failed attempt, the successful retry removed
    the failed manifest but left its "Failed" card."""
    output = tmp_path / "s.vcf.gz"

    def failing(args):
        results.current_result().expect_output(output)
        raise WGSExtractError("boom")

    def succeeding(args):
        results.current_result().expect_output(output)
        output.write_bytes(b"x")
        results.current_result().add_output(output, "snps")

    assert dispatch(failing, tmp_path, card=True) == 1
    assert (tmp_path / "s.vcf.failed.card.html").is_file()
    assert dispatch(succeeding, tmp_path, card=True) == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["s.vcf.card.html", "s.vcf.gz", "s.vcf.manifest.json"]


def test_a_card_that_cannot_be_locked_keeps_the_manifest(tmp_path, monkeypatch):
    """PR #1143 review: a card lock that couldn't be taken ended the run
    without its manifest."""
    output = tmp_path / "s.txt"
    locks = tmp_path / "state" / "card-locks"
    locks.parent.mkdir(exist_ok=True)
    locks.write_text("not a folder")  # so no lock can be taken
    monkeypatch.setattr(
        "wgsextract_cli.core.last_run.state_directory", lambda: tmp_path / "state"
    )

    def command(args):
        output.write_text("x")
        results.current_result().add_output(output, "out")

    assert dispatch(command, tmp_path, card=True) == 0
    assert (tmp_path / "s.txt.manifest.json").is_file()
    assert not (tmp_path / "s.txt.card.html").exists()


def test_card_hides_paths_with_spaces(tmp_path):
    """PR #1143 review: the path pass stopped at spaces, leaving folders such
    as "Private Ward" in the card."""
    ward = tmp_path / "Clinical Study" / "Private Ward"
    ward.mkdir(parents=True)
    sample, tool = ward / "sample one.bam", ward / "bin" / "wgse bwa"
    sample.write_bytes(b"x")
    tool.parent.mkdir()
    tool.write_text("x")
    output = tmp_path / "out.txt"

    def command(args):
        results.current_result().add_input(sample)
        output.write_text("x")
        results.current_result().add_output(output, "out")
        results.human_output(f"Read {sample}; skipped {tool}.")

    assert dispatch(command, tmp_path, card=True) == 0
    html = (tmp_path / "out.txt.card.html").read_text(encoding="utf-8")
    assert "Read sample one.bam; skipped wgse bwa." in html
    assert "Private Ward" not in html and "Clinical Study" not in html


def test_internal_steps_write_no_cards_when_config_asks_for_them(tmp_path):
    """PR #1143 review: with result_card = true, analyze's internal steps
    still wrote cards of their own."""
    from wgsextract_cli.core import config

    own = tmp_path / "own.toml"
    own.write_text("result_card = true\n")
    try:
        config.set_config_path(own)
        config.reload_settings()
        assert "--no-card" in config.config_args()
    finally:
        config.set_config_path(None)
        config.reload_settings()
