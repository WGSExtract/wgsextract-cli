"""What the sample's existing report says about a lookup's site or gene.

Findings are copied from the ``wgsextract.report/1`` document with their own
confidence, evidence file and disclaimer, attributed to that report; nothing
here classifies or diagnoses. A section the report shows as not run is listed
with the command that would run it, so "no finding" is never confused with
"not looked at".
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from wgsextract_cli.core import read_scope
from wgsextract_cli.core.builds import canonical_build

from .identifiers import Locus, contig_key

REPORT_SUFFIX = "_personal_report.json"
# Report sections whose findings are about variants or genes.
SECTIONS = (
    "clinvar",
    "acmg_research",
    "pharmacogenomics",
    "carrier_screening",
    "structural_variants",
)
_SPAN = re.compile(r"(?P<chrom>[^:\s]+):(?P<start>\d+)-(?P<end>\d+)")
LIMITATION = (
    "A report section lists only what its analysis reports (its title says "
    "what it covers); no finding here is not evidence that a variant is "
    "absent or benign."
)


def discover(folder: Path) -> list[Path]:
    """Personal reports in ``folder`` a server job may read, newest first."""
    found = []
    for candidate in folder.glob(f"*{REPORT_SUFFIX}"):
        if not read_scope.may_follow(candidate.resolve()):
            continue
        try:
            found.append((candidate.stat().st_mtime, candidate))
        except OSError:
            continue
    return [path for _, path in sorted(found, reverse=True)]


def report_path(
    explicit: Path | None, input_path: Path
) -> tuple[Path | None, list[Path]]:
    """The report to read (an explicit file or folder, else the only one in
    the input's folder) and, when that is ambiguous, the candidates."""
    folder = input_path.expanduser().absolute().parent
    if explicit is not None:
        given = explicit.expanduser().absolute()
        if not given.is_dir():
            return given, []
        folder = given
    reports = discover(folder)
    return (reports[0], []) if len(reports) == 1 else (None, reports)


def _load(path: Path) -> Mapping[str, Any]:
    from wgsextract_cli.core import personal_report
    from wgsextract_cli.core.utils import UsageError

    if not read_scope.may_follow(path.resolve()):
        raise UsageError(f"The report {path} is outside the folders this run may read.")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise UsageError(f"Can't read the report {path}: {e}") from None
    if not isinstance(document, dict) or document.get("schema") != (
        personal_report.SCHEMA_ID
    ):
        raise UsageError(f"{path} is not a {personal_report.SCHEMA_ID} report.")
    return document


def _overlaps(variant: Mapping[str, Any], region: str, loci: Sequence[Locus]) -> bool:
    """Whether a finding's variant (its REF, or a structural variant's
    recorded chrom:start-end) overlaps ``loci``."""
    chrom, pos = variant.get("chrom"), variant.get("pos")
    if not isinstance(chrom, str) or not isinstance(pos, int):
        return False
    end = pos + max(len(str(variant.get("ref") or "N")), 1) - 1
    span = _SPAN.fullmatch(region)
    if variant.get("ref") is None and span and int(span["start"]) == pos:
        end = max(end, int(span["end"]))
    return any(
        contig_key(chrom) == contig_key(locus.chrom)
        and pos <= locus.end
        and locus.start <= end
        for locus in loci
    )


def _match(
    finding: Mapping[str, Any],
    loci: Sequence[Locus],
    genes: Iterable[str],
    *,
    whole_genes: bool,
) -> str | None:
    """ "site" when the finding's variant is in ``loci``; "gene" when it is
    about one of ``genes`` and, unless ``whole_genes``, about no other site."""
    variant = finding.get("variant")
    region = str((finding.get("evidence") or {}).get("region") or "")
    placed = isinstance(variant, Mapping) and isinstance(variant.get("pos"), int)
    if placed and _overlaps(variant, region, loci):  # type: ignore[arg-type]
        return "site"
    if placed and not whole_genes:
        return None
    wanted = {g.upper() for g in genes}
    gene = str(finding.get("gene") or "").upper()
    if (gene and gene in wanted) or region.upper().removeprefix("GENE ") in wanted:
        return "gene"
    return None


_FINDING_KEYS = (
    "section",
    "label",
    "value",
    "detail",
    "gene",
    "note",
    "confidence",
    "confidence_reasons",
    "confidence_basis",
    "evidence",
    "variant",
    "disclaimer",
    "pgx",
)


def join(
    explicit: Path | None,
    input_path: Path,
    loci: Sequence[Locus],
    genes: Sequence[str],
    *,
    whole_genes: bool,
    build: str,
    first_sample: bool,
) -> dict[str, object]:
    """The report's findings at ``loci`` or about ``genes`` (any of their
    sites when ``whole_genes``), and the state of each section that could
    hold them. A report found beside the input but made from another VCF, a
    sample other than the VCF's first (the one reports judge) and a section
    on another build join nothing."""
    command = f"wgsextract report --input {input_path}"
    path, candidates = report_path(explicit, input_path)
    if path is None:
        if candidates:
            return {
                "status": "ambiguous_report",
                "candidates": [str(p) for p in candidates],
                "message": "Several reports are in the input's folder; choose one "
                "with --report.",
            }
        return {
            "status": "no_report",
            "message": "No personal report found for this sample, so no analysis "
            "results are joined.",
            "command": command,
        }
    document = _load(path)
    made_from = (document.get("sample") or {}).get("vcf")
    if explicit is None and made_from and made_from != input_path.name:
        return {
            "status": "report_for_other_input",
            "candidates": [str(path)],
            "message": f"The report beside the input was made from {made_from}, "
            "not this VCF; give --report to join it anyway.",
            "command": command,
        }
    if not first_sample:
        return {
            "status": "report_for_other_sample",
            "candidates": [str(path)],
            "message": "Reports judge a multi-sample VCF's first sample, not the "
            "one chosen with --sample, so none is joined.",
        }
    sections = document.get("sections") or {}
    findings: list[dict[str, object]] = []
    states: list[dict[str, object]] = []
    for name in SECTIONS:
        section = sections.get(name)
        if not isinstance(section, Mapping):
            continue
        matched = 0
        section_build = canonical_build(str(section.get("build") or "")) or None
        other_build = section_build not in (None, canonical_build(build))
        for finding in [] if other_build else section.get("findings") or []:
            if not isinstance(finding, Mapping):
                continue
            how = _match(finding, loci, genes, whole_genes=whole_genes)
            if how is None:
                continue
            matched += 1
            findings.append(
                {
                    "match": how,
                    **{k: finding[k] for k in _FINDING_KEYS if k in finding},
                }
            )
        state: dict[str, object] = {
            "section": name,
            "title": section.get("title"),
            "status": "build_mismatch" if other_build else section.get("status"),
            "matched": matched,
        }
        if section.get("status") == "not_run":
            state["missing"] = section.get("missing") or []
        if section.get("error"):
            state["error"] = section.get("error")
        sources = section.get("sources") or []
        if sources:
            state["sources"] = sources
        gene_list = section.get("gene_list")
        if isinstance(gene_list, Mapping):
            state["gene_list"] = {
                k: gene_list.get(k) for k in ("id", "name", "version") if k in gene_list
            }
        states.append(state)
    return {
        "status": "joined",
        "report": {
            "path": str(path),
            "generated": document.get("generated"),
            "wgsextract_version": document.get("wgsextract_version"),
            "sample": document.get("sample"),
            "disclaimer": document.get("disclaimer"),
        },
        "findings": findings,
        "sections": states,
        "limitations": [LIMITATION],
    }


def report_age_days(
    interpretation: Mapping[str, object], input_path: Path
) -> float | None:
    """How much older than the input the joined report is, in days (None
    when it isn't older or can't be told)."""
    report = interpretation.get("report")
    if not isinstance(report, Mapping):
        return None
    try:
        generated = datetime.fromisoformat(str(report.get("generated")))
        changed = input_path.expanduser().stat().st_mtime
    except (OSError, ValueError, TypeError):
        return None
    behind = changed - generated.timestamp()
    return behind / 86400 if behind > 0 else None
