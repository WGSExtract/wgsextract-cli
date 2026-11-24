"""Read explicit, content-bound cohort inputs without exposing identifiers."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.results import fingerprint
from wgsextract_cli.core.utils import UsageError

SCHEMA = "wgsextract.cohort/1"
CALLERS = {"bcftools", "deepvariant", "gatk", "clair3"}


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str


@dataclass(frozen=True)
class Participant:
    label: str
    sample: str
    variants: Artifact
    evidence: Artifact
    ancestry: Artifact | None
    alignment: Artifact | None
    contamination: Artifact | None

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        return tuple(
            a
            for a in (
                self.variants,
                self.evidence,
                self.ancestry,
                self.alignment,
                self.contamination,
            )
            if a is not None
        )


@dataclass(frozen=True)
class Relatedness:
    result: Artifact
    samples: Artifact
    pairs: Artifact

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        return self.result, self.samples, self.pairs


@dataclass(frozen=True)
class Cohort:
    build: str
    caller: str
    reference: Artifact
    reference_index: Artifact
    participants: tuple[Participant, ...]
    relatedness: Relatedness | None


def local_path(base: Path, value: str) -> Path:
    def remote(path: str) -> bool:
        return (
            "://" in path
            or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]+:/", path) is not None
            or "##idx##" in path
            or path.replace("\\", "/").startswith("//")
        )

    if remote(value):
        raise UsageError(
            "Cohort inputs must be local files, not URLs or network shares."
        )
    try:
        path = (base / Path(value).expanduser()).resolve()
    except (OSError, ValueError, RuntimeError) as error:
        raise UsageError("A cohort path could not be resolved.") from error
    if remote(str(path)):
        raise UsageError("Cohort inputs must resolve to local files.")
    return path


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError
    return value


def _artifact(base: Path, value: object) -> Artifact:
    fields = _object(value)
    digest = _text(fields["sha256"]).lower()
    if re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise ValueError
    return Artifact(local_path(base, _text(fields["path"])), digest)


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError
    return _object(json.loads(path.read_text(encoding="utf-8")))


def _participant(base: Path, value: object, number: int) -> Participant:
    try:
        row = _object(value)

        def optional(key: str) -> Artifact | None:
            return _artifact(base, row[key]) if row.get(key) is not None else None

        alignment, contamination = optional("alignment"), optional("contamination")
        if contamination is not None and alignment is None:
            raise UsageError(
                f"Participant #{number} needs an alignment binding for contamination QC."
            )
        return Participant(
            _text(row["id"]),
            _text(row["sample"]),
            _artifact(base, row["variants"]),
            _artifact(base, row["evidence"]),
            optional("ancestry"),
            alignment,
            contamination,
        )
    except (KeyError, ValueError, OSError) as error:
        raise UsageError(f"Invalid cohort participant #{number} binding.") from error


def read_cohort(path: Path) -> Cohort:
    try:
        manifest = local_path(Path.cwd(), str(path))
        base = manifest.parent
        data = read_json(manifest)
        if data.get("schema") != SCHEMA:
            raise ValueError
        build, caller = canonical_build(_text(data["assembly"])), _text(data["caller"])
        if build is None:
            raise UsageError("Unknown reference assembly in the cohort manifest.")
        if caller not in CALLERS:
            raise UsageError("Unsupported caller in the cohort manifest.")
        reference = _artifact(base, data["reference"])
        index = _artifact(base, data["reference_index"])
        expected_index = local_path(base, str(reference.path) + ".fai")
        if index.path != expected_index:
            raise UsageError("The bound FASTA index must be the reference's .fai.")
        rows = data["participants"]
        if not isinstance(rows, list):
            raise ValueError
        participants = tuple(
            _participant(base, row, n) for n, row in enumerate(rows, 1)
        )
        relatedness = None
        if data.get("relatedness") is not None:
            binding = _object(data["relatedness"])
            relatedness = Relatedness(
                _artifact(base, binding["result"]),
                _artifact(base, binding["samples"]),
                _artifact(base, binding["pairs"]),
            )
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        raise UsageError(f"Invalid cohort manifest; expected {SCHEMA}.") from error
    if len(participants) < 2:
        raise UsageError("A cohort needs at least two participants.")
    if len({p.label for p in participants}) != len(participants):
        raise UsageError("Duplicate participant ID in the cohort manifest.")
    for role in ("variants", "evidence"):
        bindings = {(getattr(p, role).sha256, p.sample) for p in participants}
        if len(bindings) != len(participants):
            raise UsageError(f"Duplicate participant {role} artifact and sample.")
    alignments = [p.alignment.sha256 for p in participants if p.alignment is not None]
    if len(set(alignments)) != len(alignments):
        raise UsageError("Duplicate participant alignment bindings.")
    return Cohort(build, caller, reference, index, participants, relatedness)


def verify_artifact(artifact: Artifact, label: str) -> None:
    try:
        if artifact.path.is_file():
            digest = hashlib.sha256()
            with artifact.path.open("rb") as stream:
                while chunk := stream.read(1 << 20):
                    digest.update(chunk)
            if digest.hexdigest() == artifact.sha256:
                return
    except OSError:
        pass
    raise UsageError(f"Missing file or SHA-256 mismatch for {label}.")


def ancestry_join(
    participant: Participant, number: int, build: str, primary_samples: int
) -> tuple[tuple[str, str], dict[str, float | str]] | None:
    artifact = participant.ancestry
    if artifact is None:
        return None
    label = f"participant #{number}'s ancestry result"
    verify_artifact(artifact, label)
    try:
        data = read_json(artifact.path)
        if data["command"] != "qc ancestry" or data["status"] != "ok":
            raise ValueError
        inputs = {row["role"]: row["fingerprint"] for row in data["inputs"]}
        if inputs["input"] != fingerprint(participant.variants.path):
            raise ValueError
        if "reference_evidence" in inputs and inputs[
            "reference_evidence"
        ] != fingerprint(participant.evidence.path):
            raise ValueError
        params = _object(_object(data.get("provenance", {})).get("params", {}))
        if params.get("sample") != participant.sample and (
            params.get("sample") is not None or primary_samples != 1
        ):
            raise ValueError
        metrics = _object(data["metrics"])
        if canonical_build(_text(metrics["ancestry_build"])) != build:
            raise ValueError
        version = _text(metrics["ancestry_reference_version"])
        panel = _text(inputs["ancestry_reference"])
        values: dict[str, float | str] = {"ancestry_reference_version": version}
        for key in (
            "ancestry_pca_sites_fraction",
            *(f"ancestry_pc{i}" for i in range(1, 11)),
        ):
            value = metrics[key]
            if type(value) not in (float, int) or not math.isfinite(value):
                raise ValueError
            values[key] = value
        if not 0 < float(values["ancestry_pca_sites_fraction"]) <= 1:
            raise ValueError
    except (KeyError, ValueError, OSError, TypeError) as error:
        raise UsageError(f"Unusable or mismatched {label}.") from error
    return (version, panel), values
