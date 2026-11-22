"""Run lineage and typed BAM/VCF artifacts in result manifests (pilot, #1182).

Every result document carries a ``run`` record: this run's ``id``, the
``root`` run of the workflow it belongs to, the ``parent`` that started it
and its ``step`` there, and, for a workflow, the ``children`` its steps ran or
reused, with the step journal's reason. A BAM, CRAM, VCF or BCF a run read or
wrote gets an ``artifact`` record: what the file is scientifically (assembly,
samples, index readiness), what it is to the workflow (final, intermediate,
cache or temp, kept or discarded) and where it came from (the producing run
and its source files). docs/result-manifest.md describes the fields.

Facts come from the file itself (``artifact_facts``: its header and its
index, read without running a tool) and its fingerprint. A producer comes from the
manifest beside the file, and only when that manifest lists the file with the
same fingerprint; otherwise the file is ``external`` and nothing about its
history is assumed.

``artifact_checks.require_consumable`` is the consumer side: it refuses an input whose
assembly, reference, sample or index doesn't fit before the scientific tool
starts, with a stable ``code`` on the error.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from wgsextract_cli.core.artifact_facts import (
    GENOMIC_KINDS,
    ArtifactIndex,
    file_facts,
    index_state,
    read_header,
)
from wgsextract_cli.core.output_schema import Constraints

if TYPE_CHECKING:
    from wgsextract_cli.core.results import CommandResult, FileRecord

Lifecycle = Literal["final", "intermediate", "cache", "temp"]
Origin = Literal["this_run", "recorded", "external"]
RunId = Annotated[str, Constraints(pattern="^[0-9a-f]{32}$")]
ArtifactFingerprint = Annotated[
    str | None,
    Constraints(description="wgse-fp1 fingerprint, as a file record's."),
]
_RUN_ID = re.compile("[0-9a-f]{32}")
_FULL_DIGEST_LIMIT = 3 << 20  # results.fingerprint hashes whole files this small


def new_run_id() -> str:
    return uuid.uuid4().hex


@dataclass
class ChildRun:
    """A step of this workflow and the run that did its work."""

    step: str
    run: Annotated[
        RunId | None,
        Constraints(description="The step's command run; null when it left no result."),
    ] = None
    command: str | None = None
    status: Literal["ok", "failed", "cancelled", "reused"] = "ok"
    reason: Annotated[
        str, Constraints(description="Why the step ran or was reused.")
    ] = ""


@dataclass
class RunRecord:
    """This run's identity in its workflow."""

    id: RunId = field(default_factory=new_run_id)
    root: Annotated[
        RunId | None,
        Constraints(description="The workflow's first run; this run's id at the top."),
    ] = None
    parent: Annotated[
        RunId | None, Constraints(description="The run that started this one.")
    ] = None
    step: Annotated[
        str | None, Constraints(description="This run's step in its parent.")
    ] = None
    children: list[ChildRun] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = self.root or self.id

    def this_run(self, command: str) -> dict[str, str]:
        return {"run": self.id, "command": command}

    def record_step(
        self,
        step: str,
        metrics: Mapping[str, object],
        reason: str,
        outcome: Literal["ok", "failed", "reused"],
    ) -> None:
        """List the command runs that did workflow ``step``'s work (in
        ``metrics["result_runs"]``), now or, ``reused``, in an earlier run."""
        runs = metrics.get("result_runs")
        items = (
            [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []
        )
        for item in items or [{}]:
            status = item.get("status") if outcome != "reused" else None
            self.children.append(
                ChildRun(
                    step=step,
                    run=item.get("run"),
                    command=item.get("command"),
                    status=status
                    if status in ("ok", "failed", "cancelled")
                    else outcome,
                    reason=reason,
                )
            )

    def join(self, parent: str | None, root: str | None, step: str | None) -> None:
        """Make this run the ``step`` of ``parent`` in the workflow ``root``
        (malformed ids are ignored)."""
        if not parent or not _RUN_ID.fullmatch(parent):
            return
        self.parent = parent
        self.root = root if root and _RUN_ID.fullmatch(root) else parent
        self.step = step or None


def lineage_args(run: RunRecord, step: str | None = None) -> list[str]:
    """The hidden global options that make a wgsextract subprocess ``step``
    of ``run``."""
    args = ["--parent-run", run.id, "--root-run", run.root or run.id]
    return args + (["--run-step", step] if step else [])


@dataclass
class Producer:
    """The run that wrote an artifact."""

    run: RunId | None
    root: RunId | None
    command: str
    step: str | None = None


@dataclass
class SourceEdge:
    """A file an artifact was made from."""

    path: str
    role: str
    fingerprint: ArtifactFingerprint = None
    producer: Annotated[
        RunId | None,
        Constraints(description="The run that wrote the source, when recorded."),
    ] = None
    lifecycle: Lifecycle | None = None
    retained: bool = True


@dataclass
class ArtifactRecord:
    """What a BAM, CRAM, VCF or BCF is, and where it came from."""

    origin: Annotated[
        Origin,
        Constraints(
            description="this_run (written by this run or one of its steps), "
            "recorded (a manifest beside it names its producer and its "
            "fingerprint still matches) or external (nothing is known)."
        ),
    ]
    lifecycle: Annotated[
        Lifecycle | None,
        Constraints(description="final, intermediate, cache or temp; null if unknown."),
    ] = None
    retained: Annotated[
        bool, Constraints(description="False when the run deleted it on purpose.")
    ] = True
    digest_kind: Annotated[
        Literal["full", "sampled"] | None,
        Constraints(description="Whether the fingerprint read the whole file."),
    ] = None
    assembly: Annotated[
        str | None,
        Constraints(description="hg19, hg38 or t2t from the header's chr1 length."),
    ] = None
    reference: Annotated[
        ArtifactFingerprint,
        Constraints(description="Fingerprint of the FASTA it was made with."),
    ] = None
    samples: list[str] = field(default_factory=list)
    index: ArtifactIndex | None = None
    producer: Producer | None = None
    sources: list[SourceEdge] = field(default_factory=list)


def _digest_kind(record: FileRecord) -> Literal["full", "sampled"] | None:
    if record.fingerprint is None or record.size_bytes is None:
        return None
    return "full" if record.size_bytes <= _FULL_DIGEST_LIMIT else "sampled"


# --- recorded producers ---------------------------------------------------


@dataclass
class _Recorded:
    producer: Producer
    lifecycle: Lifecycle | None
    reference: str | None


def recorded_producer(record: FileRecord) -> _Recorded | None:
    """The producer the manifest beside ``record`` names for this exact file."""
    from wgsextract_cli.core.results import SCHEMA_ID, manifest_path_for

    if record.fingerprint is None:
        return None
    try:
        document = json.loads(
            manifest_path_for(record.path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("schema") != SCHEMA_ID:
        return None
    run = document.get("run")
    run = run if isinstance(run, dict) else {}
    for output in document.get("outputs") or []:
        if (
            not isinstance(output, dict)
            or output.get("path") != record.path
            or output.get("fingerprint") != record.fingerprint
        ):
            continue
        artifact = output.get("artifact")
        artifact = artifact if isinstance(artifact, dict) else {}
        producer = artifact.get("producer")
        if not isinstance(producer, dict):
            producer = {
                "run": run.get("id"),
                "root": run.get("root"),
                "command": document.get("command"),
                "step": run.get("step"),
            }
        return _Recorded(
            Producer(
                run=producer.get("run"),
                root=producer.get("root"),
                command=str(producer.get("command") or ""),
                step=producer.get("step"),
            ),
            artifact.get("lifecycle"),
            artifact.get("reference"),
        )
    return None


# --- annotating a result ---------------------------------------------------


def _annotatable(record: FileRecord) -> bool:
    return record.kind in GENOMIC_KINDS and record.exists and "://" not in record.path


def input_artifact(record: FileRecord) -> ArtifactRecord:
    facts = file_facts(record.path, record.kind)
    recorded = recorded_producer(record)
    return ArtifactRecord(
        origin="recorded" if recorded else "external",
        lifecycle=recorded.lifecycle if recorded else None,
        digest_kind=_digest_kind(record),
        assembly=facts.assembly,
        reference=recorded.reference if recorded else None,
        samples=facts.samples,
        index=facts.index,
        producer=recorded.producer if recorded else None,
    )


def edge(record: FileRecord, *, retained: bool = True) -> SourceEdge:
    artifact = record.artifact
    return SourceEdge(
        path=record.path,
        role=record.role,
        fingerprint=record.fingerprint,
        producer=artifact.producer.run if artifact and artifact.producer else None,
        lifecycle=artifact.lifecycle if artifact else None,
        retained=retained and (artifact.retained if artifact else True),
    )


def annotate(
    result: CommandResult,
    *,
    lifecycles: Mapping[str, Lifecycle],
    sources: Mapping[str, Sequence[str]],
    carried: Mapping[str, ArtifactRecord],
    discarded: Sequence[FileRecord],
) -> None:
    """Fill in the artifact records of ``result``'s BAM/CRAM/VCF/BCF files.

    ``lifecycles`` and ``sources`` are what the command declared per output
    path (default: final, made from every genomic input); ``carried`` are a
    step's own records, kept as that step wrote them; ``discarded`` are files
    the run made and deleted, which stay attributable as sources."""
    for record in result.inputs:
        if _annotatable(record):
            record.artifact = input_artifact(record)
    by_path = {r.path: r for r in [*result.inputs, *discarded] if r.artifact}
    default = [edge(r) for r in result.inputs if r.artifact]
    reference = result.provenance.reference
    producer = Producer(
        run=result.run.id,
        root=result.run.root,
        command=result.command,
        step=result.run.step,
    )
    for record in result.outputs:
        if not _annotatable(record):
            continue
        if record.path in carried:
            # As the step described it, with its index as it is now.
            record.artifact = carried[record.path]
            record.artifact.index = index_state(
                record.path, record.kind, read_header(record.path, record.kind)
            )
            continue
        facts = file_facts(record.path, record.kind)
        declared = sources.get(record.path)
        record.artifact = ArtifactRecord(
            origin="this_run",
            lifecycle=lifecycles.get(record.path, "final"),
            digest_kind=_digest_kind(record),
            assembly=facts.assembly,
            reference=reference.fingerprint if reference else None,
            samples=facts.samples,
            index=facts.index,
            producer=producer,
            sources=default
            if declared is None
            else [edge(by_path[p]) for p in declared if p in by_path],
        )


class Lineage:
    """What a command declared about its outputs' lineage, until the
    result is finalised and ``annotate`` describes its artifacts."""

    def __init__(self) -> None:
        self.lifecycles: dict[str, Lifecycle] = {}
        self.sources: dict[str, list[str]] = {}
        self.carried: dict[str, ArtifactRecord] = {}
        self.discarded: list[FileRecord] = []

    def declare(
        self,
        path: str,
        lifecycle: Lifecycle | None,
        sources: Sequence[str | os.PathLike[str]] | None,
        artifact: ArtifactRecord | None,
    ) -> None:
        if lifecycle is not None:
            self.lifecycles[path] = lifecycle
        if sources is not None:
            self.sources[path] = [str(Path(s).expanduser().absolute()) for s in sources]
        if artifact is not None:
            self.carried[path] = artifact

    def discard(
        self, records: Sequence[FileRecord], run: RunRecord, command: str
    ) -> None:
        """Keep ``records``, whose files are about to be deleted, as this
        run's temporary files."""
        for record in records:
            record.refresh()
            record.artifact = ArtifactRecord(
                origin="this_run",
                lifecycle="temp",
                retained=False,
                digest_kind=_digest_kind(record),
                producer=Producer(run.id, run.root, command, run.step),
            )
            self.discarded.append(record)

    def annotate(self, result: CommandResult) -> None:
        try:
            annotate(
                result,
                lifecycles=self.lifecycles,
                sources=self.sources,
                carried=self.carried,
                discarded=self.discarded,
            )
        except Exception:  # lineage must never fail a run
            logging.warning("Could not describe the run's artifacts.", exc_info=True)


def artifact_from_dict(value: object) -> ArtifactRecord | None:
    """An artifact record read back from a result document or step journal."""
    if not isinstance(value, dict):
        return None
    try:
        index = value.get("index")
        producer = value.get("producer")
        return ArtifactRecord(
            origin=value["origin"],
            lifecycle=value.get("lifecycle"),
            retained=bool(value.get("retained", True)),
            digest_kind=value.get("digest_kind"),
            assembly=value.get("assembly"),
            reference=value.get("reference"),
            samples=[str(s) for s in value.get("samples") or []],
            index=ArtifactIndex(**index) if isinstance(index, dict) else None,
            producer=Producer(**producer) if isinstance(producer, dict) else None,
            sources=[SourceEdge(**e) for e in value.get("sources") or []],
        )
    except (KeyError, TypeError):
        return None
