"""``panel check``: validate a research panel file and resolve its genes.

Reads no sample data and writes no files; the result's ``details`` hold the
panel's identity and SHA-256, each problem with its location, each gene's
resolution and coding length, and each group's effective filters.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

from wgsextract_cli.api.panel import PanelCheckParams
from wgsextract_cli.core import panel, panel_spec
from wgsextract_cli.core.errors import CodedError
from wgsextract_cli.core.panel_presets import PRESETS
from wgsextract_cli.core.results import current_result, human_output, warn

# PharmCAT calls these genes' star alleles; a haplotype watch over one of
# them duplicates `vcf pharmcat`, which handles their exceptions and CNVs.
PHARMCAT_GENES = frozenset(g.upper() for g in PRESETS["pharmacogenes"].genes)


def _gene_record(match: panel.GeneMatch, notes: panel.Annotations) -> dict[str, Any]:
    record: dict[str, Any] = {"given": match.given, "status": match.via}
    if match.symbol is None:
        if match.candidates:
            record["candidates"] = list(match.candidates)
        if match.suggestions:
            record["suggestions"] = list(match.suggestions)
        return record
    gene = notes.genes[match.symbol]
    record.update(
        symbol=gene.symbol,
        hgnc_id=gene.hgnc_id or None,
        biotype=gene.biotype,
        loci=[
            {"chrom": x.chrom, "start": x.start, "end": x.end}
            for x in notes.loci[match.symbol]
        ],
        cds_bp=gene.cds_bp,
        cds_transcript=gene.cds_transcript or None,
        cds_basis=gene.cds_basis or None,
    )
    return record


def _note_match(
    match: panel.GeneMatch, notes: panel.Annotations, where: str, owner: str
) -> None:
    if match.via in ("unknown", "ambiguous"):
        code = f"panel_gene_{'ambiguous' if match.via == 'ambiguous' else 'unresolved'}"
        warn(
            code,
            f"{where}: {panel.match_problem(match, notes)}; the audit leaves it out.",
            gene=match.given,
            location=where,
            owner=owner,
        )
        return
    symbol = notes.loci[match.symbol or ""][0].symbol
    if match.via == "alias":
        warn(
            "panel_gene_alias",
            f"{where}: {match.given} is an alias of {symbol}; using {symbol}.",
            gene=match.given,
            symbol=symbol,
            location=where,
        )


def _groups(
    document: Mapping[str, Any], notes: panel.Annotations
) -> tuple[list[dict[str, Any]], list[panel_spec.Problem]]:
    records: list[dict[str, Any]] = []
    problems: list[panel_spec.Problem] = []
    for index, group in enumerate(document.get("groups", [])):
        where = f"groups[{index}]"
        genes: list[dict[str, Any]] = []
        symbols: dict[str, str] = {}
        for position, name in enumerate(group["genes"]):
            location = f"{where}.genes[{position}]"
            match = panel.match_gene(name, notes)
            _note_match(match, notes, location, group["id"])
            genes.append(_gene_record(match, notes))
            if match.symbol is None:
                continue
            if match.symbol in symbols:
                problems.append(
                    panel_spec.Problem(
                        "duplicate",
                        location,
                        f"{name} is {notes.genes[match.symbol].symbol}, as is "
                        f"{symbols[match.symbol]} in this group",
                    )
                )
            symbols.setdefault(match.symbol, name)
            if notes.genes[match.symbol].cds_bp is None:
                warn(
                    "panel_gene_noncoding",
                    f"{location}: {name} has no coding sequence in "
                    f"{notes.source} ({notes.genes[match.symbol].biotype}); "
                    "coding filters can't qualify its variants.",
                    gene=name,
                    location=location,
                )
        coding = [g["cds_bp"] for g in genes if g.get("cds_bp")]
        records.append(
            {
                "id": group["id"],
                "name": group.get("name"),
                "genes": genes,
                "resolved": len(symbols),
                "cds_bp": sum(coding),
                "recessive": list(group.get("recessive", [])),
                "filters": panel_spec.effective_filters(document, group),
                **({"dosage": group["dosage"]} if "dosage" in group else {}),
            }
        )
    return records, problems


def _coding_loci(notes: panel.Annotations) -> dict[str, list[panel.Locus]]:
    """Coding genes' loci by chromosome, once per run."""
    by_chrom: dict[str, list[panel.Locus]] = {}
    for key, loci in notes.loci.items():
        if notes.genes[key].cds_bp:
            for locus in loci:
                by_chrom.setdefault(locus.chrom, []).append(locus)
    return by_chrom


def _genes_at(loci: list[panel.Locus], pos: int) -> list[str]:
    """Symbols of the loci holding 1-based ``pos``."""
    return sorted({x.symbol for x in loci if x.start < pos <= x.end})


def _sites(
    document: Mapping[str, Any], notes: panel.Annotations, same_build: bool
) -> list[dict[str, Any]]:
    records = []
    coding = _coding_loci(notes) if same_build and document.get("sites") else {}
    for site in document.get("sites", []):
        position = panel_spec.parse_position(site["locus"])
        record: dict[str, Any] = {
            "id": site["id"],
            "locus": site["locus"],
            "rsid": site.get("rsid"),
        }
        if same_build:
            record["genes"] = _genes_at(coding.get(position.chrom, []), position.pos)
        records.append(record)
    return records


def _haplotypes(
    document: Mapping[str, Any], notes: panel.Annotations
) -> list[dict[str, Any]]:
    records = []
    for index, haplotype in enumerate(document.get("haplotypes", [])):
        where = f"haplotypes[{index}].gene"
        match = panel.match_gene(haplotype["gene"], notes)
        _note_match(match, notes, where, haplotype["id"])
        pharmcat = match.symbol in PHARMCAT_GENES
        if pharmcat:
            symbol = notes.genes[match.symbol or ""].symbol
            warn(
                "panel_haplotype_pharmcat_gene",
                f"{where}: PharmCAT calls {symbol}'s star alleles, with its "
                "exceptions and copy-number alleles; prefer `wgsextract vcf "
                "pharmcat` for them. This watch only matches the panel's "
                "allele combinations.",
                gene=symbol,
                location=where,
            )
        records.append(
            {
                "id": haplotype["id"],
                "gene": _gene_record(match, notes),
                "sites": list(haplotype["sites"]),
                "alleles": sorted(haplotype["alleles"]),
                "pharmcat_gene": pharmcat,
            }
        )
    return records


def _fail(
    details: dict[str, Any], problems: list[panel_spec.Problem], path: Path
) -> NoReturn:
    details["valid"] = False
    details["problems"] = [
        {"code": p.code, "location": p.location, "message": p.message} for p in problems
    ]
    current_result().set_details(details)
    count = f"{len(problems)} problem{'s' * (len(problems) != 1)}"
    raise CodedError(
        "panel_invalid",
        f"{path} is not a valid {panel_spec.SCHEMA_ID} research panel ({count}):\n  "
        + "\n  ".join(map(str, problems)),
    )


def inspect_panel(
    path: Path, requested_build: str | None
) -> tuple[Mapping[str, Any], panel.Annotations, dict[str, Any]]:
    """Validate and resolve a panel for either check or audit."""
    path = path.expanduser()
    result = current_result()
    result.add_input(path, "panel")
    parsed = panel_spec.read_panel(path)
    warn("panel_research_only", panel_spec.DISCLAIMER)
    details: dict[str, Any] = {
        "research_only": True,
        "panel": {
            "path": str(path.absolute()),
            "format": parsed.format,
            "sha256": parsed.sha256,
        },
    }
    document = parsed.document
    problems = parsed.problems or panel_spec.validate(document or {})
    if problems or document is None:
        _fail(details, problems, path)
    declared = document.get("build")
    build = requested_build or panel_spec.PANEL_BUILDS.get(declared or "", "hg38")
    notes = panel.load_annotations(build)
    same_build = declared is None or panel_spec.PANEL_BUILDS[declared] == build
    if not same_build and (document.get("sites") or document.get("haplotypes")):
        warn(
            "panel_build_differs",
            f"The panel's sites and haplotypes are {declared} coordinates; "
            f"inputs on {build} can't be audited against them.",
            panel_build=declared,
            build=build,
        )
    groups, problems = _groups(document, notes)
    details["panel"].update(
        id=document["id"],
        name=document["name"],
        version=document["version"],
        build=declared,
    )
    details.update(
        build=build,
        annotation=notes.source,
        groups=groups,
        sites=_sites(document, notes, same_build),
        haplotypes=_haplotypes(document, notes),
        calibration=panel_spec.effective_calibration(document),
    )
    if problems:
        _fail(details, problems, path)
    details["valid"] = True
    details["problems"] = []
    return document, notes, details


def run_panel_check(params: PanelCheckParams) -> None:
    document, notes, details = inspect_panel(params.panel, params.build)
    result = current_result()
    result.set_details(details)
    groups = details["groups"]
    genes = sum(g["resolved"] for g in groups)
    unresolved = sum(
        1 for g in groups for gene in g["genes"] if gene.get("symbol") is None
    )
    result.add_metrics(
        groups=len(groups),
        genes=genes,
        unresolved_genes=unresolved,
        sites=len(details["sites"]),
        haplotypes=len(details["haplotypes"]),
    )
    human_output(
        f"{document['name']} ({document['id']} {document['version']}): a valid "
        f"{panel_spec.SCHEMA_ID} research panel."
    )
    human_output(f"SHA-256: {details['panel']['sha256']}")
    human_output(f"Genes resolved in {details['build']} ({notes.source}):")
    for group in groups:
        missing = [g["given"] for g in group["genes"] if g.get("symbol") is None]
        line = (
            f"  {group['id']}: {group['resolved']} of {len(group['genes'])} genes, "
            f"{group['cds_bp']:,} coding bp"
        )
        human_output(
            line + (f"; not resolved: {', '.join(missing)}" if missing else "")
        )
    if details["sites"] or details["haplotypes"]:
        human_output(
            f"{len(details['sites'])} site(s) and {len(details['haplotypes'])} "
            f"haplotype(s) on {document['build']}."
        )


__all__ = ["run_panel_check"]
