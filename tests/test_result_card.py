"""Shareable result cards written by --card (<name>.card.html)."""

import argparse
import json
import re
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tests.smoke_utils import ensure_fake_data, run_cli
from tests.test_server import _serve
from wgsextract_cli import __version__
from wgsextract_cli.cli.dispatch import dispatch_command
from wgsextract_cli.core import results
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
    html = (tmp_path / "fake_info.card.html").read_text(encoding="utf-8")
    assert_self_contained(html)
    # The printed summary is on the card, and so is the tool that made it.
    first_line = stdout.strip().splitlines()[0]
    assert first_line.split()[0] in html
    assert "samtools" in html and str(FAKE_DIR) not in html
    manifest = json.loads((tmp_path / "fake_info.manifest.json").read_text())
    assert [o["role"] for o in manifest["outputs"]] == ["result_card"]


def test_cli_result_card_setting_and_no_card_override(tmp_path):
    if not shutil.which("samtools"):
        pytest.skip("samtools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    config = tmp_path / "config.toml"
    config.write_text("result_card = true\n", encoding="utf-8")
    base = ["--config", str(config), "extract", "mt-bam", "--input"]
    base += [str(FAKE_DIR / "fake.bam"), "--ref", str(FAKE_DIR / "fake_ref.fa")]

    rc, _, stderr = run_cli([*base, "--outdir", str(tmp_path / "on")])
    assert rc == 0, stderr
    assert (tmp_path / "on" / "fake_mtDNA.bam.card.html").is_file()

    rc, _, stderr = run_cli([*base, "--outdir", str(tmp_path / "off"), "--no-card"])
    assert rc == 0, stderr
    assert not list((tmp_path / "off").glob("*.card.html"))


def test_serve_card_jobs_write_cards(tmp_path):
    """The desktop app runs `info` as a `wgsextract serve` job, which skips
    CLI dispatch; `serve --card` must still give each job its card."""
    if not shutil.which("samtools"):
        pytest.skip("samtools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    work = tmp_path / "work"
    work.mkdir()
    params = {"input": str(FAKE_DIR / "fake.bam"), "outdir": str(work)}
    with _serve(tmp_path, [work, FAKE_DIR], card=True) as client:
        status, job = client.request("POST", "/ops/info?wait=120", params)
    assert (status, job["status"]) == (200, "succeeded"), job
    card = work / "fake_info.card.html"
    roles = {o["path"]: o["role"] for o in job["result"]["outputs"]}
    assert roles == {str(card): "result_card"}
    html = card.read_text(encoding="utf-8")
    assert_self_contained(html)
    assert "Avg Read Length" in html and str(FAKE_DIR) not in html


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
    card = lab / "wgse" / "fake_info.card.html"
    assert "Avg Read Length" in card.read_text(encoding="utf-8")
    manifest = json.loads((lab / "wgse" / "fake_info.manifest.json").read_text())
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
