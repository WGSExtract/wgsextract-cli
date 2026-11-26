"""Focused realign resume regressions."""

import json
import os
import platform
import re
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, sam_to_bam
from tests.test_realign import (
    _args as _args,
)
from tests.test_realign import (
    _dying_tools as _dying_tools,
)
from tests.test_realign import (
    _Fakes as _Fakes,
)
from tests.test_realign import (
    _old_alignment as _old_alignment,
)
from tests.test_realign import (
    _realign_cli as _realign_cli,
)
from tests.test_realign import (
    _records as _records,
)
from tests.test_realign import (
    _view as _view,
)
from tests.test_realign import (
    _write_realign_reference as _write_realign_reference,
)
from tests.test_realign import (
    genome_and_ref as genome_and_ref,
)
from tests.test_realign import (
    roomy_disk as roomy_disk,
)
from tests.test_realign import (
    source as source,
)
from wgsextract_cli.commands import realign
from wgsextract_cli.commands.realignment import mapping as realign_mapping
from wgsextract_cli.commands.realignment import steps as realign_steps
from wgsextract_cli.core.utils import WGSExtractError


@pytest.fixture(scope="module")
def resume_input(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, str, str]:
    root = tmp_path_factory.mktemp("realign-resume")
    genome, ref = _write_realign_reference(root)
    source, _ = _old_alignment(root, genome)
    return root, source, ref


def _resume_argv(source: str, ref: str, out: Path) -> list[str]:
    return [
        "--input", str(source), "--ref", ref, "--outdir", str(out),
        "--threads", "2", "--sample", "Ann",
    ]  # fmt: skip


@pytest.fixture(scope="module")
def clean_resume_records(
    resume_input: tuple[Path, str, str],
) -> tuple[list[str], str]:
    root, source, ref = resume_input
    baseline = root / "keep-fastq"
    baseline.mkdir()
    shims = _dying_tools(baseline, baseline / "calls.log")
    out = baseline / "out"
    clean = _realign_cli([*_resume_argv(source, ref, out), "--keep-fastq"], shims)
    assert clean.returncode == 0, clean.stderr
    return _records(out / "x_aligned.bam")


def _retained_fastq_argv(source: str, ref: str, out: Path, scratch: Path) -> list[str]:
    return [
        *_resume_argv(source, ref, out),
        "--tmp-dir", str(scratch), "--memory", "64M",
        "--native-aligners", "off", "--json",
    ]  # fmt: skip


@pytest.fixture(scope="module")
def clean_retained_fastq_records(
    resume_input: tuple[Path, str, str],
) -> tuple[list[str], str]:
    root, source, ref = resume_input
    baseline = root / "retained-fastq"
    baseline.mkdir()
    scratch = baseline / "scratch"
    scratch.mkdir()
    shims = _dying_tools(baseline, baseline / "calls.log")
    out = baseline / "out"
    clean = _realign_cli(
        _retained_fastq_argv(source, ref, out, scratch),
        shims,
        available_memory=1024**3,
    )
    assert clean.returncode == 0, clean.stderr
    return _records(out / "x_aligned.bam")


@pytest.mark.parametrize("failed", [False, True])
def test_force_refreshes_exact_output_cache_with_legacy_retention(
    tmp_path, monkeypatch, source, failed
):
    fakes = _Fakes(monkeypatch)
    pair = (str(tmp_path / "x_R1.fastq.gz"), str(tmp_path / "x_R2.fastq.gz"))
    for path in pair:
        Path(path).write_text("stale")
    if failed:
        fakes.align_error = WGSExtractError("alignment failed")
        with pytest.raises(WGSExtractError, match="alignment failed"):
            realign.run(_args(source, force_extract=True))
        assert all(Path(path).read_text() == "@r\nA\n+\nI\n" for path in pair)
    else:
        realign.run(_args(source, force_extract=True))
        assert not any(Path(path).exists() for path in pair)
    assert fakes.extracted == [pair] and fakes.aligned == [pair]
    assert fakes.streamed == []


def test_failed_realign_retains_selected_workspace_fastqs_for_recovery(
    tmp_path, monkeypatch, source, caplog
):
    from wgsextract_cli.core.temp_directory import run_temporary_directory

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fakes = _Fakes(monkeypatch)
    monkeypatch.setattr(
        realign_steps, "collate_working_memory", lambda *_a, **_kw: 2 * 1024**4
    )
    fakes.align_error = RuntimeError("alignment interrupted")
    with (
        pytest.raises(RuntimeError, match="alignment interrupted"),
        run_temporary_directory(scratch),
    ):
        realign.run(_args(source, outdir=str(tmp_path / "out")))
    pair = fakes.extracted[0]
    workspace = Path(pair[0]).parent
    assert workspace.parent == scratch and workspace.exists()
    assert all(Path(path).read_text() == "@r\nA\n+\nI\n" for path in pair)
    assert str(workspace) in caplog.text and "--r1 and --r2" in caplog.text
    fakes.align_error = None
    with run_temporary_directory(scratch):
        realign.run(_args(source, r1=pair[0], r2=pair[1]))
    assert fakes.aligned == [pair, pair] and len(fakes.extracted) == 1
    assert all(Path(path).exists() for path in pair)


@needs_tools("samtools")
def test_force_step_index_reindexes_an_alignment_its_aligner_indexed(
    tmp_path, monkeypatch, source
):
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(realign_mapping, "indexed_read_count", lambda *_args: 2)
    monkeypatch.setattr(realign_steps, "indexed_read_count", lambda *_args: 2)
    aligned = sam_to_bam(
        ["@SQ\tSN:chr1\tLN:100", "r\t0\tchr1\t1\t60\t4M\t*\t0\t0\tACGT\tIIII"],
        tmp_path / "a.bam",
    )
    out = tmp_path / "x_aligned.bam"
    calls = []

    def align_ultima(*_args):
        calls.append("align")
        out.write_bytes(Path(aligned).read_bytes())
        Path(f"{out}.bai").write_text("the aligner's index")

    monkeypatch.setattr(realign.align_cmd, "align_ultima", align_ultima)
    args = {"platform": "ultima", "aligner": "bwa", "force_steps": None}
    realign.run(_args(source, **args))
    realign.run(_args(source, **args))
    assert calls == ["align"]

    realign.run(_args(source, **{**args, "force_steps": ("index",)}))
    assert calls == ["align"]
    assert Path(f"{out}.bai").read_bytes().startswith(b"BAI\x01")


@pytest.mark.skipif(platform.system() == "Windows", reason="bash PATH shims")
@needs_tools("bwa", "samtools")
@pytest.mark.parametrize(
    "die, signal",
    [("bwa mem", "9"), ("bwa mem", "TERM"), ("samtools sort", "9"),
     ("samtools sort", "INT")],
)  # fmt: skip
def test_a_run_killed_mid_step_resumes_there_with_identical_output(
    tmp_path, resume_input, clean_resume_records, die, signal
):
    _, source, ref = resume_input
    log = tmp_path / "calls.log"
    shims = _dying_tools(tmp_path, log)

    def realign_to(outdir, die=None):
        log.write_text("")
        argv = [*_resume_argv(source, ref, outdir), "--keep-fastq"]
        done = _realign_cli(argv, shims, die, signal)
        calls = [c for c in log.read_text().splitlines() if c != "samtools --version"]
        return done, calls

    out = tmp_path / "out"
    killed, calls = realign_to(out, die=die)
    assert killed.returncode != 0
    assert any(re.fullmatch(die, call) for call in calls)
    bam = out / "x_aligned.bam"
    journal = json.loads((out / "x_aligned.bam.steps.json").read_text())
    assert journal["steps"]["extract"]["status"] == "complete"
    if signal != "9":
        # A run stopped by a signal reports it in the workflow's manifest.
        manifest = json.loads(
            (out / "manifests" / "x_aligned.bam.manifest.json").read_text()
        )
        assert manifest["status"] == "cancelled"
    assert not list(out.glob("*.steps.json.manifest.json"))

    resumed, calls = realign_to(out)
    assert resumed.returncode == 0, resumed.stderr
    assert "Running step extract" not in resumed.stderr
    # Nothing before the step it was killed in runs again.
    assert not {"samtools collate", "samtools fastq"} & set(calls)
    assert "Skipping step extract: complete" in resumed.stderr
    assert "Running step align: an earlier run was interrupted" in resumed.stderr
    assert any(re.fullmatch(die, call) for call in calls)
    assert _records(bam) == clean_resume_records
    reindexed = tmp_path / "reindexed.bai"
    _view("index", "-b", "-o", str(reindexed), str(bam))
    assert Path(str(bam) + ".bai").read_bytes() == reindexed.read_bytes()
    assert not [p.name for p in out.iterdir() if "partial" in p.name]

    again, calls = realign_to(out)
    assert again.returncode == 0 and "bwa mem" not in calls
    assert not any(call.endswith(" index") for call in calls)


@pytest.mark.skipif(platform.system() == "Windows", reason="bash PATH shims")
@needs_tools("bwa", "samtools")
@pytest.mark.parametrize(
    "cache_state,temp_alias",
    [
        ("valid", False),
        ("truncated", False),
        ("valid", True),
        ("writable-directory", False),
        ("writable-file", False),
        ("foreign-owner", False),
        ("unsafe-temp-root", False),
        ("sticky-temp-root", False),
    ],
)
def test_722_sigint_resume_reuses_valid_retained_temp_fastqs(
    tmp_path, resume_input, clean_retained_fastq_records, cache_state, temp_alias
):
    _, source, ref = resume_input
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    if temp_alias:
        alias = tmp_path / "scratch-alias"
        alias.symlink_to(scratch, target_is_directory=True)
        scratch = alias
    shims = _dying_tools(tmp_path, tmp_path / "calls.log")

    def run(outdir, die=None, effective_uid=None):
        return _realign_cli(
            _retained_fastq_argv(source, ref, outdir, scratch),
            shims,
            die,
            "INT",
            available_memory=1024**3,
            effective_uid=effective_uid,
        )

    out = tmp_path / "out"
    stopped = run(out, die="bwa mem")
    assert stopped.returncode == 130, stopped.stderr
    journal_path = out / "x_aligned.bam.steps.json"
    extraction = json.loads(journal_path.read_text())["steps"]["extract"]
    assert extraction["status"] == "complete"
    retained = [Path(item["path"]) for item in extraction["outputs"]]
    assert all(path.is_file() for path in retained)
    if cache_state == "truncated":
        retained[0].write_bytes(b"")
    elif cache_state == "writable-directory":
        retained[0].parent.chmod(0o777)
    elif cache_state == "writable-file":
        retained[0].chmod(0o666)
    elif cache_state == "unsafe-temp-root":
        scratch.chmod(0o777)
    elif cache_state == "sticky-temp-root":
        scratch.chmod(0o1777)

    resumed = run(
        out,
        effective_uid=os.geteuid() + 1 if cache_state == "foreign-owner" else None,
    )
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout)
    trusted = cache_state in {"valid", "sticky-temp-root"}
    assert result["metrics"]["steps_skipped"] == (1 if trusted else 0)
    assert _records(out / "x_aligned.bam") == clean_retained_fastq_records
    if trusted:
        assert not any(path.exists() for path in retained)
    else:
        assert all(path.exists() for path in retained)


@pytest.mark.skipif(platform.system() == "Windows", reason="bash PATH shims")
@needs_tools("bwa", "samtools")
def test_truncated_outputs_and_changed_settings_redo_their_steps(
    tmp_path, genome_and_ref
):
    genome, ref = genome_and_ref
    source, _ = _old_alignment(tmp_path, genome)
    log = tmp_path / "calls.log"
    shims = _dying_tools(tmp_path, log)
    out = tmp_path / "out"
    bam = out / "x_aligned.bam"

    def realign_again(*extra):
        log.write_text("")
        argv = ["--input", str(source), "--ref", ref, "--outdir", str(out),
                "--threads", "2", "--keep-fastq", *extra]  # fmt: skip
        done = _realign_cli(argv, shims)
        assert done.returncode == 0, done.stderr
        return done.stderr, log.read_text().splitlines()

    realign_again()
    first = _records(bam)

    bam.write_bytes(bam.read_bytes()[:-100])
    stderr, calls = realign_again()
    assert "truncated or rewritten" in stderr
    assert "bwa mem" in calls and "samtools collate" not in calls
    assert _records(bam) == first

    stderr, calls = realign_again("--sample", "Bea")
    assert "settings changed: sample None -> 'Bea'" in stderr
    assert "bwa mem" in calls and "samtools collate" not in calls

    # A damaged FASTQ doesn't matter while the alignment made from it is
    # sound, but it is extracted again before it is aligned again.
    fastq = out / "x_R1.fastq.gz"
    fastq.write_bytes(fastq.read_bytes()[:-10])
    stderr, calls = realign_again("--sample", "Bea")
    assert "bwa mem" not in calls
    stderr, calls = realign_again("--sample", "Bea", "--force-step", "align")
    assert "Running step extract: x_R1.fastq.gz changed size" in stderr
    assert {"samtools collate", "bwa mem"} <= set(calls)


@needs_tools("bwa", "samtools")
def test_a_second_run_into_the_same_output_is_refused(tmp_path, genome_and_ref):
    from wgsextract_cli.core.steps import ResumePolicy, StepJournal

    genome, ref = genome_and_ref
    source, _ = _old_alignment(tmp_path, genome)
    out = tmp_path / "out"
    journal = out / "x_aligned.bam.steps.json"
    with StepJournal(journal, "realign", ResumePolicy()):
        with pytest.raises(WGSExtractError, match="Another wgsextract run"):
            realign.run(_args(source, ref=ref, outdir=str(out)))


def _tree(folder: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(folder)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in folder.rglob("*")
    }


@pytest.mark.skipif(platform.system() == "Windows", reason="bash PATH shims")
@needs_tools("bwa", "samtools")
@pytest.mark.parametrize("signal", ["9", "INT"])
def test_fastqs_staged_in_the_output_survive_a_kill_and_go_after_the_resume(
    tmp_path, genome_and_ref, signal
):
    """The staged-command rule: without --tmp-dir, realign's FASTQs (stage 1)
    wait in the output folder. A run killed while aligning keeps them; the
    rerun resumes at the alignment and, once it succeeds, removes them, so
    only the files the user keeps are left. The input's folder is never
    written."""
    genome, ref = genome_and_ref
    source, _ = _old_alignment(tmp_path, genome)
    inputs = _tree(Path(source).parent)
    log = tmp_path / "calls.log"
    shims = _dying_tools(tmp_path, log)
    out = tmp_path / "out"

    def run(die=None):
        log.write_text("")
        argv = ["--input", str(source), "--ref", ref, "--outdir", str(out),
                "--threads", "2", "--memory", "64M", "--native-aligners", "off",
                "--sample", "Ann"]  # fmt: skip
        # 1 GiB available forces FASTQ files instead of streaming them.
        done = _realign_cli(argv, shims, die, signal, available_memory=1024**3)
        return done, log.read_text().splitlines()

    killed, calls = run(die="bwa mem")
    assert killed.returncode != 0
    assert "samtools collate" in calls
    assert sorted(p.name for p in out.glob("*.fastq.gz")) == [
        "x_R1.fastq.gz",
        "x_R2.fastq.gz",
    ]

    resumed, calls = run()
    assert resumed.returncode == 0, resumed.stderr
    assert "Skipping step extract: complete" in resumed.stderr
    assert "samtools collate" not in calls and "bwa mem" in calls
    # The journal's lock guard, made when the killed run's lock was
    # reclaimed, is kept between runs by design (core/file_lock.py).
    guard = ".x_aligned.bam.steps.json.lock.guard"
    assert sorted(p.name for p in out.iterdir() if p.name != guard) == [
        "manifests",
        "x_aligned.bam",
        "x_aligned.bam.bai",
        "x_aligned.bam.steps.json",
    ]
    assert (out / "manifests" / "x_aligned.bam.manifest.json").is_file()
    assert _tree(Path(source).parent) == inputs
