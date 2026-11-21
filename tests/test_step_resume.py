"""Step resume with directory inputs: the step journal's public behaviour.

Issue #858 (step resume skipped reruns after files inside directory inputs
changed): a database folder's contents changed from release one to release
two, yet the finished step was skipped and its output kept release one.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from wgsextract_cli.core.steps import ResumePolicy, Step, StepJournal


def test_1214_journal_lock_cleanup_preserves_original_workflow_error(tmp_path):
    path = tmp_path / "steps.json"
    lock = tmp_path / ".steps.json.lock"
    with pytest.raises(RuntimeError, match="workflow failed"):
        with StepJournal(path, "synthetic", ResumePolicy()):
            lock.unlink()
            lock.mkdir()
            raise RuntimeError("workflow failed")


class Workflow:
    """``annotation`` reads a database folder and writes analysis.txt from its
    release files; ``summary`` reads analysis.txt."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.database = root / "database"
        self.database.mkdir(parents=True)
        self.analysis = root / "analysis.txt"
        self.summary = root / "summary.txt"
        self.journal = root / "steps.json"

    def run(self) -> dict[str, str]:
        """Run both steps; return each step's decision reason (ran or skipped)."""
        reasons = {}
        with StepJournal(self.journal, "synthetic", ResumePolicy()) as journal:
            annotation = Step(
                "annotation",
                inputs={"database": self.database},
                outputs=(self.analysis,),
            )
            first = journal.decide(annotation)
            if first.run:
                with journal.running(first):
                    releases = sorted(self.database.rglob("*.txt"))
                    self.analysis.write_text(
                        "".join(f"from-{p.read_text()}\n" for p in releases)
                    )
            summary = Step(
                "summary", inputs={"analysis": self.analysis}, outputs=(self.summary,)
            )
            second = journal.decide(summary, after=[first])
            if second.run:
                with journal.running(second):
                    lines = self.analysis.read_text().splitlines()
                    self.summary.write_text(f"{len(lines)} releases\n")
            for decision in (first, second):
                verb = "ran" if decision.run else "skipped"
                reasons[decision.step.name] = f"{verb}: {decision.reason}"
        return reasons

    def run_id(self, step: str) -> str:
        document = json.loads(self.journal.read_text())
        return str(document["steps"][step]["run_id"])


def rewrite(path: Path, text: str) -> None:
    """Rewrite ``path`` as a later edit would: a second on, so filesystems
    with coarse timestamps (a few ms) see a new mtime."""
    path.write_text(text)
    mtime = path.stat().st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(mtime, mtime))


@pytest.fixture
def workflow(tmp_path: Path) -> Workflow:
    flow = Workflow(tmp_path / "out")
    (flow.database / "release.txt").write_text("release-one")
    flow.run()
    return flow


def test_858_changed_file_inside_directory_input_reruns_and_regenerates_output(
    workflow: Workflow,
) -> None:
    first_run = workflow.run_id("annotation")

    # The issue's reproduction: same name, same size, new content.
    rewrite(workflow.database / "release.txt", "release-two")
    reasons = workflow.run()

    assert reasons["annotation"].startswith("ran: its database")
    assert workflow.analysis.read_text() == "from-release-two\n"
    assert workflow.run_id("annotation") != first_run
    # The step that reads the regenerated output reruns too.
    assert reasons["summary"].startswith("ran:")


def test_858_unchanged_directory_input_is_skipped(workflow: Workflow) -> None:
    reasons = workflow.run()
    assert all(reason.startswith("skipped: complete") for reason in reasons.values())


@pytest.mark.parametrize("change", ["replace", "add", "remove", "nested"])
def test_858_directory_contents_changes_rerun_the_dependent_steps(
    workflow: Workflow, change: str
) -> None:
    database = workflow.database
    if change == "replace":
        # A new release unpacked over the old one at the same path.
        shutil.rmtree(database)
        database.mkdir()
        (database / "release.txt").write_text("release-2")
    elif change == "add":
        (database / "patch.txt").write_text("patch-one")
    elif change == "remove":
        (database / "release.txt").unlink()
    else:
        (database / "chr1").mkdir()
        (database / "chr1" / "part.txt").write_text("part-one")

    reasons = workflow.run()

    assert reasons["annotation"].startswith("ran: its database")
    assert reasons["summary"].startswith("ran:")
    expected = sorted(database.rglob("*.txt"))
    assert workflow.analysis.read_text() == "".join(
        f"from-{p.read_text()}\n" for p in expected
    )
    assert workflow.summary.read_text() == f"{len(expected)} releases\n"


def test_858_lock_files_in_directory_input_do_not_force_a_rerun(
    workflow: Workflow,
) -> None:
    # Another run downloading into the same library holds a lock there.
    (workflow.database / "release.txt.lock").write_text("{}")
    reasons = workflow.run()
    assert reasons["annotation"].startswith("skipped: complete")


def test_858_journal_from_before_directory_fingerprints_reruns_safely(
    workflow: Workflow,
) -> None:
    # An earlier wgsextract recorded a directory input as only its kind.
    document = json.loads(workflow.journal.read_text())
    for item in document["steps"]["annotation"]["inputs"]:
        for key in ("fingerprint", "files", "size"):
            item.pop(key, None)
    workflow.journal.write_text(json.dumps(document))

    reasons = workflow.run()

    assert "an earlier wgsextract didn't record" in reasons["annotation"]
    assert workflow.analysis.read_text() == "from-release-one\n"
    assert workflow.run()["annotation"].startswith("skipped: complete")


def test_858_directory_a_step_reports_reading_is_checked_on_resume(
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "weights.txt").write_text("weights-one")
    output = tmp_path / "calls.txt"

    def run() -> bool:
        with StepJournal(tmp_path / "s.json", "synthetic", ResumePolicy()) as j:
            decision = j.decide(Step("call", outputs=(output,)))
            if decision.run:
                with j.running(decision) as handle:
                    # A child command names the directory only once it runs.
                    handle.add_input("models", models)
                    output.write_text((models / "weights.txt").read_text())
            return decision.run

    assert run()
    assert not run()
    rewrite(models / "weights.txt", "weights-two")
    assert run()
    assert output.read_text() == "weights-two"


def test_858_file_name_that_is_not_utf8_does_not_break_resume(
    workflow: Workflow,
) -> None:
    try:
        name = os.path.join(os.fsencode(workflow.database), b"latin1-\xe9.txt")
        with open(name, "wb") as stream:
            stream.write(b"x")
    except (OSError, ValueError):
        pytest.skip("this filesystem refuses file names that aren't UTF-8")
    assert workflow.run()["annotation"].startswith("ran: its database")
    assert workflow.run()["annotation"].startswith("skipped: complete")


def test_858_repointed_link_in_directory_input_reruns(tmp_path: Path) -> None:
    flow = Workflow(tmp_path / "out")
    for release in ("a", "b"):
        folder = tmp_path / release
        folder.mkdir()
        (folder / "release.txt").write_text("release-x")
        os.utime(folder / "release.txt", ns=(10**18, 10**18))
    try:
        (flow.database / "current").symlink_to(tmp_path / "a", True)
    except OSError:
        pytest.skip("this system can't make symbolic links")
    flow.run()

    (flow.database / "current").unlink()
    (flow.database / "current").symlink_to(tmp_path / "b", True)

    assert flow.run()["annotation"].startswith("ran: its database")


def test_process_exiting_during_the_tag_scan_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psutil's macOS proc_environ raised SystemError and crashed realign."""
    import psutil

    from wgsextract_cli.core.steps import RUN_TAG, tagged_processes

    class FakeProcess:
        def __init__(self, pid: int, environ: dict[str, str] | None) -> None:
            self.pid = pid
            self.info = {"name": f"tool{pid}"}
            self._environ = environ

        def environ(self) -> dict[str, str]:
            if self._environ is None:
                raise SystemError(
                    "<built-in function proc_environ> returned a result "
                    "with an exception set"
                )
            return self._environ

    processes = [FakeProcess(101, None), FakeProcess(102, {RUN_TAG: "run-1"})]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(processes))
    assert tagged_processes({"run-1"}) == ["tool102 (pid 102)"]


# Issue #1181 (resume integrity): inputs over 3 MiB were compared by sampled
# blocks only, so an edit between them reused a stale step, and a directory
# output was recorded by its kind alone, so deleting a file in it didn't rerun.

MIB = 1 << 20


def copy_step(root: Path, source: Path) -> tuple[str, Path]:
    """Run a step that copies ``source``; return why it ran or was skipped."""
    output = root / "copy.bin"
    with StepJournal(root / "copy.steps.json", "synthetic", ResumePolicy()) as j:
        decision = j.decide(Step("copy", inputs={"source": source}, outputs=(output,)))
        if decision.run:
            with j.running(decision):
                shutil.copyfile(source, output)
    return ("ran: " if decision.run else "skipped: ") + decision.reason, output


def edit_byte(path: Path, offset: int, *, keep_mtime: bool = False) -> None:
    info = path.stat()
    with open(path, "r+b") as stream:
        stream.seek(offset)
        old = stream.read(1)
        stream.seek(offset)
        stream.write(bytes([old[0] ^ 0xFF]))
    if keep_mtime:
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    else:
        later = info.st_mtime_ns + 1_000_000_000
        os.utime(path, ns=(later, later))


def test_1181_edit_outside_sampled_blocks_reruns_the_step(tmp_path: Path) -> None:
    source = tmp_path / "reads.bin"
    source.write_bytes(os.urandom(8 * MIB))
    copy_step(tmp_path, source)
    assert copy_step(tmp_path, source)[0].startswith("skipped: complete"), (
        "an unchanged input reuses the step"
    )

    # The issue's case: one byte at 2 MiB, which the sampled fingerprint
    # (first, middle and last MiB) never reads.
    edit_byte(source, 2 * MIB)
    reason, output = copy_step(tmp_path, source)

    assert reason.startswith("ran: its source")
    assert "sha256 full digest differs" in reason
    assert output.read_bytes() == source.read_bytes()


def test_1181_recorded_digest_cannot_vouch_for_an_edit_that_restores_mtime(
    tmp_path: Path,
) -> None:
    source = tmp_path / "reads.bin"
    source.write_bytes(os.urandom(8 * MIB))
    copy_step(tmp_path, source)

    edit_byte(source, 2 * MIB, keep_mtime=True)

    assert copy_step(tmp_path, source)[0].startswith("ran: its source")


def test_1181_large_input_is_reused_until_modified(tmp_path: Path) -> None:
    from wgsextract_cli.core.step_identity import FULL_DIGEST_LIMIT

    source = tmp_path / "reads.bin"
    with open(source, "wb") as stream:
        stream.truncate(FULL_DIGEST_LIMIT + 8 * MIB)
    copy_step(tmp_path, source)
    # Moved (rename keeps the mtime): still the same data.
    moved = tmp_path / "moved.bin"
    source.rename(moved)
    reason, _ = copy_step(tmp_path, moved)
    assert reason.startswith("skipped: complete")
    assert "wgse-fp1 sampled" in reason

    edit_byte(moved, 2 * MIB)
    reason, _ = copy_step(tmp_path, moved)
    assert reason.startswith("ran: its source")
    assert "was modified" in reason


def test_1181_journal_reports_digest_algorithm_and_strength(tmp_path: Path) -> None:
    source = tmp_path / "reads.bin"
    source.write_bytes(b"ACGT" * 1000)
    copy_step(tmp_path, source)
    record = json.loads((tmp_path / "copy.steps.json").read_text())["steps"]["copy"]
    full = {"algorithm": "sha256", "strength": "full"}
    assert record["inputs"][0]["digest"] == full
    assert record["outputs"][0]["digest"] == full

    reason, _ = copy_step(tmp_path, source)
    assert reason.endswith("checked 2 sha256 full)")


def test_1181_journal_without_digests_reruns_with_a_reason(tmp_path: Path) -> None:
    source = tmp_path / "reads.bin"
    source.write_bytes(b"ACGT" * 1000)
    copy_step(tmp_path, source)
    journal = tmp_path / "copy.steps.json"
    document = json.loads(journal.read_text())
    for item in document["steps"]["copy"]["inputs"]:
        item.pop("digest")
    journal.write_text(json.dumps(document))

    reason, _ = copy_step(tmp_path, source)

    assert "without an integrity digest" in reason
    assert copy_step(tmp_path, source)[0].startswith("skipped: complete")


class Report:
    """A step writing a report folder: ``table.tsv`` and ``plot.svg``, plus a
    ``cache`` it may later reuse or lose."""

    def __init__(self, root: Path, members: list[str] | None = None) -> None:
        self.root = root
        self.folder = root / "report"
        self.members = members

    def run(self) -> str:
        with StepJournal(self.root / "r.steps.json", "synthetic", ResumePolicy()) as j:
            decision = j.decide(Step("report", outputs=(self.folder,)))
            if decision.run:
                with j.running(decision) as handle:
                    self.folder.mkdir(exist_ok=True)
                    (self.folder / "table.tsv").write_text("gene\tcalls\nAPOE\t2\n")
                    (self.folder / "plot.svg").write_text("<svg/>")
                    (self.folder / "cache").mkdir(exist_ok=True)
                    (self.folder / "cache" / "tiles.bin").write_bytes(b"0" * 64)
                    if self.members is not None:
                        handle.add_output(self.folder, members=self.members)
        return ("ran: " if decision.run else "skipped: ") + decision.reason


@pytest.mark.parametrize("change", ["delete", "replace", "truncate"])
@pytest.mark.parametrize("members", [None, ["table.tsv", "plot.svg"]])
def test_1181_changed_required_member_of_directory_output_reruns(
    tmp_path: Path, change: str, members: list[str] | None
) -> None:
    report = Report(tmp_path, members)
    report.run()
    assert report.run().startswith("skipped: complete")
    table = report.folder / "table.tsv"
    if change == "delete":
        table.unlink()
    elif change == "replace":
        rewrite(table, "gene\tcalls\nAPOE\t3\n")
    else:
        table.write_text("gene")

    assert report.run().startswith("ran: ")
    assert table.read_text() == "gene\tcalls\nAPOE\t2\n"
    assert report.run().startswith("skipped: complete")


def test_1181_disposable_member_of_directory_output_does_not_rerun(
    tmp_path: Path,
) -> None:
    report = Report(tmp_path, ["table.tsv", "plot.svg"])
    report.run()
    shutil.rmtree(report.folder / "cache")
    (report.folder / "table.tsv.lock").write_text("")
    assert report.run().startswith("skipped: complete")


def test_1181_without_declared_members_every_file_is_required(tmp_path: Path) -> None:
    report = Report(tmp_path)
    report.run()
    (report.folder / "table.tsv.lock").write_text("")
    assert report.run().startswith("skipped: complete")
    (report.folder / "cache" / "tiles.bin").unlink()
    assert "report's contents changed" in report.run()


def test_1181_declared_member_must_exist(tmp_path: Path) -> None:
    from wgsextract_cli.core.utils import WGSExtractError

    report = Report(tmp_path, ["table.tsv", "summary.html"])
    with pytest.raises(WGSExtractError, match="summary.html"):
        report.run()


def plan(root: Path, step: Step) -> str:
    """The dry-run reason for ``step`` in ``root``'s copy journal."""
    with StepJournal(
        root / "copy.steps.json", "synthetic", ResumePolicy(dry_run=True)
    ) as journal:
        decision = journal.decide(step)
    return ("ran: " if decision.run else "skipped: ") + decision.reason


def test_1181_output_replaced_by_a_fifo_reruns_without_blocking(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("this system has no named pipes")
    source = tmp_path / "reads.bin"
    source.write_bytes(b"ACGT" * 1000)
    _, output = copy_step(tmp_path, source)
    output.unlink()
    os.mkfifo(output)

    reason = plan(tmp_path, Step("copy", inputs={"source": source}, outputs=(output,)))

    assert reason == "ran: copy.bin is no longer a regular file"


def test_1181_unreadable_large_input_is_not_a_digest_match(tmp_path: Path) -> None:
    from wgsextract_cli.core.step_identity import FULL_DIGEST_LIMIT

    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("needs POSIX permissions that bind this user")
    source = tmp_path / "reads.bin"
    with open(source, "wb") as stream:
        stream.truncate(FULL_DIGEST_LIMIT + MIB)
    _, output = copy_step(tmp_path, source)
    # A completion whose sampled read failed recorded no fingerprint.
    journal = tmp_path / "copy.steps.json"
    document = json.loads(journal.read_text())
    document["steps"]["copy"]["inputs"][0]["fingerprint"] = None
    journal.write_text(json.dumps(document))
    source.chmod(0)
    try:
        reason = plan(
            tmp_path, Step("copy", inputs={"source": source}, outputs=(output,))
        )
    finally:
        source.chmod(0o644)

    assert reason.startswith("ran: its source")
    assert "couldn't be read to verify its digest" in reason


def test_1181_skip_reason_counts_inputs_a_step_reported_while_running(
    tmp_path: Path,
) -> None:
    from wgsextract_cli.core.step_identity import FULL_DIGEST_LIMIT

    reference = tmp_path / "reference.fa"
    with open(reference, "wb") as stream:
        stream.truncate(FULL_DIGEST_LIMIT + MIB)
    output = tmp_path / "calls.txt"
    step = Step("call", outputs=(output,))
    with StepJournal(tmp_path / "copy.steps.json", "synthetic", ResumePolicy()) as j:
        with j.running(j.decide(step)) as handle:
            # A child command names its reference only once it runs.
            handle.add_input("reference", reference)
            output.write_text("calls")

    reason = plan(tmp_path, step)

    assert reason.startswith("skipped: complete")
    assert reason.endswith("checked 1 sha256 full, 1 wgse-fp1 sampled)")
