"""Join previously computed QC only to the participant and files it describes."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from wgsextract_cli.commands.cohort_inputs import (
    Artifact,
    Cohort,
    Participant,
    Relatedness,
    ancestry_join,
    read_json,
    verify_artifact,
)
from wgsextract_cli.core.ref_library.assets import input_stem
from wgsextract_cli.core.results import fingerprint
from wgsextract_cli.core.utils import UsageError


def _qc_result(artifact: Artifact, command: str, label: str) -> dict[str, Any]:
    verify_artifact(artifact, label)
    data = read_json(artifact.path)
    if data.get("command") != command or data.get("status") != "ok":
        raise ValueError
    return data


def _contamination(person: Participant, number: int) -> dict[str, object] | None:
    if person.alignment is None:
        return None
    label = f"participant #{number}'s"
    verify_artifact(person.alignment, f"{label} QC alignment")
    if person.contamination is None:
        return None
    try:
        qc = _qc_result(
            person.contamination, "qc contamination", f"{label} contamination result"
        )
        bindings = [
            row["fingerprint"] for row in qc["inputs"] if row["role"] == "input"
        ]
        if bindings != [fingerprint(person.alignment.path)]:
            raise ValueError
        metrics = qc["metrics"]
        value, confidence = metrics["freemix"], metrics["freemix_confidence"]
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError
        if confidence not in {"high", "medium", "low"}:
            raise ValueError
        return {"freemix": value, "confidence": confidence}
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise UsageError(
            f"Unusable or mismatched {label} contamination result."
        ) from error


def _records(artifact: Artifact, fields: set[str]) -> Iterable[dict[str, str]]:
    if artifact.path.suffix.lower() != ".tsv":
        raise ValueError
    with artifact.path.open(encoding="utf-8", newline="") as stream:
        records = csv.DictReader(stream, delimiter="\t")
        if not fields.issubset(records.fieldnames or ()):
            raise ValueError
        yield from records


def _read_relatedness(
    relatedness: Relatedness, participants: tuple[Participant, ...]
) -> dict[int, dict[str, object]]:
    qc = _qc_result(relatedness.result, "qc relate", "the relatedness result")
    for table, role in (
        (relatedness.samples, "relate_samples"),
        (relatedness.pairs, "relate_pairs"),
    ):
        verify_artifact(table, f"the {role} table")
        matching = [row["fingerprint"] for row in qc["outputs"] if row["role"] == role]
        if matching != [fingerprint(table.path)]:
            raise ValueError
    inputs = {row["fingerprint"] for row in qc["inputs"] if row["role"] == "input"}
    sources: dict[tuple[Path, str], set[int]] = defaultdict(set)
    for index, person in enumerate(participants):
        for artifact in (person.variants, person.alignment):
            if artifact is not None and fingerprint(artifact.path) in inputs:
                sources[artifact.path, person.sample].add(index)
                # qc relate prefixes names that occur in more than one input.
                sources[
                    artifact.path, f"{input_stem(artifact.path)}_{person.sample}"
                ].add(index)
    by_sample: dict[str, int] = {}
    joined: dict[int, dict[str, object]] = {}
    for row in _records(
        relatedness.samples,
        {"input", "sample", "sex", "sex_confidence", "called_sites_only"},
    ):
        # Never follow a path named by an earlier result or by the table.
        source = Path(row["input"])
        matches = sources.get((source, row["sample"]))
        if not matches:
            continue
        if len(matches) != 1:
            raise ValueError
        number = next(iter(matches))
        if number in joined or row["sample"] in by_sample:
            raise ValueError
        by_sample[row["sample"]] = number
        joined[number] = {
            "sex": row["sex"],
            "sex_confidence": row["sex_confidence"],
            "called_sites_only": row["called_sites_only"] == "true",
            "flagged_pairs": 0,
        }
    if not joined:
        raise ValueError
    for row in _records(
        relatedness.pairs, {"sample_a", "sample_b", "relationship", "flagged"}
    ):
        first, second = by_sample.get(row["sample_a"]), by_sample.get(row["sample_b"])
        if first is None or second is None:
            continue
        if first != second and row["relationship"] == "same person":
            raise UsageError(
                "Relatedness QC identifies duplicate participants in the cohort."
            )
        if row["flagged"] == "true":
            for index in (first, second):
                count = joined[index]["flagged_pairs"]
                assert isinstance(count, int)
                joined[index]["flagged_pairs"] = count + 1
    return joined


def join(cohort: Cohort, sample_counts: Iterable[int]) -> list[dict[str, object]]:
    try:
        related = (
            _read_relatedness(cohort.relatedness, cohort.participants)
            if cohort.relatedness
            else {}
        )
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise UsageError(
            "Unusable or mismatched cohort relatedness result/tables."
        ) from error
    joined: list[dict[str, object]] = []
    panels = set()
    for index, (person, samples) in enumerate(
        zip(cohort.participants, sample_counts, strict=True)
    ):
        ancestry = ancestry_join(person, index + 1, cohort.build, samples)
        if ancestry is not None:
            panels.add(ancestry[0])
        joined.append(
            {
                "id": person.label,
                "sample": person.sample,
                "variants": str(person.variants.path),
                "evidence": str(person.evidence.path),
                "alignment": str(person.alignment.path) if person.alignment else None,
                "ancestry": ancestry[1] if ancestry is not None else None,
                "contamination": _contamination(person, index + 1),
                "relatedness": related.get(index),
            }
        )
    if len(panels) > 1:
        raise UsageError("Cohort ancestry results have incompatible reference panels.")
    return joined
