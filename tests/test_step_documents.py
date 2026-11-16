"""Step writer contracts and tolerant resume boundaries (#929)."""

import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from wgsextract_cli.core.steps import ResumePolicy, Step, StepJournal

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.steps.1.schema.json"


def validate(value: object, definition: str | None = None) -> None:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    if definition is not None:
        schema = {
            "$ref": f"#/$defs/{definition}",
            "$defs": schema["$defs"],
        }
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)


def test_929_step_schema_tracks_writer_models() -> None:
    subprocess.run(
        [sys.executable, "scripts/gen_output_schemas.py", "--check"],
        cwd=ROOT,
        check=True,
    )


def test_929_running_complete_identities_and_extensions_validate_and_resume(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("reads", encoding="utf-8")
    database = tmp_path / "database"
    database.mkdir()
    (database / "weights.txt").write_text("weights", encoding="utf-8")
    output = tmp_path / "output.txt"
    extra = tmp_path / "extra.txt"
    directory = tmp_path / "cache"
    path = tmp_path / "workflow.steps.json"
    step = Step(
        "first",
        inputs={"source": source, "database": database},
        outputs=(output, directory),
        params={"paths": (source, database)},
    )
    with StepJournal(path, "example", ResumePolicy()) as journal:
        with journal.running(journal.decide(step)) as run:
            running = json.loads(path.read_text(encoding="utf-8"))
            validate(running)
            validate(running["steps"]["first"], "runningStep")
            for identity in running["steps"]["first"]["inputs"]:
                validate(identity, "input")
            output.write_text("calls", encoding="utf-8")
            directory.mkdir()
            extra.write_text("extra", encoding="utf-8")
            run.add_input("reported", source)
            run.add_output(
                extra,
                size="overridden",
                fingerprint="overridden",
                extension={"custom": (True, source)},
            )
            run.metrics["nested"] = {"path": source, "values": (1, None)}
        complete = json.loads(path.read_text(encoding="utf-8"))
        validate(complete)
        record = complete["steps"]["first"]
        validate(record, "completeStep")
        for item in record["outputs"]:
            validate(
                item,
                "directoryOutput" if item.get("kind") == "directory" else "fileOutput",
            )
        recorded_extra = next(
            item for item in record["outputs"] if item["path"] == extra.name
        )
        assert recorded_extra["size"] == len("extra")
        assert recorded_extra["fingerprint"] != "overridden"
        assert recorded_extra["extension"] == {"custom": [True, str(source)]}
        assert record["metrics"]["nested"] == {"path": str(source), "values": [1, None]}
        assert not journal.decide(step).run
        journal.retire("first", (extra,))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert (
        next(
            item
            for item in saved["steps"]["first"]["outputs"]
            if item["path"] == extra.name
        )["retired"]
        is True
    )


def test_929_unclassified_records_and_extensions_survive_unrelated_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workflow.steps.json"
    historical_record = {"custom": {"nested": [1, None]}, "started": None}
    historical = {
        "schema": "wgsextract.steps/1",
        "workflow": "example",
        "steps": {"historical": historical_record},
    }
    path.write_text(json.dumps(historical), encoding="utf-8")
    output = tmp_path / "output.txt"
    with StepJournal(path, "example", ResumePolicy()) as journal:
        with journal.running(journal.decide(Step("new", outputs=(output,)))):
            output.write_text("new output", encoding="utf-8")
    saved = json.loads(path.read_text(encoding="utf-8"))
    validate(saved)
    validate(saved["steps"]["new"], "completeStep")
    assert saved["steps"]["historical"] == historical_record
