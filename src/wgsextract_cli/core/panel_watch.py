"""Read watched loci with lookup evidence and compare local allele patterns."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations_with_replacement
from typing import Any

from wgsextract_cli.core.csq_alleles import split_origin
from wgsextract_cli.core.lookup import genotypes
from wgsextract_cli.core.lookup.identifiers import Locus, contig_key
from wgsextract_cli.core.panel_spec import parse_position
from wgsextract_cli.core.variant_confidence import Thresholds


class Watch:
    def __init__(
        self,
        source: genotypes.VcfSource,
        evidence: genotypes.Evidence | None,
        gvcf: genotypes.VcfSource | None,
        thresholds: Thresholds,
    ) -> None:
        self.source = source
        self.evidence = evidence
        self.gvcf = gvcf
        self.thresholds = thresholds
        self.cache: dict[str, dict[str, Any]] = {}

    def at(self, text: str) -> dict[str, Any]:
        if text in self.cache:
            return self.cache[text]
        pos = parse_position(text)
        locus = Locus(
            pos.build, pos.chrom, pos.pos, pos.pos + len(pos.ref) - 1, pos.ref, pos.alt
        )
        result: dict[str, Any] = genotypes.site_result(
            self.source,
            self.evidence,
            locus,
            self.thresholds,
            self.gvcf,
            include_split_origin=True,
        )
        result["watch_locus"] = text
        calls = result["calls"]
        # An overlapping record is not necessarily a genotype for this exact
        # REF interval (e.g. a deletion anchored outside the watched site).
        if (
            len(calls) > 1
            and result["reference_evidence_state"] != "low_quality_variant"
            and (combined := _combine_split(calls, locus)) is not None
        ):
            alleles = combined["alleles"]
            result.update(
                status=genotypes.CONFIDENT_CALL,
                evidence_state="variant",
                reason="The original genotype is supported by the split records.",
                genotype=combined["genotype"],
                alleles=alleles,
                confidence=combined["confidence"],
                phase_call=combined,
            )
            if pos.alt is not None:
                result["query_allele_copies"] = alleles.count(pos.alt)
        elif calls and (
            len(calls) != 1 or calls[0]["pos"] != pos.pos or calls[0]["ref"] != pos.ref
        ):
            result.update(
                status=genotypes.NOT_ASSESSED,
                reason="Overlapping records do not identify one exact locus genotype.",
                genotype=None,
                alleles=None,
                query_allele_copies=None,
            )
        self.cache[text] = result
        return result

    def haplotype(self, specification: Mapping[str, Any]) -> dict[str, Any]:
        sites = [self.at(site) for site in specification["sites"]]
        known = {genotypes.CONFIDENT_CALL, genotypes.CONFIDENT_REFERENCE}
        missing = [
            loc
            for loc, site in zip(specification["sites"], sites, strict=True)
            if site["status"] not in known
            or not site["alleles"]
            or not all(isinstance(a, str) for a in site["alleles"])
        ]
        base = {
            "id": specification["id"],
            "gene": specification["gene"],
            "sites": sites,
        }
        if missing:
            return {
                **base,
                "status": "not_assessed" if len(missing) == len(sites) else "partial",
                "missing_sites": missing,
            }
        ploidy = {len(site["alleles"]) for site in sites}
        alleles: dict[str, list[str]] = specification["alleles"]
        if ploidy == {1}:
            observed = [site["alleles"][0] for site in sites]
            names = [name for name, bases in alleles.items() if bases == observed]
            return {**base, **_resolved(names, "haploid")}
        if ploidy != {2}:
            return {**base, "status": "not_assessed", "reason": "Inconsistent ploidy."}
        blocks = [_phase_block(i, site) for i, site in enumerate(sites)]
        informative = {block for block in blocks if block is not None}
        if len(informative) <= 1:
            observed = [[site["alleles"][i] for site in sites] for i in (0, 1)]
            by_bases = {tuple(bases): name for name, bases in alleles.items()}
            matched = [by_bases.get(tuple(bases)) for bases in observed]
            basis = (
                "observed_phase"
                if len(set(map(tuple, observed))) == 2
                and (informative and next(iter(informative))[0] == "phase")
                else "genotype"
            )
            return {**base, **_resolved(sorted(x for x in matched if x), basis, 2)}
        candidates = [
            [left, right]
            for left, right in combinations_with_replacement(sorted(alleles), 2)
            if _compatible(alleles[left], alleles[right], sites, blocks)
        ]
        return {
            **base,
            "status": "ambiguous_phase",
            "candidates": candidates,
            "reason": "Phase between informative sites is unknown; unnamed patterns are possible.",
        }


def _resolved(names: list[str], basis: str, ploidy: int = 1) -> dict[str, Any]:
    if len(names) != ploidy:
        return {"status": "unmatched", "phase_basis": basis}
    return {"status": "called", "diplotype": names, "phase_basis": basis}


def _phase_block(index: int, site: Mapping[str, Any]) -> tuple[str, str] | None:
    if len(set(site["alleles"])) == 1:
        return None
    call = site.get("phase_call") or site["calls"][0]
    if call.get("phased") and (phase_set := call.get("phase_set")):
        return "phase", f"{call['source']}:{phase_set}"
    return "site", str(index)


def _combine_split(
    calls: Sequence[Mapping[str, Any]], locus: Locus
) -> dict[str, Any] | None:
    """Reassemble a simple exact-locus split only with complete provenance."""
    copies: list[tuple[int, Mapping[str, Any]]] = []
    originals: set[str] = set()
    sources = {call["source"] for call in calls}
    if len(sources) != 1:
        return None
    for call in calls:
        raw = call.get("split_origin")
        if not isinstance(raw, str) or (original := split_origin(raw)) is None:
            return None
        _, alts, index = original
        chrom, pos, ref, _ = raw.rsplit("|", 1)[0].split("|")
        originals.add(raw.rsplit("|", 1)[0])
        if (
            (contig_key(chrom), pos, ref)
            != (contig_key(locus.chrom), str(locus.start), locus.ref)
            or call["pos"] != locus.start
            or call["ref"] != locus.ref
            or call["alts"] != [alts[index - 1]]
            or call["state"] not in ("variant", "reference_call")
            or call["confidence"] not in ("high", "moderate")
            or len(call["alleles"]) != 2
            or not set(call["alleles"]) <= {locus.ref, alts[index - 1]}
        ):
            return None
        copies.append((index, call))
    if len(originals) != 1 or sorted(i for i, _ in copies) != list(
        range(1, len(alts) + 1)
    ):
        return None
    phase_sets = {call["phase_set"] for _, call in copies}
    phased = all(call["phased"] for _, call in copies) and len(phase_sets) == 1
    nonref = [
        (copy, index)
        for index, call in copies
        for copy, base in enumerate(call["alleles"])
        if base != locus.ref
    ]
    if not nonref or len(nonref) > 2:
        return None
    if phased:
        assignment = dict(nonref)
        if len(assignment) != len(nonref):
            return None
        gt = [assignment.get(i, 0) for i in range(2)]
    else:
        gt = sorted([i for _, i in nonref] + [0] * (2 - len(nonref)))
    return {
        "alleles": [([locus.ref, *alts])[i] for i in gt],
        "genotype": ("|" if phased else "/").join(map(str, gt)),
        "phased": phased,
        "phase_set": next(iter(phase_sets)) if phased else None,
        "source": next(iter(sources)),
        "confidence": "high"
        if all(call["confidence"] == "high" for call in calls)
        else "moderate",
    }


def _compatible(
    left: Sequence[str],
    right: Sequence[str],
    sites: Sequence[Mapping[str, Any]],
    blocks: Sequence[tuple[str, str] | None],
) -> bool:
    orientation: dict[tuple[str, str], bool] = {}
    for a, b, site, block in zip(left, right, sites, blocks, strict=True):
        x, y = site["alleles"]
        if (a, b) not in ((x, y), (y, x)):
            return False
        if block is not None:
            forward = (a, b) == (x, y)
            if orientation.setdefault(block, forward) != forward:
                return False
    return True
