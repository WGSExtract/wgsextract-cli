"""The report's medication review: CPIC guidance for the medications a person
lists in a local ``--pgx-context`` file, gated on the evidence behind each
pharmacogenomic call.

A CPIC recommendation is given only when the calls it depends on are confident
enough to choose it: a weak call, a no-call or an ambiguous call counts as any
of its possible results, and when those lead to different recommendations the
guidance is withheld. A listed medication changes a phenotype only through a
rule in ``assets/report/pgx_context_rules.json`` whose CPIC statement the
loaded CPIC release still carries; the genetic (baseline) phenotype is kept
beside it. Nothing here is a prescription.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from wgsextract_cli.core import cpic_data, variant_confidence
from wgsextract_cli.core.cpic_data import CpicData, Drug, Recommendation
from wgsextract_cli.core.pgx_context import Medication, PgxContext
from wgsextract_cli.core.report_documents import (
    ReportAdvisoryDocument,
    ReportContextGeneDocument,
    ReportContextMedicationDocument,
    ReportContextRulesDocument,
    ReportCpicRecommendationDocument,
    ReportCpicReleaseDocument,
    ReportFindingDocument,
    ReportMedicationReviewDocument,
    ReportMissingDocument,
    ReportPgxContextDocument,
)

from .context import NOT_ASSESSED, SECTIONS, _Context, _finding, _worst
from .pgx_state import PgxGene

RULES_RESOURCE = "assets/report/pgx_context_rules.json"
NOT_PRESCRIPTION = "Not a prescription; review with a clinician."
DISCLAIMER = (
    "CPIC guidance for the medications you listed, matched to this sequencing "
    "run's pharmacogenomic calls, to discuss with a prescriber or pharmacist. "
    "It is not a prescription or medical advice: never start, stop or change a "
    "medication without professional review. Only drug effects CPIC states are "
    "applied; other interactions and health factors are not assessed. CPIC "
    "guidelines assist clinicians and are updated over time."
)
# CPIC wording that advises against the drug itself, sentence by sentence:
# not "No reason to avoid", "Avoid moderate and strong CYP2D6 inhibitors" or
# "If aromatase inhibitor use is contraindicated".
_AGAINST = re.compile(
    r"^avoid(?:$| (?!(?:moderate|strong|cyp\w+)\b))|"
    r"^(?!if\b).*\b(?:not recommended|contraindicated)\b|\bdo not use\b",
    re.IGNORECASE,
)


def against(text: str) -> bool:
    """Whether CPIC's recommendation advises against using the drug."""
    return any(
        _AGAINST.search(sentence.strip()) for sentence in re.split(r"[.;]\s+", text)
    )


@dataclass(frozen=True)
class Effect:
    possible: tuple[str, ...]
    quote: str
    unclear: bool = False


@dataclass(frozen=True)
class Rule:
    id: str
    gene: str
    drug: str
    effects: Mapping[str, Effect]


@dataclass(frozen=True)
class Rules:
    version: str
    sha256: str
    checked_against_cpic: str
    rules: tuple[Rule, ...]


def load_rules() -> Rules:
    text = resources.files("wgsextract_cli").joinpath(RULES_RESOURCE).read_bytes()
    document: dict[str, Any] = json.loads(text)
    return Rules(
        version=str(document["version"]),
        sha256=hashlib.sha256(text).hexdigest(),
        checked_against_cpic=str(document["checked_against_cpic"]),
        rules=tuple(
            Rule(
                rule["id"],
                rule["gene"],
                rule["drug"],
                {
                    phenotype: Effect(
                        tuple(effect["possible"]),
                        effect["quote"],
                        bool(effect.get("unclear")),
                    )
                    for phenotype, effect in rule["effects"].items()
                },
            )
            for rule in document["rules"]
        ),
    )


@dataclass
class GeneContext:
    """A gene's genetic phenotype and what the listed medications may make it."""

    gene: PgxGene
    possible: set[str] = field(default_factory=set)
    unclear: bool = False
    rule: Rule | None = None
    trigger: str | None = None
    quote: str | None = None
    recommendation: Recommendation | None = None
    note: str = ""

    @property
    def applied(self) -> bool:
        return self.quote is not None


@dataclass
class Advisory:
    medication: Medication
    drug: Drug | None
    status: str
    genes: list[str] = field(default_factory=list)
    recommendation: Recommendation | None = None
    alternatives: list[Recommendation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def medication_review(
    ctx: _Context, context: PgxContext, cpic: CpicData, rules: Rules
) -> ReportMedicationReviewDocument:
    """The ``medication_review`` section; ``ctx.pgx_genes`` must already hold
    the pharmacogenomics section's gene results."""
    pharmcat = ctx.latest(lambda s: s.command == "vcf pharmcat" and s.status == "ok")
    files = (
        ctx.rel(pharmcat.output("pharmcat_phenotypes")) if pharmcat else None,
        ctx.rel(pharmcat.manifest) if pharmcat else None,
    )
    resolved = {m.name: cpic.drug(m.name) for m in context.medications}
    listed = {drug.name for drug in resolved.values() if drug}
    adjustments = _adjustments(ctx.pgx_genes, listed, cpic, rules)

    section = ReportMedicationReviewDocument(
        title=SECTIONS["medication_review"],
        status="ok",
        summary="",
        findings=[],
        missing=[],
        warnings=[],
        sources=[],
        disclaimer=DISCLAIMER,
        cpic=ReportCpicReleaseDocument(
            release=cpic.release,
            published=str(cpic_data.PIN["published"]),
            sha256=cpic.sha256,
            url=str(cpic_data.PIN["url"]),
            license=str(cpic_data.PIN["license"]),
            attribution=str(cpic_data.PIN["attribution"]),
        ),
        rules=ReportContextRulesDocument(
            version=rules.version,
            sha256=rules.sha256,
            checked_against_cpic=rules.checked_against_cpic,
        ),
        context=ReportPgxContextDocument(
            sha256=context.sha256,
            medications=[
                ReportContextMedicationDocument(
                    name=m.name,
                    population=m.population,
                    cpic_drug=drug.id if (drug := resolved[m.name]) else None,
                )
                for m in context.medications
            ],
        ),
        genes=[],
    )
    if pharmcat is None:
        section["missing"].append(
            ReportMissingDocument(
                what="PharmCAT phenotypes, which the medication review rests on",
                command=ctx.command_for("vcf pharmcat", ctx.vcf, "vcf.gz"),
            )
        )

    genes_used: list[str] = []
    for medication in context.medications:
        for advisory in _advise(
            medication, resolved[medication.name], cpic, ctx.pgx_genes, adjustments
        ):
            genes_used += [g for g in advisory.genes if g not in genes_used]
            section["findings"].append(
                _advisory_finding(advisory, ctx.pgx_genes, cpic, files)
            )
    for gene in sorted({*genes_used, *adjustments}):
        state = ctx.pgx_genes.get(gene)
        if state is None:
            continue
        document = _gene_document(adjustments.get(gene) or GeneContext(state))
        section["genes"].append(document)
        section["findings"].append(_gene_finding(document, state, files))

    counts: dict[str, int] = {}
    for finding in section["findings"]:
        if advisory_document := finding.get("advisory"):
            status = advisory_document["status"]
            counts[status] = counts.get(status, 0) + 1
    section["summary"] = "; ".join(
        f"{n} {status.replace('_', ' ')}" for status, n in sorted(counts.items())
    )
    return section


def _adjustments(
    genes: Mapping[str, PgxGene], listed: set[str], cpic: CpicData, rules: Rules
) -> dict[str, GeneContext]:
    """The phenotype changes the listed medications' rules allow, each checked
    against the loaded CPIC release's own statement."""
    out: dict[str, GeneContext] = {}
    for rule in rules.rules:
        state = genes.get(rule.gene)
        if rule.drug not in listed or state is None:
            continue
        adjusted = out.setdefault(rule.gene, GeneContext(state))
        adjusted.rule, adjusted.trigger = rule, rule.drug
        phenotype = state.phenotype
        effect = rule.effects.get(phenotype or "")
        if phenotype is None:
            adjusted.note = (
                f"{rule.drug} may change {rule.gene} activity (CPIC), but the "
                "genetic phenotype isn't established confidently enough to adjust."
            )
            continue
        if effect is None:
            adjusted.note = f"CPIC states no {rule.drug} effect for a {phenotype}."
            continue
        drug = cpic.drug(rule.drug)
        source = next(
            (
                r
                for r in (cpic.recommendations_of(drug.id) if drug else ())
                if r.phenotypes.get(rule.gene) == phenotype
                and effect.quote in r.implications.get(rule.gene, "")
            ),
            None,
        )
        if source is None:
            adjusted.note = (
                f"Rule {rule.id} not applied: CPIC {cpic.release} no longer states it."
            )
            continue
        adjusted.possible |= set(effect.possible)
        adjusted.unclear |= effect.unclear
        adjusted.quote, adjusted.recommendation = effect.quote, source
    return out


def _advise(
    medication: Medication,
    drug: Drug | None,
    cpic: CpicData,
    genes: Mapping[str, PgxGene],
    adjustments: Mapping[str, GeneContext],
) -> list[Advisory]:
    def single(status: str, note: str) -> list[Advisory]:
        return [Advisory(medication, drug, status, notes=[note])]

    if drug is None:
        return single(
            "insufficient_evidence",
            f"{medication.name} isn't in CPIC {cpic.release}: no CPIC guideline "
            "covers it (or this isn't CPIC's generic name for it).",
        )
    recommendations = cpic.recommendations_of(drug.id)
    if not recommendations:
        pairs = cpic.pairs_of(drug.id)
        prescribing = [p.gene for p in pairs if p.level in cpic_data.PRESCRIBING_LEVELS]
        levels = ", ".join(sorted(f"{p.gene} {p.level}" for p in pairs))
        return single(
            "insufficient_evidence",
            f"CPIC's {drug.name} guidance ({', '.join(prescribing)}) isn't in its "
            "recommendation table (it may be a dosing algorithm); see the guideline."
            if prescribing
            else f"CPIC has no prescribing recommendation for {drug.name}"
            + (f" (CPIC levels: {levels})." if levels else "."),
        )
    populations = sorted({r.population for r in recommendations})
    general_only_note = None
    if medication.population:
        wanted = medication.population.lower()
        chosen = next((p for p in populations if p.lower() == wanted), None)
        if chosen is None and "general" not in populations:
            return single(
                "insufficient_evidence",
                f"CPIC has no {drug.name} recommendation for "
                f"'{medication.population}'; it covers: {', '.join(populations)}.",
            )
        if chosen is None:
            general_only_note = (
                f"CPIC's {drug.name} guidance is general; it doesn't distinguish "
                f"'{medication.population}'."
            )
        recommendations = [
            r for r in recommendations if r.population == (chosen or "general")
        ]
    elif len(populations) > 1:
        return single(
            "context_needed",
            f"CPIC's {drug.name} guidance differs by patient group or indication "
            f"({', '.join(populations)}): add population to {drug.name} in the "
            "context file.",
        )

    out = []
    for gene_set in sorted({tuple(sorted(r.lookup)) for r in recommendations}):
        group = [r for r in recommendations if tuple(sorted(r.lookup)) == gene_set]
        advisory = _evaluate(medication, drug, group, gene_set, genes, adjustments)
        if general_only_note:
            advisory.notes.append(general_only_note)
        out.append(advisory)
    return out


def _evaluate(
    medication: Medication,
    drug: Drug,
    group: Sequence[Recommendation],
    genes_needed: Sequence[str],
    genes: Mapping[str, PgxGene],
    adjustments: Mapping[str, GeneContext],
) -> Advisory:
    advisory = Advisory(medication, drug, "advisory", genes=list(genes_needed))
    missing = [g for g in genes_needed if g not in genes]
    if missing:
        advisory.status = "not_assessed"
        hint = (
            " with --outside-call from typing cyp2d6"
            if "CYP2D6" in missing
            else " with --outside-call from typing hla"
            if any(g.startswith("HLA-") for g in missing)
            else ""
        )
        advisory.notes.append(
            f"No {', '.join(missing)} result: run vcf pharmcat on this sample{hint}."
        )
        return advisory
    advisory.notes = [
        f"{gene}: {genes[gene].describe()} ({genes[gene].source}, evidence "
        f"{genes[gene].evidence.replace('_', ' ')})."
        for gene in genes_needed
    ]
    uncertain = [g for g in genes_needed if not genes[g].adequate]

    def fits(r: Recommendation, extra: Mapping[str, set[str]]) -> bool:
        """Whether ``r`` can be this person's: a weak call allows any result,
        a confident one its own lookup keys (or an ``extra`` phenotype)."""
        return all(
            not genes[gene].adequate
            or r.lookup.get(gene) in genes[gene].lookup_keys
            or r.phenotypes.get(gene) in extra.get(gene, set())
            for gene in genes_needed
        )

    base = _distinct(r for r in group if fits(r, {}))
    uncovered = _uncovered(group, genes_needed, genes)
    if base and uncovered:
        advisory.status = "withheld"
        advisory.alternatives = base
        advisory.reasons = ["alternatives_differ"]
        advisory.notes.append(
            f"CPIC has no {drug.name} recommendation for some possible results "
            f"({'; '.join(uncovered)}), so none can be established; confirm the "
            "genotype with a clinical test."
        )
        return advisory
    if not base:
        advisory.status = "insufficient_evidence"
        advisory.notes.append(
            f"CPIC has no {drug.name} recommendation for this result."
        )
        return advisory
    if len(base) > 1:
        advisory.status = "withheld"
        advisory.alternatives = base
        advisory.reasons = ["alternatives_differ"]
        advisory.notes.append(
            "CPIC's recommendations differ between the possible results of "
            f"{', '.join(uncertain or genes_needed)}; confirm the genotype with a "
            "clinical test before relying on any of them."
        )
        return advisory
    (chosen,) = base
    advisory.recommendation = chosen
    if uncertain:
        advisory.notes.append(
            f"CPIC's recommendation is the same for every possible "
            f"{', '.join(uncertain)} result."
        )
    possible = {
        gene: adjustments[gene].possible
        for gene in genes_needed
        if gene in adjustments and adjustments[gene].trigger != drug.name
    }
    advisory.alternatives = [
        r
        for r in _distinct(r for r in group if fits(r, possible))
        if _key(r) != _key(chosen)
    ]
    for gene in genes_needed:
        adjusted = adjustments.get(gene)
        if adjusted is None or not adjusted.applied or adjusted.trigger == drug.name:
            continue
        if advisory.alternatives or adjusted.unclear:
            advisory.status = "context_review"
            advisory.notes.append(
                f"With {adjusted.trigger}, CPIC states {gene} phenoconversion may "
                f"occur ({_possible_text(adjusted)}); CPIC's {drug.name} "
                "recommendation for the genetic phenotype may then not apply."
            )
        if re.search(rf"\b{re.escape(gene)}\b[^.]*\binhibitors?\b", chosen.text, re.I):
            advisory.status = "context_review"
            advisory.notes.append(
                f"This recommendation advises avoiding {gene} inhibitors, and CPIC "
                f"describes {adjusted.trigger} as inhibiting {gene}."
            )
    return advisory


def _uncovered(
    group: Sequence[Recommendation],
    genes_needed: Sequence[str],
    genes: Mapping[str, PgxGene],
) -> list[str]:
    """The possible results no recommendation in ``group`` covers: each
    combination of a confident gene's alternatives and a weak gene's values."""
    options: list[list[tuple[str, frozenset[str]]]] = []
    for gene in genes_needed:
        state = genes[gene]
        if state.adequate:
            options.append(
                [(a.diplotype, frozenset(a.lookup_keys)) for a in state.alternatives]
            )
        else:
            values = sorted({r.lookup[gene] for r in group if gene in r.lookup})
            options.append([(v, frozenset({v})) for v in values])
    return [
        ", ".join(
            f"{g} {name}" for g, (name, _) in zip(genes_needed, combo, strict=True)
        )
        for combo in itertools.product(*options)
        if not any(
            all(
                r.lookup.get(g) in keys
                for g, (_, keys) in zip(genes_needed, combo, strict=True)
            )
            for r in group
        )
    ]


def _key(r: Recommendation) -> tuple[str, str]:
    return r.text, r.classification


def _distinct(recommendations: Iterable[Recommendation]) -> list[Recommendation]:
    seen: dict[tuple[str, str], Recommendation] = {}
    for r in recommendations:
        seen.setdefault(_key(r), r)
    return list(seen.values())


def _possible_text(adjusted: GeneContext) -> str:
    if adjusted.unclear:
        return "to an unclear extent"
    if not adjusted.possible:
        return "no change"
    return "possibly " + " or ".join(sorted(adjusted.possible))


def _recommendation(
    r: Recommendation, cpic: CpicData
) -> ReportCpicRecommendationDocument:
    guideline = cpic.guidelines.get(r.guideline_id)
    _, pmid, doi = (guideline.publication if guideline else None) or ("", None, None)
    return ReportCpicRecommendationDocument(
        id=r.id,
        text=r.text,
        classification=r.classification,
        phenotypes=dict(r.phenotypes),
        implication="; ".join(r.implications.values()),
        population=r.population,
        guideline=guideline.name if guideline else r.guideline_id,
        pmid=pmid,
        doi=doi,
        url=guideline.url if guideline else "",
    )


def _advisory_finding(
    advisory: Advisory,
    genes: Mapping[str, PgxGene],
    cpic: CpicData,
    files: tuple[str | None, str | None],
) -> ReportFindingDocument:
    chosen = advisory.recommendation
    name = advisory.drug.name if advisory.drug else advisory.medication.name
    states = [genes[g] for g in advisory.genes if g in genes]
    if advisory.status in ("advisory", "context_review") and chosen is not None:
        value = f"CPIC ({chosen.classification}): {chosen.text}"
        if advisory.status == "context_review":
            value = f"Review with your listed medications. {value}"
        # Genes the recommendation doesn't depend on (it's the same for every
        # result of a weak call) don't set its label.
        used = [s for s in states if s.adequate]
        label = _worst(s.evidence for s in used) if used else NOT_ASSESSED
        reasons = sorted({r for s in used if s.evidence == label for r in s.reasons})
    elif advisory.status == "withheld":
        value = "Withheld: no single CPIC recommendation fits every possible result"
        label = variant_confidence.LOW
        reasons = sorted({*advisory.reasons, *(r for s in states for r in s.reasons)})
    else:
        value = advisory.status.replace("_", " ").capitalize()
        label, reasons = NOT_ASSESSED, []
    phenotypes = (
        ", ".join(f"{g}: {p}" for g, p in chosen.phenotypes.items()) if chosen else ""
    )
    guideline = cpic.guidelines.get(chosen.guideline_id) if chosen else None
    others = "; ".join(
        f"for {', '.join(r.phenotypes.values()) or 'another result'}: "
        f"{r.text} ({r.classification})"
        for r in advisory.alternatives
    )
    if others:
        others = (
            "Not guidance for this person; CPIC's recommendations if the "
            f"phenotype changes: {others}."
            if advisory.status == "context_review"
            else f"Possible CPIC recommendations, none established: {others}."
        )
    detail = " ".join(
        part
        for part in (
            f"For {phenotypes}." if phenotypes else "",
            *advisory.notes,
            others,
            f"Source: CPIC {guideline.name} guideline"
            + (f" (PMID {guideline.publication[1]})" if guideline.publication else "")
            + f", CPIC database {cpic.release}."
            if guideline
            else "",
        )
        if part
    )
    contraindication = bool(chosen and against(chosen.text))
    finding = _finding(
        "medication_review",
        f"{name} ({', '.join(advisory.genes)})" if advisory.genes else name,
        value,
        detail=detail or None,
        gene=",".join(advisory.genes) or None,
        confidence=label,
        reasons=reasons,
        basis="weakest evidence of the gene calls the CPIC recommendation needs",
        evidence=files[0] if states else None,
        region=f"gene {', '.join(advisory.genes)}" if advisory.genes else None,
        manifest=files[1] if states else None,
        disclaimer=NOT_PRESCRIPTION,
    )
    finding["advisory"] = ReportAdvisoryDocument(
        status=advisory.status,
        medication=advisory.medication.name,
        cpic_drug=advisory.drug.id if advisory.drug else None,
        population=advisory.medication.population,
        genes=advisory.genes,
        contraindication=contraindication,
        recommendation=_recommendation(chosen, cpic) if chosen else None,
        alternatives=[_recommendation(r, cpic) for r in advisory.alternatives],
        notes=advisory.notes,
    )
    return finding


def _gene_document(adjusted: GeneContext) -> ReportContextGeneDocument:
    state = adjusted.gene
    baseline = state.phenotype or state.describe()
    if adjusted.applied:
        text = (
            f"{baseline}; with {adjusted.trigger}: {_possible_text(adjusted)}"
            if adjusted.possible or adjusted.unclear
            else f"{baseline} (CPIC: {adjusted.trigger} has minimal effect)"
        )
    else:
        text = f"{baseline} (unchanged" + (
            f": {adjusted.note})"
            if adjusted.note
            else ": no listed medication has a CPIC-stated effect on it)"
        )
    return ReportContextGeneDocument(
        gene=state.gene,
        baseline=baseline,
        adjusted=text,
        possible=sorted(adjusted.possible),
        unclear=adjusted.unclear,
        rule=adjusted.rule.id if adjusted.rule else None,
        trigger=adjusted.trigger,
        quote=adjusted.quote,
        cpic_recommendation=adjusted.recommendation.id
        if adjusted.recommendation
        else None,
    )


def _gene_finding(
    document: ReportContextGeneDocument,
    state: PgxGene,
    files: tuple[str | None, str | None],
) -> ReportFindingDocument:
    detail = f"Genetic phenotype: {document['baseline']}."
    if document["quote"]:
        detail += (
            f" Rule {document['rule']} (CPIC recommendation "
            f"{document['cpic_recommendation']}): \u201c{document['quote']}\u201d"
        )
    return _finding(
        "medication_review",
        f"{state.gene} phenotype with listed medications",
        document["adjusted"],
        detail=detail,
        gene=state.gene,
        confidence=state.evidence,
        reasons=state.reasons,
        basis="the evidence of the gene's call (PharmCAT, or its outside caller)",
        evidence=files[0],
        region=f"gene {state.gene}",
        manifest=files[1],
        disclaimer=NOT_PRESCRIPTION,
    )
