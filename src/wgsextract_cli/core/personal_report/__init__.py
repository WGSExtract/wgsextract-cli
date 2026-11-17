"""``report``: a sample's results as one local HTML, Markdown and JSON report.

The report is assembled from what earlier commands left in the sample folder:
their result manifests (``*.manifest.json``, docs/result-manifest.md) and the
sample workspace (``wgsextract.sample.toml``, docs/sample-workspace.md). It
runs no analysis. Its only network use is downloading a built-in gene list
(core/gene_lists.py) into the reference library the first time ClinVar calls
need one. A section whose command has not run says so and gives the command
that produces it.

Every finding carries a confidence label (``high``, ``moderate``, ``low``, or
``not_assessed`` when nothing supports a label), its reasons, the evidence
file (relative to the sample folder) with the region, and the "technical
result, not diagnosis" disclaimer. The JSON document follows
``assets/schemas/wgsextract.report.1.schema.json``; docs/personal-report.md
describes it.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path, PureWindowsPath
from typing import Any

from wgsextract_cli import __version__
from wgsextract_cli.core import (
    acmg,
    clinical_evidence,
    contamination,
    gene_lists,
    read_scope,
    variant_confidence,
)
from wgsextract_cli.core.builds import (
    build_from_chr1_length,
    canonical_build,
    is_hg37_build,
    is_hg38_build,
)
from wgsextract_cli.core.multiqc import find_documents
from wgsextract_cli.core.reference_assets import input_stem
from wgsextract_cli.core.reference_models import sequence_lengths
from wgsextract_cli.core.report_documents import (
    ReportAttemptDocument,
    ReportLinkDocument,
    ReportMissingDocument,
    ReportSourceDocument,
    ReportWarningDocument,
)
from wgsextract_cli.core.sample_workspace import WORKSPACE_FILE, SampleWorkspace
from wgsextract_cli.core.table_writer import Column, Value
from wgsextract_cli.core.utils import UsageError

from . import acmg_calls
from .rendering import (
    NEWER_ATTEMPT_FAILED as NEWER_ATTEMPT_FAILED,
)
from .rendering import (
    NOT_DIAGNOSIS as NOT_DIAGNOSIS,
)
from .rendering import (
    RESEARCH_ONLY as RESEARCH_ONLY,
)
from .rendering import (
    _number,
)
from .rendering import (
    link_base as link_base,
)
from .rendering import (
    render_html as render_html,
)
from .rendering import (
    render_markdown as render_markdown,
)

COMMAND = "report"
SCHEMA_ID = "wgsextract.report/1"
SCHEMA_RESOURCE = "assets/schemas/wgsextract.report.1.schema.json"
GUIDE = "docs/personal-report.md"
_ENDED = {"failed": "failed", "cancelled": "was cancelled"}
DISCLAIMER = (
    "This report summarises technical results of software run on this "
    "computer; it is not a diagnosis or medical advice. "
    + variant_confidence.DISCLAIMER
)
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
_OVERVIEW_FACTS = (
    ("sex", "Sex"),
    ("platform", "Platform"),
    ("build", "Build"),
    ("mean_coverage", "Mean coverage"),
    ("target_depths", "Depth by target"),
    ("read_length", "Read length"),
)
CONTAMINATION_LABEL = "Contamination (FREEMIX)"
_INFO_FACTS = (
    ("sex", "sex"),
    ("mean_coverage", "ard_mapped"),
    ("read_length", "avg_read_len"),
)
_QC_METRICS = (
    ("sequencer", "Sequencer", ""),
    ("ard_mapped", "Mean depth (mapped)", "x"),
    ("reads_mapped_pct", "Reads mapped", "%"),
    ("duplicate_pct", "Duplicates", "%"),
    ("avg_read_len", "Mean read length", " bp"),
    ("avg_insert_size", "Mean insert size", " bp"),
    ("size_gb", "File size", " GB"),
)
_MICROARRAY_ROLES = ("combined_kit", "vendor_kit", "aadr")

# The findings table (``--format``): one row per finding, for agents and
# spreadsheets. The JSON document holds the same findings with their links.
TEXT, INTEGER, REAL = "TEXT", "INTEGER", "REAL"
FINDING_COLUMNS: tuple[Column, ...] = (
    Column(
        "section",
        TEXT,
        "Report section: lineage, pharmacogenomics, carrier_screening, clinvar, "
        "acmg_research or structural_variants.",
    ),
    Column("label", TEXT, "What the finding is, e.g. 'BRCA2 Pathogenic'."),
    Column("value", TEXT, "The result: haplogroup, diplotype or ClinVar class."),
    Column("detail", TEXT, "Phenotype, condition or caller detail."),
    Column("gene", TEXT, "Gene symbol, when the finding is in a gene."),
    Column("chrom", TEXT, "Chromosome of a variant finding."),
    Column("pos", INTEGER, "1-based position of a variant finding."),
    Column("ref", TEXT, "Reference allele."),
    Column("alt", TEXT, "Alternate allele."),
    Column("genotype", TEXT, "The sample's GT."),
    Column("zygosity", TEXT, "het, hom_alt or hemizygous."),
    Column("depth", INTEGER, "Read depth at the call."),
    Column("genotype_quality", INTEGER, "Genotype quality (GQ)."),
    Column("alt_fraction", REAL, "Fraction of reads carrying the ALT."),
    Column(
        "confidence", TEXT, "Confidence label: high, moderate, low or not_assessed."
    ),
    Column("confidence_reasons", TEXT, "Reason codes, '|'-separated."),
    Column("evidence_file", TEXT, "Evidence file, relative to the sample folder."),
    Column("evidence_region", TEXT, "Region of the evidence (chrom:pos, gene)."),
    Column(
        "disclaimer",
        TEXT,
        "Technical result, not diagnosis (research use only for acmg_research).",
    ),
    Column("clinvar_id", TEXT, "ClinVar resource record identity."),
    Column("clinvar_review_status", TEXT, "CLNREVSTAT assertion review status."),
    Column(
        "clinvar_review_stars",
        INTEGER,
        "ClinVar review stars (0-4), not sequencing confidence.",
    ),
    Column("clinvar_review_tier", TEXT, "ClinVar assertion support tier."),
    Column(
        "clinvar_conflict_evidence",
        TEXT,
        "CLNSIGCONF conflicting submitter classifications.",
    ),
    Column(
        "original_record", TEXT, "Original allele and sample fields (URL-encoded JSON)."
    ),
    Column(
        "mnv_components", TEXT, "Phase-supported MNV components (URL-encoded JSON)."
    ),
    Column(
        "mnv_residual_copies",
        INTEGER,
        "Pathogenic component copies outside a composed MNV; the original genotype is retained.",
    ),
)


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
        if document.get("command") != COMMAND:
            found[_key(path)] = Source(Path(os.path.abspath(path)), document)
    candidates: dict[str, Source] = {}
    for directory in extra:
        for path, document in find_documents(directory):
            key = _key(path)
            if document.get("command") != COMMAND and key not in found:
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
    return {_key(p) for p in [*source.sample_files(), *source.files("outputs")]}


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
                    p.name.lower().endswith(_ALIGNMENT_SUFFIXES) for p in _roots(g)
                )
            )
        ]
        return sorted(
            (s for g in kept for s in g), key=lambda s: (s.finished, str(s.manifest))
        )
    if not workspace:
        stems = sorted({input_stem(p) for g in groups for p in _roots(g)})
        if len(stems) > 1:
            raise UsageError(
                "The folder has results of several samples ("
                + ", ".join(stems)
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
        workspace=workspace.path.exists() or workspace.is_genome_folder,
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


# --- the gene list's build ---------------------------------------------------


def _gene_build(build: str | None) -> str | None:
    if not build:
        return None
    if is_hg38_build(build):
        return "hg38"
    if is_hg37_build(build):
        return "hg19"
    return None


def _vcf_build(path: Path) -> str | None:
    """The build a VCF's ##contig lines name, read locally from its header."""
    lines = []
    try:
        opener = gzip.open if path.name.endswith(("gz", "bgz")) else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not line.startswith("##"):
                    break
                lines.append(line)
    except (OSError, EOFError):
        return None
    return canonical_build(build_from_chr1_length(sequence_lengths("".join(lines))))


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
    section: str,
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
    variant: Mapping[str, Any] | None = None,
    links: Sequence[tuple[str, str]] = (),
    disclaimer: str = NOT_DIAGNOSIS,
) -> dict[str, Any]:
    return {
        "section": section,
        "label": label,
        "value": value,
        "detail": detail,
        "gene": gene,
        "note": note,
        "confidence": confidence,
        "confidence_reasons": list(reasons),
        "confidence_basis": basis,
        "evidence": {"file": evidence, "region": region, "manifest": manifest},
        "variant": dict(variant) if variant else None,
        "links": [ReportLinkDocument(label=label, url=url) for label, url in links],
        "disclaimer": disclaimer,
    }


def _section(
    name: str, sources: Sequence[Source | None], ctx: _Context
) -> dict[str, Any]:
    used = [s for s in sources if s is not None]
    failed = [s for s in used if s.status != "ok"]
    section: dict[str, Any] = {
        "title": SECTIONS[name],
        "status": "ok" if len(failed) < len(used) else "failed" if used else "not_run",
        "summary": "",
        "findings": [],
        "missing": [],
        "warnings": [w for s in used for w in s.warnings()],
        "sources": [],
    }
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


def _missing(section: dict[str, Any], what: str, command: str) -> None:
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


def _overview(ctx: _Context) -> dict[str, Any]:
    info = ctx.latest(_command("info"))
    run = ctx.latest(_command("qc contamination"))
    run = run if run is not None and run.status == "ok" else None
    repeat_run = ctx.latest(_command("typing str"))
    repeat_run = (
        repeat_run if repeat_run is not None and repeat_run.status == "ok" else None
    )
    trio = ctx.latest(_command("vcf trio"))
    trio = trio if trio is not None and trio.status == "ok" else None
    section = _section("overview", [info, run, trio, repeat_run], ctx)
    facts = []
    for name, label in _OVERVIEW_FACTS:
        fact = ctx.fact(name)
        if fact is None:
            continue
        value = fact.get("value")
        facts.append(
            {
                "name": name,
                "label": label,
                "value": value,
                "text": _fact_text(name, value),
                "source": fact.get("source"),
                "method": fact.get("method"),
                "confidence": fact.get("confidence"),
                "file": fact.get("file"),
                "at": fact.get("at"),
            }
        )
    if info is not None and info.status == "ok":
        # Outside a sample workspace nothing is recorded; `info` still says.
        known = {f["name"] for f in facts}
        for name, key in _INFO_FACTS:
            value = info.metrics.get(key)
            if name in known or value in (None, "", "Unknown"):
                continue
            facts.append(
                {
                    "name": name,
                    "label": dict(_OVERVIEW_FACTS)[name],
                    "value": value,
                    "text": _fact_text(name, value),
                    "source": "info",
                    "method": "info --detailed metrics (no sample workspace)",
                    "confidence": None,
                    "file": next(iter(map(ctx.rel, info.files("inputs"))), None),
                    "at": info.finished or None,
                }
            )
    estimate = _contamination_fact(ctx, run)
    if estimate is not None:
        facts.append(estimate)
    section["facts"] = facts
    freemix = estimate["value"] if estimate else None
    threshold = contamination.max_contamination()
    warned = any(w["code"] == contamination.WARNING for w in section["warnings"])
    if (
        isinstance(freemix, (int, float))
        and threshold
        and freemix >= threshold
        and not warned
    ):
        section["warnings"].append(
            ReportWarningDocument(
                code=contamination.WARNING,
                message=f"qc contamination estimated {freemix:.1%} of the "
                "reads are another person's (FREEMIX, at or above "
                f"qc_max_contamination {threshold:.1%}): every finding below "
                f"may be affected. See {contamination.GUIDE}.",
            )
        )
    qc = []
    if info is not None and info.status == "ok":
        for key, label, unit in _QC_METRICS:
            value = info.metrics.get(key)
            if value in (None, ""):
                continue
            qc.append(
                {
                    "name": key,
                    "label": label,
                    "value": value,
                    "text": _number(value, unit),
                }
            )
    section["qc"] = qc
    if facts:
        section["status"] = "ok"
    if info is None or info.status != "ok":
        _missing(
            section,
            "QC metrics and sex (info --detailed)",
            ctx.command_for("info --detailed", ctx.alignment, "cram"),
        )
    if ctx.alignment and freemix is None:
        _missing(
            section,
            "DNA contamination check (qc contamination)",
            ctx.command_for("qc contamination", ctx.alignment, "cram"),
        )
    section["summary"] = (
        ", ".join(f"{f['label']} {f['text']}" for f in facts[:4])
        or "No facts recorded."
    )
    return section


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


def _contamination_fact(ctx: _Context, run: Source | None) -> dict[str, Any] | None:
    """The selected alignment's contamination estimate: from the sample
    workspace (only that file's, or the genome folder's), else from its
    newest successful `qc contamination` run."""
    path = ctx.folder / ctx.alignment if ctx.alignment else None
    fact = contamination.sample_contamination(path) if path else None
    if fact is not None:
        found = fact.to_dict()
        value = found.get("value")
        return {
            "name": contamination.FACT,
            "label": CONTAMINATION_LABEL,
            "value": value,
            "text": _fact_text(contamination.FACT, value),
            "source": found.get("source"),
            "method": found.get("method"),
            "confidence": found.get("confidence"),
            "file": found.get("file"),
            "at": found.get("at"),
        }
    value = run.metrics.get("freemix") if run is not None else None
    if run is None or not isinstance(value, (int, float)):
        return None
    return {
        "name": contamination.FACT,
        "label": CONTAMINATION_LABEL,
        "value": value,
        "text": _fact_text(contamination.FACT, value),
        "source": "qc contamination",
        "method": "qc contamination metrics (no sample workspace)",
        "confidence": run.metrics.get("freemix_confidence"),
        "file": next(iter(map(ctx.rel, run.files("inputs"))), None),
        "at": run.finished or None,
    }


def _fact_text(name: str, value: Any) -> str:
    if isinstance(value, Mapping):
        return ", ".join(f"{k} {_number(v, 'x')}" for k, v in value.items())
    if name == "mean_coverage":
        return _number(value, "x")
    if name == "read_length":
        return _number(value, " bp")
    if name == "contamination" and isinstance(value, (int, float)):
        return f"{value:.1%}"
    return str(value)


def _lineage(ctx: _Context) -> dict[str, Any]:
    y = ctx.latest(_command("lineage y-haplogroup"))
    mt = ctx.latest(_command("lineage mt-haplogroup"))
    section = _section("lineage", [y, mt], ctx)
    from wgsextract_cli.commands._lineage_links import tree_links

    sex = ctx.fact("sex")
    female = bool(sex and str(sex.get("value")) == "Female")
    for source, what, mitochondrial, region in (
        (y, "Y-DNA haplogroup", False, "chrY"),
        (mt, "mtDNA haplogroup", True, "chrM"),
    ):
        command = "lineage mt-haplogroup" if mitochondrial else "lineage y-haplogroup"
        if source is None or source.status != "ok":
            if not mitochondrial and female:
                continue
            _missing(section, what, ctx.command_for(command, ctx.alignment, "cram"))
            continue
        haplogroup = str(source.metrics.get("haplogroup") or "")
        if not haplogroup:
            continue
        quality_key = "haplogroup_quality" if mitochondrial else "qc_score"
        quality = source.metrics.get(quality_key)
        label, reasons = _quality_label(quality, source.warnings())
        marker = source.metrics.get("haplogroup_marker")
        detail = ", ".join(
            x
            for x in (
                f"marker {marker}" if marker else "",
                f"quality {quality:g}" if isinstance(quality, (int, float)) else "",
            )
            if x
        )
        section["findings"].append(
            _finding(
                "lineage",
                what,
                haplogroup,
                detail=detail or None,
                confidence=label,
                reasons=reasons,
                basis="Haplogrep quality" if mitochondrial else "Yleaf QC-score",
                evidence=ctx.rel(source.output("haplogroup_report")),
                region=region,
                manifest=ctx.rel(source.manifest),
                links=tree_links(haplogroup, mitochondrial=mitochondrial),
            )
        )
    summary = "; ".join(f"{f['label']} {f['value']}" for f in section["findings"])
    if female and (y is None or y.status != "ok"):
        summary = "; ".join(
            x for x in (summary, "Y-DNA: none (recorded sex Female)") if x
        )
    section["summary"] = summary
    return section


ANCESTRY_NOTE = (
    "Shares of the sample's genome best matched by reference components "
    "learned from the HGDP and 1000 Genomes samples. Each is named after the "
    "reference groups that carry it most; they are not ethnicities, and a "
    "component is shared by many present-day populations."
)


def _ancestry(ctx: _Context) -> dict[str, Any]:
    source = ctx.latest(_command("qc ancestry"))
    section = _section("ancestry", [source], ctx)
    section["note"] = ANCESTRY_NOTE
    section["components"] = []
    if source is None or source.status != "ok":
        _missing(
            section,
            "Ancestry proportions (qc ancestry)",
            ctx.command_for("qc ancestry", ctx.alignment or ctx.vcf, "cram"),
        )
        return section
    table = source.output("ancestry_table")
    try:
        with open(table or "", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
    except (OSError, csv.Error):
        rows = []
    for row in rows:
        try:
            section["components"].append(
                {
                    "k": int(row["k"]),
                    "name": row["component"],
                    "region": row["region"],
                    "proportion": float(row["proportion"]),
                    "lower": float(row["lower_95"]),
                    "upper": float(row["upper_95"]),
                    "reference_populations": row["reference_populations"],
                }
            )
        except (KeyError, ValueError):
            continue
    section["metrics"] = {
        k: source.metrics[k]
        for k in ("ancestry_sites", "ancestry_depth", "ancestry_confidence")
        if k in source.metrics
    }
    section["evidence"] = ctx.rel(table)
    top = [c for c in section["components"] if c["k"] == 7 and c["proportion"] >= 0.01]
    section["summary"] = (
        ", ".join(f"{c['name']} {c['proportion']:.0%}" for c in top[:4])
        or "No proportions recorded."
    )
    return section


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


def _pharmacogenomics(ctx: _Context) -> dict[str, Any]:
    pharmcat = ctx.latest(_command_or_role("vcf pharmcat", "pharmcat_phenotypes"))
    cyrius = ctx.latest(_command("typing cyp2d6"))
    hla = ctx.latest(_command("typing hla"))
    section = _section("pharmacogenomics", [pharmcat, cyrius, hla], ctx)
    if pharmcat is not None and pharmcat.status == "ok":
        _pharmcat_findings(section, pharmcat, ctx)
    else:
        _missing(
            section,
            "PharmCAT diplotypes and phenotypes (GRCh38 VCF)",
            ctx.command_for("vcf pharmcat", ctx.vcf, "vcf.gz"),
        )
    if cyrius is not None and cyrius.status == "ok":
        _cyrius_finding(section, cyrius, ctx)
    else:
        _missing(
            section,
            "CYP2D6 star alleles (Cyrius, ~30x WGS)",
            ctx.command_for("typing cyp2d6", ctx.alignment, "cram"),
        )
    if hla is not None and hla.status == "ok":
        _hla_findings(section, hla, ctx)
    else:
        _missing(
            section,
            "HLA-A and HLA-B alleles (T1K)",
            ctx.command_for("typing hla", ctx.alignment, "cram"),
        )
    gene_statuses: dict[str, str] = {}
    priority = {"resolved": 0, "ambiguous": 1, "no_call": 2}
    for finding in section["findings"]:
        status = (finding.get("pgx") or {}).get(
            "status",
            "no_call" if "no_call" in finding["confidence_reasons"] else "resolved",
        )
        previous = gene_statuses.get(finding["gene"], "no_call")
        gene_statuses[finding["gene"]] = min(
            (previous, status), key=priority.__getitem__
        )
    statuses: dict[str, set[str]] = {}
    for gene, status in gene_statuses.items():
        statuses.setdefault(status, set()).add(gene)
    section["summary"] = "; ".join(
        f"{len(genes)} {status.replace('_', '-')} gene(s)"
        for status, genes in sorted(statuses.items())
    )
    return section


def _pharmcat_findings(section: dict[str, Any], source: Source, ctx: _Context) -> None:
    from wgsextract_cli.commands.vcf.pharmcat import summarize

    phenotypes = source.output("pharmcat_phenotypes")
    try:
        rows = summarize(str(phenotypes)) if phenotypes else []
    except (OSError, ValueError) as e:
        section["warnings"].append(
            ReportWarningDocument(
                code="unreadable_evidence", message=f"PharmCAT phenotypes: {e}"
            )
        )
        rows = []
    calls: dict[str, list[dict[str, Any]]] = {}
    for call in _labelled_calls(source, "pgx_calls", "confidence_table")[0]:
        for gene in str(call.get("gene") or "").split(","):
            if gene:
                calls.setdefault(gene, []).append(call)
    evidence = source.output("pharmcat_report", "pharmcat_phenotypes")
    for row in rows:
        gene, diplotype, phenotype = row.gene, row.diplotype, row.phenotype
        gene_calls = calls.get(gene, [])
        if gene_calls:
            label = _worst(str(c.get("confidence")) for c in gene_calls)
            reasons = sorted(
                {
                    r
                    for c in gene_calls
                    if c.get("confidence") == label
                    for r in c["reasons"]
                }
            )
            basis = f"weakest of {len(gene_calls)} labelled call(s) in the gene"
        else:
            label, reasons = NOT_ASSESSED, ["no_labelled_calls"]
            basis = "no labelled calls in the gene"
        if row.status != "resolved":
            label = variant_confidence.LOW
            reasons = sorted({*reasons, row.status})
        finding = _finding(
            "pharmacogenomics",
            f"{gene} diplotype (PharmCAT)",
            diplotype,
            detail=phenotype if phenotype != "-" else None,
            gene=gene,
            confidence=label,
            reasons=reasons,
            basis=basis,
            evidence=ctx.rel(evidence),
            region=f"gene {gene}",
            manifest=ctx.rel(source.manifest),
        )
        finding["pgx"] = {
            "status": row.status,
            "alternatives": row.alternatives,
            "phenotype_consistent": row.phenotype_consistent,
            "source": "PharmCAT",
        }
        section["findings"].append(finding)


def _cyrius_finding(section: dict[str, Any], source: Source, ctx: _Context) -> None:
    metrics = source.metrics
    genotype = metrics.get("cyp2d6_genotype")
    status = metrics.get("cyp2d6_filter")
    call_status = metrics.get("cyp2d6_call_status")
    depth = metrics.get("median_depth")
    thresholds = variant_confidence.thresholds_from()
    if not genotype:
        label, reasons = variant_confidence.LOW, ["no_call"]
    elif status != "PASS":
        label, reasons = variant_confidence.LOW, ["caller_filter"]
    elif call_status == "unresolved":
        label, reasons = variant_confidence.LOW, ["genotype_unresolved"]
    elif not isinstance(depth, (int, float)):
        label, reasons = variant_confidence.MODERATE, ["depth_unknown"]
    elif depth < thresholds.min_depth:
        label, reasons = variant_confidence.LOW, ["low_depth"]
    elif depth < thresholds.high_depth:
        label, reasons = variant_confidence.MODERATE, ["moderate_depth"]
    else:
        label, reasons = variant_confidence.HIGH, []
    if genotype and call_status is None and label == variant_confidence.HIGH:
        # Runs from before Cyrius's Call_info was recorded can't show the call
        # was a unique match.
        label, reasons = variant_confidence.MODERATE, ["call_info_unknown"]
    # Cyrius's Total_CN, recorded as cyp2d6_copy_number by older runs, counts
    # CYP2D6 and CYP2D7 together.
    copies = metrics.get("cyp2d6_cyp2d7_copy_number", metrics.get("cyp2d6_copy_number"))
    candidates = metrics.get("cyp2d6_raw_star_alleles")
    detail = ", ".join(
        x
        for x in (
            f"filter {status}" if status else "",
            f"Cyrius call {metrics['cyp2d6_call_info']}"
            if metrics.get("cyp2d6_call_info")
            else "",
            f"candidates {candidates}" if candidates else "",
            f"CNV group {metrics['cyp2d6_cnv_group']}"
            if metrics.get("cyp2d6_cnv_group")
            else "",
            f"CYP2D6+CYP2D7 copy number {copies}" if copies is not None else "",
            f"median depth {_number(depth, 'x')}" if depth is not None else "",
        )
        if x
    )
    if not genotype:
        value = "no call"
    elif call_status == "unresolved" or (call_status is None and status != "PASS"):
        value = f"unresolved (Cyrius: {genotype})"
    else:
        value = str(genotype)
    section["findings"].append(
        _finding(
            "pharmacogenomics",
            "CYP2D6 diplotype (Cyrius)",
            value,
            detail=detail or None,
            gene="CYP2D6",
            confidence=label,
            reasons=reasons,
            basis="Cyrius filter, call uniqueness and median depth vs. confidence "
            "thresholds",
            evidence=ctx.rel(source.output("cyp2d6_calls", "cyrius_report")),
            region="gene CYP2D6",
            manifest=ctx.rel(source.manifest),
        )
    )


def _carrier_screening(ctx: _Context) -> dict[str, Any]:
    smn = ctx.latest(_command("typing smn"))
    section = _section("carrier_screening", [smn], ctx)
    if smn is not None and smn.status == "ok":
        _smn_finding(section, smn, ctx)
    else:
        _missing(
            section,
            "SMN1/SMN2 copy number for SMA carrier status (SMNCopyNumberCaller, "
            "~30x short-read WGS)",
            ctx.command_for("typing smn", ctx.alignment, "cram"),
        )
    if section["findings"]:
        section["summary"] = f"{section['findings'][0]['value']}."
    return section


def _smn_finding(section: dict[str, Any], source: Source, ctx: _Context) -> None:
    from wgsextract_cli.commands.genotyping.smn import (
        MAX_COVERAGE_MAD,
        MIN_DEPTH,
    )
    from wgsextract_cli.commands.genotyping.smn import (
        NOT_DIAGNOSIS as SMN_NOT_DIAGNOSIS,
    )

    metrics = source.metrics
    smn1 = metrics.get("smn1_copy_number")
    depth = metrics.get("median_depth")
    mad = metrics.get("coverage_mad")
    carrier, affected = metrics.get("sma_carrier"), metrics.get("sma_affected")
    if smn1 is None:
        label, reasons = variant_confidence.LOW, ["no_call"]
        if any(w.get("code") == "low_coverage" for w in source.warnings()):
            reasons = ["low_coverage"]
    elif carrier is None or affected is None:
        label, reasons = variant_confidence.LOW, ["status_uncertain"]
    elif not str(metrics.get("smn_filter") or "").startswith("PASS"):
        label, reasons = variant_confidence.LOW, ["caller_filter"]
    elif any(w.get("code") == "aligner_not_calibrated" for w in source.warnings()):
        label, reasons = variant_confidence.LOW, ["aligner_not_calibrated"]
    elif isinstance(mad, (int, float)) and mad > MAX_COVERAGE_MAD:
        label, reasons = variant_confidence.LOW, ["uneven_coverage"]
    elif not isinstance(depth, (int, float)):
        label, reasons = variant_confidence.MODERATE, ["depth_unknown"]
    elif depth < MIN_DEPTH:
        label, reasons = variant_confidence.LOW, ["low_depth"]
    else:
        label, reasons = variant_confidence.HIGH, []
    if smn1 is None:
        value = "no call"
    elif smn1 == 0:
        value = "0 SMN1 copies: consistent with SMA" + (
            "" if affected else " (status uncertain)"
        )
    elif smn1 == 1:
        value = "1 SMN1 copy: SMA carrier" + ("" if carrier else " (status uncertain)")
    else:
        value = f"{smn1} SMN1 copies: not a carrier by copy number"
    g27134 = metrics.get("g27134tg_copy_number")
    detail = ", ".join(
        x
        for x in (
            f"SMN1 {smn1}" if smn1 is not None else "",
            f"SMN2 {metrics.get('smn2_copy_number')}" if smn1 is not None else "",
            f"g.27134T>G {g27134}" if g27134 is not None else "",
            f"filter {metrics.get('smn_filter')}" if metrics.get("smn_filter") else "",
            f"median depth {_number(depth, 'x')}" if depth is not None else "",
        )
        if x
    )
    note = SMN_NOT_DIAGNOSIS
    if smn1 == 2:
        note = (
            "Two SMN1 copies can still be a silent (2+0) carrier, with both on one "
            "chromosome"
            + (", which g.27134T>G here makes more likely" if g27134 else "")
            + ". "
            + note
        )
    section["findings"].append(
        _finding(
            "carrier_screening",
            "SMA carrier status (SMN1 copy number, SMNCopyNumberCaller)",
            value,
            detail=detail or None,
            gene="SMN1",
            note=note,
            confidence=label,
            reasons=reasons,
            basis="SMNCopyNumberCaller filter, status and median depth vs. ~30x",
            evidence=ctx.rel(source.output("smn_calls", "smn_report")),
            region="genes SMN1, SMN2",
            manifest=ctx.rel(source.manifest),
        )
    )


def _hla_findings(section: dict[str, Any], source: Source, ctx: _Context) -> None:
    """The HLA-A and HLA-B diplotypes ``typing hla`` called (those PharmCAT
    takes), labelled by T1K's allele qualities."""
    from wgsextract_cli.commands.genotyping.hla import (
        PHARMCAT_GENES,
        parse_t1k,
        reported_diplotype,
    )

    evidence = source.output("hla_genotypes")
    try:
        calls = {c.gene: c for c in parse_t1k(str(evidence))} if evidence else None
    except (OSError, ValueError, IndexError) as e:
        section["warnings"].append(
            ReportWarningDocument(
                code="unreadable_evidence", message=f"T1K genotypes: {e}"
            )
        )
        calls = None
    release = source.metrics.get("ipd_imgt_hla_release")
    for gene in PHARMCAT_GENES:
        diplotype = source.metrics.get(gene)
        call = (calls or {}).get(gene)
        qualities = [q for _, q in call.alleles] if call else []
        if calls is not None and reported_diplotype(call) != diplotype:
            # e.g. a later, interrupted run replaced the genotypes
            label, reasons = NOT_ASSESSED, ["evidence_mismatch"]
            section["warnings"].append(
                ReportWarningDocument(
                    code="evidence_mismatch",
                    message=f"The T1K genotypes no longer give the {gene} call "
                    "typing hla recorded; rerun it.",
                )
            )
        elif not diplotype:
            label, reasons = variant_confidence.LOW, ["no_call"]
        elif calls is None:
            label, reasons = variant_confidence.MODERATE, ["quality_unknown"]
        else:
            label, reasons = variant_confidence.HIGH, []
        detail = "; ".join(
            x
            for x in (
                f"T1K quality {', '.join(map(str, qualities))}" if qualities else "",
                str(release or ""),
            )
            if x
        )
        section["findings"].append(
            _finding(
                "pharmacogenomics",
                f"{gene} alleles (T1K)",
                str(diplotype or "no confident call"),
                detail=detail or None,
                gene=gene,
                confidence=label,
                reasons=reasons,
                basis="T1K allele quality (above 0 for each allele, as T1K recommends)",
                evidence=ctx.rel(evidence),
                region=f"gene {gene}",
                manifest=ctx.rel(source.manifest),
            )
        )


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


def _clinvar(ctx: _Context) -> dict[str, Any]:
    source = ctx.latest(_command_or_role("vcf clinvar", "clinvar_pathogenic"))
    section = _section("clinvar", [source], ctx)
    described = gene_lists.describe(ctx.gene_list)
    section["title"] = SECTIONS["clinvar"].format(gene_list=described["name"])
    section["gene_list"] = described
    if source is None or source.status != "ok":
        command = ctx.command_for("vcf clinvar", ctx.vcf, "vcf.gz")
        if ctx.vcf is None and ctx.alignment:
            command = (
                f"{ctx.command_for('vcf snp', ctx.alignment, 'cram')}, then {command}"
            )
        _missing(section, "ClinVar annotation (vcf clinvar)", command)
        return section
    vcf = source.output("clinvar_pathogenic")
    # The evidence VCF's own header, else the current facts of the VCF it read
    vcf_inputs = [
        source.locate(str(r["path"]))
        for role in ("vcf_input", "input")
        for r in source.records("inputs")
        if r.get("role") == role
        and str(r.get("path") or "").lower().endswith(_VCF_SUFFIXES)
        and read_scope.may_follow(source.locate(str(r["path"])))
    ]
    build = _gene_build(
        (_vcf_build(vcf) if vcf else None)
        or next(filter(None, map(ctx.build_of, vcf_inputs)), None)
    )
    labelled, complete = _labelled_calls(
        source, "clinvar_pathogenic", "confidence_table"
    )
    calls = [
        c
        for c in labelled
        if variant_confidence.is_pathogenic(str(c.get("CLNSIG") or ""))
    ]
    conflicts, _ = _labelled_calls(
        source, "clinvar_conflicts", "clinvar_conflict_table"
    )
    conflicts = [
        c for c in conflicts if clinical_evidence.review_evidence(c)["conflict"]
    ]
    gene_list = gene_lists.GeneList(described["id"], described["name"], ())
    unavailable = False
    if calls or conflicts:
        try:
            gene_list = gene_lists.load(ctx.gene_list)
        except gene_lists.GeneListUnavailable as error:
            unavailable = True
            section["warnings"].append(
                ReportWarningDocument(code="gene_list_unavailable", message=str(error))
            )
    if not unavailable:
        for call in calls:
            genes = _call_genes(
                gene_list,
                call,
                build,
                str(call.get("chrom") or ""),
                _int(call.get("pos")),
            )
            call["_report_gene_symbols"] = [g.symbol for g in genes] if genes else []
        calls = clinical_evidence.nonredundant_calls(calls)
    outside = unplaced = 0
    findings = []
    gene_calls: dict[str, tuple[gene_lists.Gene, list[Mapping[str, Any]]]] = {}
    for call in () if unavailable else [*calls, *conflicts]:
        review = clinical_evidence.review_evidence(call)
        pos = _int(call.get("pos"))
        chrom = str(call.get("chrom") or "")
        genes = _call_genes(gene_list, call, build, chrom, pos)
        if genes is None:
            unplaced += not review["conflict"]
            continue
        if not genes:
            outside += not review["conflict"]
            continue
        if "_report_gene_symbols" in call:
            genes = [g for g in genes if g.symbol in call["_report_gene_symbols"]]
        if not review["conflict"]:
            for gene in genes:
                gene_calls.setdefault(gene.symbol, (gene, []))[1].append(call)
        significance = str(call.get("CLNSIG") or "").replace("_", " ")
        # A variant ClinVar places in several listed genes is one finding.
        symbols = "/".join(g.symbol for g in genes)
        notes = [
            f"{g.symbol}: {g.note}" if len(genes) > 1 else g.note
            for g in genes
            if g.note
        ]
        if call.get("_mnv_residual_copies"):
            notes.append(
                f"{call['_mnv_residual_copies']} additional pathogenic copy outside the composed MNV; original genotype shown"
            )
        findings.append(
            _finding(
                "clinvar",
                f"{symbols} {significance}",
                significance,
                detail="; ".join(
                    filter(
                        None,
                        [
                            str(call.get("CLNDN") or "").replace("_", " "),
                            f"ClinVar review: {review['stars'] if review['stars'] is not None else 'unknown'} stars ({review['review_tier']})",
                            f"ClinVar ID: {review['id']}" if review["id"] else "",
                            f"Conflicting assertions (not consensus P/LP): {review['conflict_evidence'] or significance}"
                            if review["conflict"]
                            else "",
                        ],
                    )
                ),
                gene=symbols,
                note="; ".join(notes) or None,
                confidence=str(call.get("confidence") or NOT_ASSESSED),
                reasons=list(call.get("reasons") or []),
                basis=variant_confidence.SCHEME,
                evidence=ctx.rel(
                    source.output("clinvar_conflicts") if review["conflict"] else vcf
                ),
                region=f"{chrom}:{pos}",
                manifest=ctx.rel(source.manifest),
                variant={
                    "chrom": chrom,
                    "pos": pos,
                    "ref": call.get("ref"),
                    "alt": call.get("alt"),
                    "genotype": call.get("genotype"),
                    "zygosity": call.get("zygosity"),
                    "depth": _int(call.get("depth")),
                    "genotype_quality": _int(call.get("genotype_quality")),
                    "alt_fraction": _float(call.get("alt_fraction")),
                    "clinical_evidence": review,
                    "original_record": call.get(clinical_evidence.ORIGIN) or None,
                    "mnv_components": call.get(clinical_evidence.COMPONENTS) or None,
                    "mnv_residual_copies": call.get("_mnv_residual_copies"),
                    "phase_set": str(
                        call.get("_mnv_phase_set") or call.get("phase_set") or ""
                    )
                    or None,
                },
            )
        )
    order = {label: i for i, label in enumerate(CONFIDENCE)}
    findings.sort(key=lambda f: (order.get(f["confidence"], 9), f["gene"] or ""))
    section["findings"] = findings
    section["genes"] = sorted(
        (_gene_calls(g, c, complete) for g, c in gene_calls.values()),
        key=lambda g: g["gene"],
    )
    if not complete:
        section["warnings"].append(
            ReportWarningDocument(
                code="calls_truncated",
                message="The run's confidence table is missing, and its "
                "manifest lists only some calls: gene counts may be incomplete, "
                "and only a biallelic status (which more calls can't change) is "
                "given. Rerun vcf clinvar.",
            )
        )
    section["build"] = build
    section["counts"] = {
        "carried": len(calls),
        "in_gene_list": sum(
            not f["variant"]["clinical_evidence"]["conflict"] for f in findings
        ),
        "outside_gene_list": outside,
        "unplaced": unplaced,
        **{
            label: sum(
                f["confidence"] == label
                and not f["variant"]["clinical_evidence"]["conflict"]
                for f in findings
            )
            for label in variant_confidence.LABELS
        },
    }
    if conflicts:
        section["counts"]["conflicts_for_review"] = len(conflicts)
        section["all_conflicts"] = ctx.rel(
            source.output("clinvar_conflict_table")
            or source.output("clinvar_conflicts")
        )
    section["all_calls"] = ctx.rel(source.output("confidence_table") or vcf)
    if unavailable:
        section["counts"]["unplaced"] = len(calls)
        section["summary"] = (
            f"{len(calls)} call{'s' * (len(calls) != 1)} not placed in genes: "
            "the gene list is unavailable; see the full table."
        )
        return section
    if unplaced and build is None and gene_list.needs_build():
        section["warnings"].append(
            ReportWarningDocument(
                code="build_unknown",
                message="The VCF's build could not be identified, so its calls "
                "could not be placed in genes; see the full table.",
            )
        )
    elif unplaced:
        section["warnings"].append(
            ReportWarningDocument(
                code="gene_annotation_missing",
                message=f"{unplaced} call{'s' * (unplaced != 1)} have no ClinVar "
                "GENEINFO and the gene list has no spans to place them by; rerun "
                "vcf clinvar with a current ClinVar file, or use a list with "
                "spans. See the full table.",
            )
        )
    flagged = [
        f"{g['gene']} {g['status'].replace('_', ' ')}" + _qualifiers(g)
        for g in section["genes"]
        if g["status"]
    ]
    section["summary"] = (
        f"{section['counts']['in_gene_list']} call{'s' * (section['counts']['in_gene_list'] != 1)} in "
        f"{described['name']} genes; {outside} more elsewhere"
        + (f"; {unplaced} not placed" if unplaced else "")
        + (f". Recessive genes: {', '.join(flagged)}" if flagged else "")
        + (
            f"; {len(conflicts)} conflicting assertions for review (not consensus P/LP)"
            if conflicts
            else ""
        )
        + "."
    )
    return section


def _acmg_research(ctx: _Context) -> dict[str, Any] | None:
    """fastVEP's automated ACMG/AMP classes (`vcf acmg`, issue 629): the
    carried alleles' classes, and the P/LP calls in the gene list's genes
    under its reporting rules. Research only; None when `vcf acmg` never ran."""
    source = ctx.latest(_command_or_role("vcf acmg", "acmg_summary"))
    if source is None:
        return None
    section = _section("acmg_research", [source], ctx)
    described = gene_lists.describe(ctx.gene_list)
    section["title"] = SECTIONS["acmg_research"].format(gene_list=described["name"])
    section["gene_list"] = described
    section["disclaimer"] = acmg.DISCLAIMER
    # The disclaimer heads the section; the run's warning would repeat it.
    section["warnings"] = [
        w for w in section["warnings"] if w["code"] != "acmg_research_only"
    ]
    if source.status != "ok":
        return section
    summary: Mapping[str, Any] = {}
    if path := source.output("acmg_summary"):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = None
        summary = loaded if isinstance(loaded, Mapping) else {}
    tool = summary.get("tool") or {}
    section["tool"] = (
        f"{tool.get('name', 'fastVEP')} {tool['version']}"
        if tool.get("version")
        else None
    )
    section["criteria_not_evaluated"] = [
        str(c) for c in summary.get("criteria_not_evaluated") or []
    ]
    vcf = source.output("annotated_vcf")
    section["all_calls"] = ctx.rel(vcf)
    if vcf is None:
        section["warnings"].append(
            {
                "code": "evidence_missing",
                "message": "The classified VCF of the vcf acmg run is missing; "
                "rerun vcf acmg.",
            }
        )
        section["summary"] = "The classified VCF is missing."
        return section
    try:
        found = acmg_calls.scan(
            vcf,
            variant_confidence.thresholds_from(),
            contaminated=_contaminated(ctx, source.files("inputs")) is not None,
        )
    except (OSError, EOFError, ValueError, IndexError, zlib.error) as e:
        section["warnings"].append(
            ReportWarningDocument(
                code="unreadable_evidence",
                message=f"The classified VCF {vcf.name} can't be read ({e}); "
                "rerun vcf acmg.",
            )
        )
        section["summary"] = "The classified VCF can't be read."
        return section
    samples = found.header.samples
    if len(samples) > 1:
        section["warnings"].append(
            ReportWarningDocument(
                code="confidence_first_sample_only",
                message=f"The classes counted and the calls listed are the first "
                f"sample's ({samples[0]}) of this {len(samples)}-sample VCF; "
                "subset it to the intended sample (bcftools view -s) and rerun "
                "vcf acmg.",
            )
        )
    if found.rare_imputed:
        section["warnings"].append(
            ReportWarningDocument(
                code="rare_imputed_not_listed",
                message=f"{found.rare_imputed} imputed P/LP "
                f"allele{'s' * (found.rare_imputed != 1)} rarer than 1% in the "
                "panel (or of unknown panel frequency) are counted but not "
                "listed: imputation can't see a rare allele the reads don't "
                "show. See the classified VCF.",
            )
        )
    build = _gene_build(_vcf_build(vcf))
    section["build"] = build
    gene_list = gene_lists.GeneList(described["id"], described["name"], ())
    unavailable = False
    if found.calls:
        try:
            gene_list = gene_lists.load(ctx.gene_list)
        except gene_lists.GeneListUnavailable as error:
            unavailable = True
            section["warnings"].append(
                {"code": "gene_list_unavailable", "message": str(error)}
            )
    findings = []
    outside = unplaced = 0
    for call in () if unavailable else found.calls:
        row = call.row
        chrom, pos = str(row["chrom"]), _int(row["pos"])
        symbol = str(row["gene"] or "")
        if symbol:
            genes = gene_list.named([symbol])
        elif pos is not None and gene_list.has_spans(build):
            genes = gene_list.genes_at(build, chrom, pos)
        else:
            unplaced += 1
            continue
        if not genes:
            outside += 1
            continue
        cls = str(row["acmg_class"])
        symbols = "/".join(g.symbol for g in genes)
        criteria = str(row["acmg_criteria"] or "").replace("&", ", ")
        findings.append(
            _finding(
                "acmg_research",
                f"{symbols} {cls} (automated)",
                f"{acmg.CLASSES[cls]} (automated)",
                detail="; ".join(
                    filter(
                        None,
                        [
                            f"criteria met: {criteria}" if criteria else "",
                            f"classified on {row['acmg_transcript']}"
                            if row["acmg_transcript"]
                            else "",
                            str(row["hgvs_p"] or row["hgvs_c"] or ""),
                            str(row["consequence"] or ""),
                        ],
                    )
                ),
                gene=symbols,
                note="; ".join(
                    f"{g.symbol}: {g.note}" if len(genes) > 1 else g.note
                    for g in genes
                    if g.note
                )
                or None,
                confidence=call.confidence,
                reasons=call.reasons,
                basis="not assessed: a sites-only VCF has no genotype to judge"
                if found.sites_only
                else variant_confidence.SCHEME,
                evidence=ctx.rel(vcf),
                region=f"{chrom}:{pos}",
                manifest=ctx.rel(source.manifest),
                variant={
                    "chrom": chrom,
                    "pos": pos,
                    "ref": row["ref"],
                    "alt": row["alt"],
                    "genotype": row["genotype"],
                    "zygosity": row["zygosity"],
                    "depth": row["depth"],
                    "genotype_quality": row["gq"],
                    "alt_fraction": call.alt_fraction,
                    "phase_set": call.phase_set,
                },
                disclaimer=RESEARCH_ONLY,
            )
        )
    order = {label: i for i, label in enumerate(CONFIDENCE)}
    findings.sort(key=lambda f: (order.get(f["confidence"], 9), f["gene"] or ""))
    section["findings"] = findings
    pathogenic = len(found.calls)
    if unavailable:
        unplaced = pathogenic
    section["counts"] = {
        **{cls.lower(): found.carried[cls] for cls in acmg.CLASSES},
        "in_gene_list": len(findings),
        "outside_gene_list": outside,
        "unplaced": unplaced,
    }
    if unplaced and not unavailable:
        section["warnings"].append(
            {
                "code": "gene_annotation_missing",
                "message": f"{unplaced} P/LP call{'s' * (unplaced != 1)} have no "
                "VEP gene symbol and the gene list has no spans to place them by; "
                "see the classified VCF.",
            }
        )
    classes = ", ".join(f"{found.carried[c]:,} {c}" for c in acmg.CLASSES)
    alleles = "alleles" if found.sites_only else "carried alleles"
    section["summary"] = (
        f"fastVEP's automated classes of the {sum(found.carried.values()):,} "
        f"{alleles} it classified: {classes}. "
        f"{len(findings):,} P/LP call{'s' * (len(findings) != 1)} in "
        f"{described['name']} genes; {outside:,} more elsewhere"
        + (f"; {unplaced:,} not placed" if unplaced else "")
        + ". Research use only; not a diagnosis."
    )
    return section


# Rare SVs listed as findings, rarest first; the rest are in the table.
MAX_SV_FINDINGS = 100
_NOT_CARRIED = {"0/0", "0|0", "./.", ".|.", ".", "0"}


def _structural_variants(ctx: _Context) -> dict[str, Any]:
    source = ctx.latest(_command_or_role("vcf sv-freq", "sv_frequencies"))
    section = _section("structural_variants", [source], ctx)
    if source is None or source.status != "ok":
        command = ctx.command_for("vcf sv-freq", "sv.vcf.gz", "vcf.gz")
        if ctx.alignment:
            command = (
                f"{ctx.command_for('vcf sv', ctx.alignment, 'cram')}, then {command}"
            )
        _missing(section, "SV population frequencies (vcf sv-freq)", command)
        return section
    table = source.output("sv_frequencies")
    rows: list[dict[str, str]] = []
    if table is not None:
        try:
            with table.open(encoding="utf-8", newline="") as stream:
                lines = (line for line in stream if not line.startswith("#"))
                rows = list(csv.DictReader(lines, delimiter="\t"))
        except (OSError, csv.Error):
            rows = []
    common = _float(source.metrics.get("common_af")) or 0.01
    rare = _float(source.metrics.get("rare_af")) or 0.001
    carried = [
        r
        for r in rows
        if (r.get("genotype") or "") not in _NOT_CARRIED
        and (r.get("filter") or "PASS") in ("PASS", ".")
    ]
    counts = {
        label: sum(r.get("sv_freq") == label for r in carried)
        for label in ("common", "low", "rare", "novel")
    }
    rare_rows = sorted(
        (r for r in carried if r.get("sv_freq") == "rare"),
        key=lambda r: _float(r.get("sv_af")) or 0.0,
    )
    findings = []
    for r in rare_rows[:MAX_SV_FINDINGS]:
        pos, end = _int(r.get("pos")), _int(r.get("end"))
        af = _float(r.get("sv_af"))
        size = f", {_number(_int(r.get('svlen')))} bp" if r.get("svlen") else ""
        region = f"{r.get('chrom')}:{pos}" + (f"-{end}" if end and end != pos else "")
        findings.append(
            _finding(
                "structural_variants",
                f"{r.get('svtype')} {region}{size}",
                f"rare (AF < {rare:g})",
                detail=f"gnomAD AF {af:.2g} ({r.get('sv_af_source')} "
                f"{r.get('sv_af_id')}; match {r.get('sv_af_match')})"
                if af is not None
                else None,
                confidence=NOT_ASSESSED,
                reasons=[],
                basis="not assessed: frequency is from the gnomAD match, not "
                "the call's read support",
                evidence=ctx.rel(source.output("annotated_vcf")),
                region=region,
                manifest=ctx.rel(source.manifest),
                variant={
                    "chrom": r.get("chrom"),
                    "pos": pos,
                    "ref": None,
                    "alt": f"<{r.get('svtype')}>",
                    "genotype": r.get("genotype"),
                    "zygosity": None,
                    "depth": None,
                    "genotype_quality": None,
                    "alt_fraction": None,
                },
            )
        )
    section["findings"] = findings
    section["all_svs"] = ctx.rel(table)
    section["thresholds"] = {"common_af": common, "rare_af": rare}
    section["counts"] = {**counts, "listed": len(findings)}
    if len(rare_rows) > len(findings):
        section["warnings"].append(
            ReportWarningDocument(
                code="svs_truncated",
                message=f"Only the {len(findings)} rarest of {len(rare_rows)} "
                "rare SVs are listed; see the full table.",
            )
        )
    section["summary"] = (
        f"{sum(counts.values())} carried PASS SVs: {counts['common']} common "
        f"(AF >= {common:g}), {counts['low']} low-frequency, {counts['rare']} rare "
        f"(AF < {rare:g}) and {counts['novel']} with no eligible same-type match. "
        "Nondirectional CNVs are unassessed, not evidence of absence from gnomAD. "
        "Other unmatched calls may reflect catalogue gaps or caller artefacts, "
        "not rarity. Common SVs are unlikely to cause rare disease; "
        "rare matched SVs may warrant investigation."
    )
    return section


# A recessive gene's P/LP calls: one copy affected, (maybe) both.
GENE_STATUS = {
    "biallelic": "a homozygous P/LP call, or P/LP calls of two ALTs of one "
    "multi-allelic call (such as 1/2): both copies affected",
    "possible_biallelic": "possibly both copies affected: two or more "
    "heterozygous P/LP calls whose phase is unknown (unphased, or in different "
    "phase sets, so they may be on the same copy), or a call whose other "
    "allele was not called",
    "carrier": "one heterozygous P/LP call: one copy affected",
}
# The same statuses when the phase set (FORMAT/PS) of the calls decided them.
PHASED_STATUS = {
    "biallelic": "heterozygous P/LP calls phased on opposite copies (trans) "
    "in one phase set: both copies affected",
    "carrier": "heterozygous P/LP calls all phased on the same copy (cis) in "
    "one phase set: one copy affected",
}


def _split_index(origin: Sequence[str]) -> bool:
    """Whether ``origin`` (an ``--old-rec-tag`` split on ``|``) names one ALT
    of its record by number."""
    return (
        len(origin) == 5
        and origin[4].isdigit()
        and 1 <= int(origin[4]) <= len(origin[3].split(","))
    )


def _phased_copy(
    call: Mapping[str, Any], alleles: Sequence[str]
) -> tuple[tuple[str, str], int] | None:
    """The phase set (chromosome, FORMAT/PS) of a heterozygous diploid call
    phased within one, and the copy (0 or 1) carrying its ALT; None when its
    phase is unknown. Pipes without a PS don't say which calls were phased
    together, so they compare nothing."""
    genotype = str(call.get("_gene_genotype") or call.get("genotype") or "")
    phase_set = str(call.get("_mnv_phase_set") or call.get("phase_set") or "")
    if "/" in genotype or "|" not in genotype or phase_set in ("", "."):
        return None
    carried = [i for i, allele in enumerate(alleles) if allele != "0"]
    if len(alleles) != 2 or len(carried) != 1:
        return None
    return (str(call.get("chrom") or ""), phase_set), carried[0]


def _gene_calls(
    gene: gene_lists.Gene, calls: Sequence[Mapping[str, Any]], complete: bool = True
) -> dict[str, Any]:
    """A listed gene's P/LP calls by zygosity, and for a gene the list reports
    only when biallelic (AR) whether this sample is a carrier or (possibly)
    biallelic, with the weakest confidence of those calls.

    Heterozygous calls are on known copies only when phased (``0|1``) in one
    phase set (FORMAT/PS): on one copy (cis) they leave the other unaffected,
    on opposite copies (trans) they affect both. So are two ALTs split from
    one diploid call (``1/2``). Calls of unknown phase, or in different phase
    sets, make the gene only possibly biallelic, as does a haploid or partly
    called genotype (``1/.``), which doesn't show the other copy."""
    het = hom = hemi = partial = unphased = 0
    split_alts: dict[str, set[str]] = {}
    phase_sets: dict[tuple[str, str], set[int]] = {}
    for call in calls:
        zygosity = str(call.get("zygosity") or "")
        gt = str(call.get("_gene_genotype") or call.get("genotype") or "")
        alleles = re.split(r"[/|]", gt)
        if call.get("_gene_genotype"):
            zygosity = "het" if alleles.count("1") == 1 else "hom"
        if zygosity == "het" and "." in alleles:
            partial += 1
        elif zygosity == "het":
            het += 1
            # CHR|POS|REF|ALTS|IDX of the record it was split from. Beside a
            # reference copy (0/1/2) the two ALTs leave one copy unaffected.
            origin = str(call.get(variant_confidence.SPLIT_TAG) or "").split("|")
            if len(alleles) == 2 and _split_index(origin):
                split_alts.setdefault("|".join(origin[:4]), set()).add(origin[4])
            if copy := _phased_copy(call, alleles):
                phase_sets.setdefault(copy[0], set()).add(copy[1])
            else:
                unphased += 1
        elif zygosity == "hom":
            hom += 1
        elif zygosity == "haploid":
            hemi += 1
    split_trans = any(len(alts) > 1 for alts in split_alts.values())
    phased_trans = any(len(copies) > 1 for copies in phase_sets.values())
    # Calls of one phase set on one copy are one allele; others may each be.
    alleles_seen = unphased + len(phase_sets)
    phase = None
    if het >= 2:
        if split_trans or phased_trans:
            phase = "trans"
        elif alleles_seen == 1:
            phase = "cis"
        else:
            phase = "unknown"
    status = None
    if gene.recessive:
        if hom or split_trans or phased_trans:
            status = "biallelic"
        elif hemi or partial or alleles_seen >= 2:
            status = "possible_biallelic"
        elif het:
            status = "carrier"
    if not complete and status != "biallelic":
        status = None
    if not complete and phase != "trans":
        phase = None
    # Phase sets decided it: no homozygous call or split 1/2 already did.
    by_phase = (status, phase) == ("carrier", "cis") or (
        status == "biallelic" and not hom and not split_trans
    )
    order = {label: i for i, label in enumerate(CONFIDENCE)}
    labels = [str(c.get("confidence") or NOT_ASSESSED) for c in calls]
    return {
        "gene": gene.symbol,
        "inheritance": gene.inheritance or None,
        "heterozygous": het,
        "homozygous": hom,
        "hemizygous": hemi,
        "partial": partial,
        "phase": phase,
        "confidence": max(labels, key=lambda label: order.get(label, len(order))),
        "status": status,
        "status_text": (PHASED_STATUS if by_phase else GENE_STATUS).get(
            status or "", ""
        ),
    }


def _qualifiers(gene: Mapping[str, Any]) -> str:
    """' (cis; low-confidence calls)': the known phase behind the status, and
    the calls' confidence when not high."""
    known = (gene["status"], gene["phase"]) in {
        ("carrier", "cis"),
        ("biallelic", "trans"),
    }
    parts = [gene["phase"]] if known else []
    if gene["confidence"] != "high":
        parts.append(_confidence_text(gene))
    return f" ({'; '.join(parts)})" if parts else ""


def _confidence_text(gene: Mapping[str, Any]) -> str:
    if gene["confidence"] == NOT_ASSESSED:
        return "calls not assessed"
    return f"{gene['confidence']}-confidence calls"


def _call_genes(
    gene_list: gene_lists.GeneList,
    call: Mapping[str, Any],
    build: str | None,
    chrom: str,
    pos: int | None,
) -> list[gene_lists.Gene] | None:
    """The list's genes ClinVar names for the call (GENEINFO), or, for an
    annotation without GENEINFO, those whose span holds it; None if neither
    can place the call."""
    if symbols := _geneinfo(call):
        return gene_list.named(symbols)
    if pos is None or not gene_list.has_spans(build):
        return None
    return gene_list.genes_at(build, chrom, pos)


def _geneinfo(call: Mapping[str, Any]) -> list[str]:
    """The gene symbols of ClinVar's GENEINFO (``SYMBOL:GeneID|...``)."""
    text = str(call.get("GENEINFO") or "")
    return [e.split(":")[0] for e in text.split("|") if e and e != "."]


def _microarray(ctx: _Context) -> dict[str, Any]:
    source = ctx.latest(_command("microarray"))
    section = _section("microarray", [source], ctx)
    section["files"] = []
    if source is None or source.status != "ok":
        _missing(
            section,
            "Consumer microarray files (23andMe, AncestryDNA, ...)",
            ctx.command_for("microarray", ctx.alignment or ctx.vcf, "cram"),
        )
        return section
    for record in source.records("outputs"):
        if record.get("role") not in _MICROARRAY_ROLES or not record.get("path"):
            continue
        path = source.locate(str(record["path"]))
        metrics = record.get("metrics") or {}
        section["files"].append(
            {
                "file": ctx.rel(path),
                "role": record.get("role"),
                "format": metrics.get("format")
                or ("CombinedKit" if record.get("role") == "combined_kit" else None),
                "exists": read_scope.may_follow(path) and path.exists(),
                "sites": metrics.get("sites"),
                "called": metrics.get("called"),
                "no_call_rate": metrics.get("no_call_rate"),
            }
        )
    section["metrics"] = {
        k: source.metrics[k]
        for k in ("autosomal_called", "autosomal_homozygosity")
        if k in source.metrics
    }
    count = len(section["files"])
    section["summary"] = f"{count} file{'s' * (count != 1)} produced."
    return section


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


_POSIX_PATH = re.compile(r"""(?<![\w.~:/-])/(?:[^\s/:'"<>|]+/)+([^\s/:'"<>|]*)""")
_WINDOWS_PATH = re.compile(
    r"""(?<![\w])[A-Za-z]:[\\/](?:[^\s\\/:'"<>|]+[\\/])*([^\s\\/:'"<>|]*)"""
)


def _path_scrubber(ctx: _Context) -> Callable[[str], str]:
    """Rewrites messages from the runs for sharing: paths in the sample folder
    (as it is now, or as a manifest recorded it before a move) relative to
    it, and any other absolute path as its file name alone."""
    prefixes: dict[str, str] = {}
    for source in ctx.sources:
        here = ctx.rel(source.manifest.parent) or "."
        for folder in (source.recorded_folder, str(source.manifest.parent)):
            if folder:
                prefixes[folder] = here
    prefixes[str(ctx.folder)] = "."
    variants = {
        spelling: rel
        for folder, rel in prefixes.items()
        for spelling in {folder, folder.replace("\\", "/")}
        if Path(spelling).is_absolute() or PureWindowsPath(spelling).is_absolute()
    }
    ordered = sorted(variants.items(), key=lambda item: -len(item[0]))

    def scrub(text: str) -> str:
        for folder, rel in ordered:
            lead = "" if rel == "." else f"{rel}/"
            for sep in ("/", "\\"):
                text = text.replace(folder + sep, lead)
        text = _WINDOWS_PATH.sub(lambda m: m.group(1) or "…", text)
        return _POSIX_PATH.sub(lambda m: m.group(1) or "…", text)

    return scrub


def build_report(
    folder: Path,
    *,
    input_file: Path | None = None,
    results: Iterable[Path] = (),
    sources: list[Source] | None = None,
    gene_list: str | Path | None = None,
) -> dict[str, Any]:
    """The report document (``wgsextract.report/1``) of the sample in ``folder``.

    ``gene_list`` is a built-in list's id or a TSV or BED file (core/gene_lists.py);
    by default the newest ACMG SF list."""
    gene_lists.describe(gene_list)
    folder = Path(os.path.abspath(folder))
    if sources is None:
        sources = sample_sources(folder, input_file, results)
    workspace = SampleWorkspace(folder)
    alignment, vcf = _choose_inputs(folder, input_file, sources, workspace)
    main_file = alignment or vcf
    described = workspace.describe(folder / main_file if main_file else None)
    ctx = _Context(folder, sources, dict(described), alignment, vcf, gene_list)
    info = latest(sources, _command("info"))
    name = (
        input_stem(alignment or vcf or "")
        or (
            input_stem(info.files("inputs")[0]) if info and info.files("inputs") else ""
        )
        or folder.name
    )
    research = _acmg_research(ctx)
    sections = {
        "overview": _overview(ctx),
        "lineage": _lineage(ctx),
        "ancestry": _ancestry(ctx),
        "pharmacogenomics": _pharmacogenomics(ctx),
        "carrier_screening": _carrier_screening(ctx),
        "clinvar": _clinvar(ctx),
        # Opt-in research: shown only when `vcf acmg` ran, never asked for.
        **({"acmg_research": research} if research is not None else {}),
        "structural_variants": _structural_variants(ctx),
        "microarray": _microarray(ctx),
    }
    scrub = _path_scrubber(ctx)
    for section in sections.values():
        for warning in section["warnings"]:
            warning["message"] = scrub(warning["message"])
        if section.get("error"):
            section["error"] = scrub(section["error"])
        for finding in section["findings"]:
            for key in ("file", "manifest"):
                file = finding["evidence"][key]
                if file and not (folder / file).exists():
                    section["warnings"].append(
                        ReportWarningDocument(
                            code="evidence_missing",
                            message=f"Evidence file not found: {file}",
                        )
                    )
    return {
        "schema": SCHEMA_ID,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wgsextract_version": __version__,
        "sample": {
            "name": name,
            "folder": folder.name,
            "alignment": alignment,
            "vcf": vcf,
            "workspace": WORKSPACE_FILE if described.get("exists") else None,
        },
        "disclaimer": DISCLAIMER,
        "guide": GUIDE,
        "confidence_reasons": REASONS,
        "sections": sections,
    }


def findings(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [f for s in report["sections"].values() for f in s["findings"]]


def table_rows(report: Mapping[str, Any]) -> list[list[Value]]:
    rows: list[list[Value]] = []
    for f in findings(report):
        v = f["variant"] or {}
        clinical = v.get("clinical_evidence") or {}
        rows.append(
            [
                f["section"],
                f["label"],
                f["value"],
                f["detail"],
                f["gene"],
                v.get("chrom"),
                v.get("pos"),
                v.get("ref"),
                v.get("alt"),
                v.get("genotype"),
                v.get("zygosity"),
                v.get("depth"),
                v.get("genotype_quality"),
                v.get("alt_fraction"),
                f["confidence"],
                "|".join(f["confidence_reasons"]),
                f["evidence"]["file"],
                f["evidence"]["region"],
                f["disclaimer"],
                clinical.get("id"),
                clinical.get("review_status"),
                clinical.get("stars"),
                clinical.get("review_tier"),
                clinical.get("conflict_evidence"),
                v.get("original_record"),
                v.get("mnv_components"),
                v.get("mnv_residual_copies"),
            ]
        )
    return rows


def load_schema() -> dict[str, Any]:
    """The wgsextract.report/1 JSON Schema shipped with the package."""
    text = resources.files("wgsextract_cli").joinpath(SCHEMA_RESOURCE).read_text()
    schema: dict[str, Any] = json.loads(text)
    return schema
