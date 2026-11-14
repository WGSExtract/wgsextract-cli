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
