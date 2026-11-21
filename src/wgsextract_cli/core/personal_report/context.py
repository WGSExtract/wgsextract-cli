"""Personal-report context support."""

from __future__ import annotations

import csv
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wgsextract_cli.core import (
    contamination,
    read_scope,
    variant_confidence,
)
from wgsextract_cli.core.multiqc import find_documents
from wgsextract_cli.core.report_documents import (
    ReportAttemptDocument,
    ReportEvidenceDocument,
    ReportFactDocument,
    ReportFindingDocument,
    ReportFindingSection,
    ReportLinkDocument,
    ReportMissingDocument,
    ReportSectionDocument,
    ReportSourceDocument,
    ReportVariantDocument,
    ReportWarningDocument,
)
from wgsextract_cli.core.sample_workspace import SampleWorkspace
from wgsextract_cli.core.utils import UsageError

from .rendering import (
    NEWER_ATTEMPT_FAILED,
    NOT_DIAGNOSIS,
    _number,
)

COMMAND = "report"
# Relatedness compares people; reading both inputs does not link their samples.
_IGNORED_COMMANDS = (COMMAND, "qc relate")
_ENDED = {"failed": "failed", "cancelled": "was cancelled"}
NOT_ASSESSED = "not_assessed"
CONFIDENCE = (*variant_confidence.LABELS, NOT_ASSESSED)
# Haplogroup callers' own 0-1 quality scores (Yleaf QC-score, Haplogrep quality).
HIGH_QUALITY, LOW_QUALITY = 0.9, 0.7
REASONS = {
    **variant_confidence.REASONS,
    "low_caller_quality": f"the caller's quality score is below {LOW_QUALITY}",
    "moderate_caller_quality": f"the caller's quality score is below {HIGH_QUALITY}",
    "quality_unknown": "the caller reported no quality score",
    "low_coverage": "the run warned that coverage is too low for reliable calls",
    "caller_filter": "the caller flagged the call (its filter did not pass)",
    "no_call": "the caller made no call",
    "genotype_unresolved": "the caller's variants fit more than one diplotype, "
    "so it picked one; no outside call was exported",
    "call_info_unknown": "the run predates recording whether the call was a "
    "unique match; rerun it",
    "evidence_mismatch": "the evidence file no longer matches the run's result",
    "no_labelled_calls": "the gene has no labelled variant calls; a reference "
    "(*1) allele rests on coverage alone",
    "uneven_coverage": "coverage is uneven, so copy-number calls may be unreliable",
    "status_uncertain": "the caller couldn't settle the carrier/affected status",
    "aligner_not_calibrated": "the input's aligner isn't one the caller is "
    "calibrated on (bwa-like)",
    "below_target_depth": "median depth is below the ~30x the caller is calibrated on",
}
SECTIONS = {
    "overview": "Sample overview",
    "lineage": "Lineage",
    "ancestry": "Ancestry proportions",
    "pharmacogenomics": "Pharmacogenomics",
    "carrier_screening": "Carrier screening by copy number",
    "clinvar": "ClinVar pathogenic / likely pathogenic in {gene_list} genes",
    "acmg_research": "Automated ACMG classes in {gene_list} genes (research only)",
    "structural_variants": "Rare structural variants",
    "microarray": "Microarray export",
}
_ALIGNMENT_SUFFIXES = (".bam", ".cram")
_VCF_SUFFIXES = (".vcf.gz", ".vcf.bgz", ".vcf", ".bcf")
# Manifest input roles naming the sample's own files
SAMPLE_ROLES = ("input", "vcf_input", "proband")
CONTAMINATION_LABEL = "Contamination (FREEMIX)"


# --- result manifests --------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """A result manifest the report reads, and where it was found."""

    manifest: Path
    document: Mapping[str, Any]

    @property
    def command(self) -> str:
        return str(self.document.get("command") or "")

    @property
    def status(self) -> str:
        return str(self.document.get("status") or "")

    @property
    def finished(self) -> str:
        provenance = self.document.get("provenance") or {}
        return str(provenance.get("finished") or provenance.get("started") or "")

    @property
    def metrics(self) -> Mapping[str, Any]:
        return self.document.get("metrics") or {}

    @property
    def details(self) -> Mapping[str, Any]:
        details = self.document.get("details")
        return details if isinstance(details, Mapping) else {}

    def records(self, kind: str) -> list[Mapping[str, Any]]:
        return [r for r in self.document.get(kind) or [] if isinstance(r, Mapping)]

    def output(self, *roles: str) -> Path | None:
        """The first existing output with one of ``roles``, in ``roles`` order,
        that this run may read (see :mod:`read_scope`)."""
        for role in roles:
            for record in self.records("outputs"):
                if record.get("role") == role and record.get("path"):
                    path = self.locate(str(record["path"]))
                    if read_scope.may_follow(path) and path.exists():
                        return path
        return None

    def files(self, kind: str) -> list[Path]:
        return [
            self.locate(str(r["path"])) for r in self.records(kind) if r.get("path")
        ]

    def locate(self, recorded: str) -> Path:
        """A path the manifest recorded; when the folder has been moved or
        copied since, the same file relative to the manifest's new place
        (if it is there), so a copy reports on its own files."""
        path = Path(recorded)
        original = self.document.get("manifest")
        if isinstance(original, str):
            try:
                relative = path.relative_to(Path(original).parent)
            except ValueError:
                return path
            moved = self.manifest.parent / relative
            if read_scope.may_follow(moved) and moved.exists():
                return moved
        return path

    @property
    def recorded_folder(self) -> str | None:
        """The folder the manifest was written in, as it recorded it."""
        original = self.document.get("manifest")
        return str(Path(original).parent) if isinstance(original, str) else None

    def sample_files(self) -> list[Path]:
        """The sample files the run read (not references or resources)."""
        return [
            self.locate(str(r["path"]))
            for r in self.records("inputs")
            if r.get("role") in SAMPLE_ROLES and r.get("path")
        ]

    def warnings(self) -> list[ReportWarningDocument]:
        return [
            ReportWarningDocument(
                code=str(w.get("code", "")), message=str(w.get("message", ""))
            )
            for w in self.records("warnings")
        ]


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _inside(path: Path, folder: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(folder))
    except ValueError:
        return False
    return True


def collect_sources(folder: Path, extra: Iterable[Path] = ()) -> list[Source]:
    """The result manifests in ``folder`` (searched recursively), plus those in
    the ``extra`` folders that belong to this sample: whose inputs are files in
    ``folder`` or outputs of a manifest already kept."""
    found: dict[str, Source] = {}
    for path, document in find_documents(folder):
        if document.get("command") not in _IGNORED_COMMANDS:
            found[_key(path)] = Source(Path(os.path.abspath(path)), document)
    candidates: dict[str, Source] = {}
    for directory in extra:
        for path, document in find_documents(directory):
            key = _key(path)
            if document.get("command") not in _IGNORED_COMMANDS and key not in found:
                candidates[key] = Source(Path(os.path.abspath(path)), document)
    produced = {_key(p) for source in found.values() for p in source.files("outputs")}
    while candidates:
        kept = [
            key
            for key, source in candidates.items()
            if any(
                _inside(p, folder) or _key(p) in produced
                for p in source.files("inputs")
            )
        ]
        if not kept:
            break
        for key in kept:
            source = found[key] = candidates.pop(key)
            produced.update(_key(p) for p in source.files("outputs"))
    return sorted(found.values(), key=lambda s: (s.finished, str(s.manifest)))


def _files(source: Source) -> set[str]:
    outside_calls = (
        source.locate(str(record["path"]))
        for record in source.records("inputs")
        if record.get("role") == "pharmcat_outside_call" and record.get("path")
    )
    return {
        _key(p)
        for p in [*source.sample_files(), *source.files("outputs"), *outside_calls]
    }


def sample_groups(sources: Sequence[Source]) -> list[list[Source]]:
    """``sources`` grouped by sample: runs are linked when one read a file
    another read or wrote."""
    groups: list[tuple[set[str], list[Source]]] = []
    for source in sources:
        files = _files(source)
        linked = [g for g in groups if g[0] & files]
        merged: tuple[set[str], list[Source]] = (files, [source])
        for group in linked:
            groups.remove(group)
            merged[0].update(group[0])
            merged[1].extend(group[1])
        groups.append(merged)
    return [members for _, members in groups]


def _roots(group: Sequence[Source]) -> list[Path]:
    """The group's sample files no run in it produced: what it started from."""
    produced = {_key(p) for s in group for p in s.files("outputs")}
    roots = {_key(p): p for s in group for p in s.sample_files()}
    return [p for key, p in roots.items() if key not in produced]


def _selection_paths(group: Sequence[Source]) -> list[Path]:
    # An align run can produce the first selectable file from FASTQs that
    # aren't report inputs. Its alignment still identifies a sample group.
    return _roots(group) or list(
        {
            path
            for source in group
            for path in source.files("outputs")
            if path.name.lower().endswith(_ALIGNMENT_SUFFIXES + _VCF_SUFFIXES)
        }
    )


def select_sample(
    sources: Sequence[Source], input_file: Path | None, *, workspace: bool
) -> list[Source]:
    """The runs on this sample. Runs on ``input_file`` and on files made from
    it (or that made it); in a sample workspace, which holds one person, also
    runs on files made from no other alignment (a VCF from elsewhere). With
    no ``input_file`` outside a workspace, every run unless they started from
    different samples' files, which is an error."""
    groups = sample_groups(sources)
    if input_file is not None:
        key = _key(input_file)
        kept = [
            g
            for g in groups
            if any(key in _files(s) for s in g)
            or (
                workspace
                and not any(
                    p.name.lower().endswith(_ALIGNMENT_SUFFIXES)
                    for p in _selection_paths(g)
                )
            )
        ]
        return sorted(
            (s for g in kept for s in g), key=lambda s: (s.finished, str(s.manifest))
        )
    if not workspace:
        choices = [
            group_paths for group in groups if (group_paths := _selection_paths(group))
        ]
        if len(choices) > 1:
            labels = sorted(str(path) for choice in choices for path in choice)
            raise UsageError(
                "The folder has results of several samples ("
                + ", ".join(labels)
                + "); choose one with --input."
            )
    return list(sources)


def sample_sources(
    folder: Path, input_file: Path | None = None, results: Iterable[Path] = ()
) -> list[Source]:
    """The runs of the sample in ``folder`` (see :func:`select_sample`)."""
    workspace = SampleWorkspace(folder)
    return select_sample(
        collect_sources(folder, results),
        input_file,
        workspace=workspace.is_genome_folder,
    )


def latest(sources: Sequence[Source], match: Callable[[Source], bool]) -> Source | None:
    """The newest successful matching run, else the newest failed one. A
    newer failed attempt it passes over is reported by :meth:`_Context.latest`."""
    hits = [s for s in sources if match(s)]
    ok = [s for s in hits if s.status == "ok"]
    return max(ok or hits, key=lambda s: s.finished, default=None)


def _command(name: str) -> Callable[[Source], bool]:
    return lambda source: source.command == name


def _command_or_role(command: str, *roles: str) -> Callable[[Source], bool]:
    """Runs of ``command`` (failed ones may have no outputs), or any run that
    wrote one of ``roles``."""
    return lambda source: (
        source.command == command
        or any(record.get("role") in roles for record in source.records("outputs"))
    )


# --- building the report -----------------------------------------------------


@dataclass
class _Context:
    folder: Path
    sources: list[Source]
    workspace: dict[str, Any]
    alignment: str | None
    vcf: str | None
    gene_list: str | Path | None = None
    # A selected run's manifest -> the newer attempt that failed (or was
    # cancelled) after it.
    superseded: dict[str, Source] = field(default_factory=dict)

    def latest(self, match: Callable[[Source], bool]) -> Source | None:
        """:func:`latest` of this sample's runs, remembering the newest
        attempt when it ended unsuccessfully after the successful run chosen."""
        hits = [s for s in self.sources if match(s)]
        chosen = latest(hits, lambda _: True)
        newest = max(hits, key=lambda s: s.finished, default=None)
        if (
            chosen is not None
            and newest is not None
            and chosen.status == "ok"
            and newest.status != "ok"
            and newest.finished > chosen.finished
        ):
            self.superseded[_key(chosen.manifest)] = newest
        return chosen

    def rel(self, path: Path | str | None) -> str | None:
        """``path`` relative to the sample folder ('/'-separated), or absolute
        when it is on another drive (Windows) and so has no relative path."""
        if path is None:
            return None
        absolute = os.path.abspath(path)
        try:
            relative = os.path.relpath(absolute, self.folder)
        except ValueError:
            return Path(absolute).as_posix()
        return Path(relative).as_posix()

    def fact(self, name: str) -> dict[str, Any] | None:
        effective = self.workspace.get("effective") or {}
        if name in effective:
            return dict(effective[name])
        shared = (self.workspace.get("overrides") or {}).get(name)
        if shared is not None:
            return {"name": name, "value": shared, "source": "override"}
        for entry in (self.workspace.get("files") or {}).values():
            if entry.get("status") == "current" and name in entry.get("facts", {}):
                return dict(entry["facts"][name])
        return None

    def build_of(self, file: Path | None) -> str | None:
        """``file``'s build as the workspace recorded it, while still current."""
        key = self.rel(file) if file else None
        entry = (self.workspace.get("files") or {}).get(key or "") or {}
        fact = entry.get("facts", {}).get("build")
        return str(fact["value"]) if fact and entry.get("status") == "current" else None

    def command_for(self, command: str, input_file: str | None, what: str) -> str:
        target = input_file or f"YOUR.{what}"
        flag = "--vcf-input" if command.startswith("vcf ") and what != "cram" else "-i"
        return f"wgsextract {command} {flag} {_quote(target)}"


def _quote(text: str) -> str:
    return f'"{text}"' if any(c.isspace() for c in text) else text


def _finding(
    section: ReportFindingSection,
    label: str,
    value: str,
    *,
    confidence: str,
    reasons: Sequence[str],
    basis: str,
    evidence: str | None,
    region: str | None,
    manifest: str | None,
    detail: str | None = None,
    gene: str | None = None,
    note: str | None = None,
    variant: ReportVariantDocument | None = None,
    links: Sequence[tuple[str, str]] = (),
    disclaimer: str = NOT_DIAGNOSIS,
) -> ReportFindingDocument:
    return ReportFindingDocument(
        section=section,
        label=label,
        value=value,
        detail=detail,
        gene=gene,
        note=note,
        confidence=confidence,
        confidence_reasons=list(reasons),
        confidence_basis=basis,
        evidence=ReportEvidenceDocument(
            file=evidence, region=region, manifest=manifest
        ),
        variant=variant.copy() if variant else None,
        links=[ReportLinkDocument(label=label, url=url) for label, url in links],
        disclaimer=disclaimer,
    )


def _section(
    name: str, sources: Sequence[Source | None], ctx: _Context
) -> ReportSectionDocument:
    used = [s for s in sources if s is not None]
    failed = [s for s in used if s.status != "ok"]
    section = ReportSectionDocument(
        title=SECTIONS[name],
        status="ok" if len(failed) < len(used) else "failed" if used else "not_run",
        summary="",
        findings=[],
        missing=[],
        warnings=[w for s in used for w in s.warnings()],
        sources=[],
    )
    for s in used:
        newer = ctx.superseded.get(_key(s.manifest))
        section["sources"].append(
            ReportSourceDocument(
                command=s.command,
                status=s.status,
                finished=s.finished or None,
                manifest=ctx.rel(s.manifest),
                latest_attempt=None
                if newer is None
                else ReportAttemptDocument(
                    command=newer.command,
                    status=newer.status,
                    finished=newer.finished or None,
                    manifest=ctx.rel(newer.manifest),
                ),
            )
        )
        if newer is not None:
            error = (newer.document.get("error") or {}).get("message")
            section["warnings"].append(
                ReportWarningDocument(
                    code=NEWER_ATTEMPT_FAILED,
                    message=f"The newest {newer.command} run"
                    f" ({newer.finished or 'time not recorded'})"
                    f" {_ENDED.get(newer.status, f'ended {newer.status}')}"
                    + (f": {error}" if error else "")
                    + f". Shown is the older {s.status} run"
                    f" ({s.finished or 'time not recorded'}), which may be out"
                    " of date.",
                )
            )
    errors = [
        f"{s.command}: {(s.document.get('error') or {}).get('message', s.status)}"
        for s in failed
    ]
    if errors:
        section["error"] = "; ".join(errors)
    return section


def _missing(section: ReportSectionDocument, what: str, command: str) -> None:
    section["missing"].append(ReportMissingDocument(what=what, command=command))


def _quality_label(
    quality: object, warnings: Sequence[Mapping[str, object]]
) -> tuple[str, list[str]]:
    if any(w["code"] == "low_coverage" for w in warnings):
        return variant_confidence.LOW, ["low_coverage"]
    reasons = (
        ["sample_contaminated"]
        if any(w["code"] == contamination.WARNING for w in warnings)
        else []
    )
    if not isinstance(quality, (int, float)) or isinstance(quality, bool):
        return variant_confidence.MODERATE, ["quality_unknown", *reasons]
    if quality < LOW_QUALITY:
        return variant_confidence.LOW, ["low_caller_quality", *reasons]
    if quality < HIGH_QUALITY:
        return variant_confidence.MODERATE, ["moderate_caller_quality", *reasons]
    if reasons:
        return variant_confidence.MODERATE, reasons
    return variant_confidence.HIGH, []


def _contaminated(ctx: _Context, inputs: Iterable[Path]) -> float | None:
    """The selected sample's FREEMIX when it is at or above
    ``qc_max_contamination``: the estimate the overview shows, else one
    recorded for a run's own ``inputs``."""
    run = ctx.latest(_command("qc contamination"))
    estimate = _contamination_fact(ctx, run if run and run.status == "ok" else None)
    value = estimate["value"] if estimate else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        threshold = contamination.max_contamination()
        return float(value) if threshold and value >= threshold else None
    for file in inputs:
        if (found := contamination.contaminated(file)) is not None:
            return found
    return None


def _contamination_fact(ctx: _Context, run: Source | None) -> ReportFactDocument | None:
    """The selected alignment's contamination estimate: from the sample
    workspace (only that file's, or the genome folder's), else from its
    newest successful `qc contamination` run."""
    path = ctx.folder / ctx.alignment if ctx.alignment else None
    fact = contamination.sample_contamination(path) if path else None
    if fact is not None:
        found = fact.to_dict()
        value = found.get("value")
        return ReportFactDocument(
            name=contamination.FACT,
            label=CONTAMINATION_LABEL,
            value=value,
            text=_fact_text(contamination.FACT, value),
            source=found.get("source"),
            method=found.get("method"),
            confidence=found.get("confidence"),
            file=found.get("file"),
            at=found.get("at"),
        )
    value = run.metrics.get("freemix") if run is not None else None
    if run is None or not isinstance(value, (int, float)):
        return None
    return ReportFactDocument(
        name=contamination.FACT,
        label=CONTAMINATION_LABEL,
        value=value,
        text=_fact_text(contamination.FACT, value),
        source="qc contamination",
        method="qc contamination metrics (no sample workspace)",
        confidence=run.metrics.get("freemix_confidence"),
        file=next(iter(map(ctx.rel, run.files("inputs"))), None),
        at=run.finished or None,
    )


def _fact_text(name: str, value: object) -> str:
    if isinstance(value, Mapping):
        return ", ".join(f"{k} {_number(v, 'x')}" for k, v in value.items())
    if name == "mean_coverage":
        return _number(value, "x")
    if name == "read_length":
        return _number(value, " bp")
    if name == "contamination" and isinstance(value, (int, float)):
        return f"{value:.1%}"
    return str(value)


def _labelled_calls(
    source: Source, role: str, table_role: str
) -> tuple[list[dict[str, Any]], bool]:
    """A run's labelled calls: the confidence TSV it wrote (every call), else
    the findings listed in its manifest (up to 1000), and whether that is
    every call."""
    table = source.output(table_role)
    if table is not None:
        try:
            with open(table, encoding="utf-8", newline="") as stream:
                lines = (line for line in stream if not line.startswith("#"))
                rows = list(csv.DictReader(lines, delimiter="\t"))
            for row in rows:
                row["reasons"] = [r for r in (row.get("reasons") or "").split("|") if r]
            return rows, True
        except (OSError, csv.Error):
            pass
    for entry in source.details.get("confidence") or []:
        if isinstance(entry, Mapping) and entry.get("role") == role:
            findings = [dict(f) for f in entry.get("findings") or []]
            return findings, not entry.get("findings_truncated")
    return [], True


def _worst(labels: Iterable[str]) -> str:
    order = {label: i for i, label in enumerate(variant_confidence.LABELS)}
    return max(labels, key=lambda label: order.get(label, -1))


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _choose_inputs(
    folder: Path,
    input_file: Path | None,
    sources: Sequence[Source],
    workspace: SampleWorkspace,
) -> tuple[str | None, str | None]:
    """The sample's alignment and VCF, relative to the folder, for hints and
    workspace facts: the given input, else what the runs read, else what the
    workspace recorded."""
    alignment = vcf = None
    candidates: list[Path] = []
    if input_file is not None:
        candidates.append(input_file)
    for source in reversed(sources):
        candidates.extend(
            source.locate(str(r["path"]))
            for r in source.records("inputs")
            if r.get("role") in SAMPLE_ROLES and r.get("path")
        )
    if input_file is None:
        keys = (workspace.describe().get("files") or {}).keys()
        candidates.extend(folder / key for key in keys)
    for path in candidates:
        if not (
            _inside(path, folder) and read_scope.may_follow(path) and path.is_file()
        ):
            continue
        name = path.name.lower()
        if alignment is None and name.endswith(_ALIGNMENT_SUFFIXES):
            alignment = Path(os.path.relpath(path, folder)).as_posix()
        elif vcf is None and name.endswith(_VCF_SUFFIXES):
            vcf = Path(os.path.relpath(path, folder)).as_posix()
    return alignment, vcf
