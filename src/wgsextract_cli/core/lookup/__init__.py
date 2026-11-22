"""``lookup``: one sample's genotype, evidence state and existing results at
an rsID, a build-qualified locus or a gene, read from local files only.

The answer joins the input VCF (variants-only or gVCF), its reference
evidence (an explicit ``--gvcf``, the input itself, or the gVCF its run's
manifest records) and the sample's personal report. Identifiers are resolved
against installed resources (``identifiers``); nothing is sent anywhere.
See docs/lookup.md.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core import variant_confidence as vc
from wgsextract_cli.core.builds import (
    build_from_chr1_length,
    build_from_reference_header,
)
from wgsextract_cli.core.reference_evidence import (
    ReferenceEvidence,
    companion,
)
from wgsextract_cli.core.utils import UsageError

from . import genotypes, interpretation
from .identifiers import (
    DBSNP_RELEASE,
    DBSNP_TERMS,
    Locus,
    build_name,
    gene_resource,
    genes_at,
    parse_locus,
    placements_path,
    resolve_gene,
    resolve_rsid,
)

SCHEMA = "wgsextract.lookup/1"
GUIDE = "docs/lookup.md"
DEFAULT_LIMIT = 50
MAX_LIMIT = 1000
PRIVACY = "Read from local files only; nothing was sent over the network."
_RSID_HISTORY = "assets/microarray/rsid/dbsnp157.tsv.gz"


@dataclass(frozen=True)
class Request:
    input: Path
    thresholds: vc.Thresholds
    reflib: str
    rsid: str | None = None
    locus: str | None = None
    gene: str | None = None
    sample: str | None = None
    gvcf: Path | None = None
    report: Path | None = None
    build: str | None = None
    limit: int = DEFAULT_LIMIT
    cursor: str | None = None


def input_build(source: genotypes.VcfSource, given: str | None) -> str:
    """The input's build from its header (chr1's length, else ##reference),
    checked against ``given``; ``given`` when the header can't tell."""
    detected = build_name(build_from_chr1_length(source.lengths))
    if detected is None:
        detected = next(
            (
                build_name(build_from_reference_header(line.split("=", 1)[1]))
                for line in source.header.splitlines()
                if line.startswith("##reference=")
            ),
            None,
        )
    stated = build_name(given) if given else None
    if given and stated is None:
        raise UsageError(f"--build {given!r} isn't GRCh37, GRCh38 or CHM13v2.0.")
    if stated and detected and stated != detected:
        raise UsageError(
            f"--build {stated} doesn't match {source.path}, whose header is "
            f"{detected}; lookup never converts coordinates between builds."
        )
    build = stated or detected
    if build is None:
        raise UsageError(
            f"Can't tell which build {source.path} is on (its header has no "
            "chr1 length or ##reference); give --build."
        )
    return build


@dataclass
class _Target:
    locus: Locus | None
    region: bool
    genes: list[str]
    resolution: dict[str, object]
    resources: list[dict[str, object]]


def _ambiguous(
    resolution: dict[str, object], candidates: list[dict[str, object]], how: str
) -> None:
    resolution.update(
        status="ambiguous",
        candidates=candidates,
        message=f"Several matches; nothing was chosen for you. {how}",
    )


def _selected(chosen: Locus, given: Locus | None) -> Locus:
    if given is None:
        return chosen
    return Locus(
        chosen.build,
        chosen.chrom,
        max(chosen.start, given.start),
        min(chosen.end, given.end),
        given.ref,
        given.alt,
    )


def _resolve(request: Request, build: str) -> _Target:
    given = parse_locus(request.locus) if request.locus else None
    if given is not None and given.build != build:
        raise UsageError(
            f"--locus {given.text()} is on {given.build}, but the input is on "
            f"{build}; lookup never converts coordinates between builds. Give "
            f"the {build} position."
        )
    if request.rsid:
        found = resolve_rsid(request.rsid, build, request.reflib)
        path = placements_path(request.reflib, build)
        resources: list[dict[str, object]] = [
            {
                "name": "dbsnp_rsid_history",
                "release": DBSNP_RELEASE,
                "terms": DBSNP_TERMS,
                "resource": _RSID_HISTORY,
            },
            {
                "name": "rsid_placements",
                "path": str(path),
                "description": "rsID positions of consumer genotyping chips "
                "(ref microarray-targets); not all of dbSNP",
            },
        ]
        placements = [
            {"locus": Locus(build, p.chrom, p.pos, p.pos).text(), "ids": list(p.ids)}
            for p in found.placements
        ]
        resolution: dict[str, object] = {
            "kind": "rsid",
            "query": found.rsid,
            "current": found.current,
            "history": found.history,
            "aliases": list(found.aliases),
            "placements": placements,
            "status": "resolved",
        }
        loci = [Locus(build, p.chrom, p.pos, p.pos) for p in found.placements]
        if given is not None:
            matches = [c for c in loci if c.overlaps(given) and c.start == given.start]
            if not matches:
                raise UsageError(
                    f"--locus {given.text()} isn't a placement of {found.rsid}; "
                    f"its placements: {', '.join(c.text() for c in loci)}."
                )
            locus = _selected(matches[0], given)
        elif len(loci) > 1 or found.history in ("ambiguous", "unresolved"):
            why = (
                f"dbSNP's merge history of {found.rsid} is {found.history}"
                if found.history in ("ambiguous", "unresolved")
                else f"{found.rsid} has {len(loci)} placements on {build}"
            )
            _ambiguous(
                resolution,
                [{"locus": c.text()} for c in loci],
                f"{why}; choose one with --locus.",
            )
            return _Target(None, False, [], resolution, resources)
        else:
            locus = loci[0]
        return _Target(locus, False, genes_at(locus), resolution, resources)
    if request.gene:
        gene = resolve_gene(request.gene, build)
        resources = [_gene_resource(build)]
        resolution = {
            "kind": "gene",
            "query": request.gene,
            "symbols": list(gene.symbols),
            "loci": [{"symbol": s, "locus": c.text()} for s, c in gene.loci],
            "status": "resolved",
        }
        chosen = [(s, c) for s, c in gene.loci if given is None or c.overlaps(given)]
        if not chosen:
            assert given is not None
            raise UsageError(
                f"--locus {given.text()} isn't in {request.gene}: "
                f"{', '.join(c.text() for _, c in gene.loci)}."
            )
        if len(chosen) > 1:
            how = (
                f"{request.gene} is an alias of {', '.join(gene.symbols)}; "
                "choose with --gene SYMBOL"
                if len(gene.symbols) > 1
                else f"{gene.symbols[0]} has {len(chosen)} placements on {build}"
            )
            _ambiguous(
                resolution,
                [{"symbol": s, "locus": c.text()} for s, c in chosen],
                f"{how}; or narrow it with --locus.",
            )
            return _Target(None, True, [], resolution, resources)
        symbol, span = chosen[0]
        resolution["symbol"] = symbol
        return _Target(_selected(span, given), True, [symbol], resolution, resources)
    if given is None:
        raise UsageError("Give --rsid, --locus or --gene.")
    resolution = {"kind": "locus", "query": given.text(), "status": "resolved"}
    return _Target(given, given.is_range, genes_at(given), resolution, [])


def _evidence(
    request: Request, source: genotypes.VcfSource, locus: Locus, work: Path
) -> tuple[genotypes.Evidence | None, genotypes.VcfSource | None, str | None]:
    """The reference evidence for the sample, its records' source, and why
    none was used when one was found but can't serve."""
    if request.gvcf is not None:
        path, origin = str(request.gvcf.expanduser()), "explicit"
    elif source.is_gvcf:
        path, origin = source.path, "input"
    else:
        found = companion(source.path)
        if found is None:
            return None, None, None
        path, origin = found, "manifest"
    if origin == "input":
        gvcf = source
    else:
        try:
            gvcf = genotypes.open_vcf(path, source.sample, option="--gvcf")
        except UsageError as e:
            if origin == "explicit":
                raise
            return None, None, f"{path}: {e}"
    if (a := build_from_chr1_length(gvcf.lengths)) and (
        b := build_from_chr1_length(source.lengths)
    ):
        if a != b:
            raise UsageError(
                f"The reference evidence {path} is on another build than {source.path}."
            )
    usable = genotypes.evidence_file(gvcf, locus, work)
    return (
        genotypes.Evidence(ReferenceEvidence(usable), path, origin),
        gvcf,
        None,
    )


def _gene_resource(build: str) -> dict[str, object]:
    note = gene_resource(build)
    return {
        "name": "gene_annotation",
        "release": note.release,
        "licence": note.licence,
        "source": note.source,
        "resource": note.resource,
    }


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not re.fullmatch(r"[0-9]{1,12}", cursor):
        raise UsageError("--cursor must be a next_cursor value from a lookup.")
    return int(cursor)


def lookup(request: Request) -> dict[str, object]:
    """The ``wgsextract.lookup/1`` document for ``request``."""
    if not 1 <= request.limit <= MAX_LIMIT:
        raise UsageError(f"--limit must be 1 to {MAX_LIMIT}.")
    offset = _offset(request.cursor)
    source = genotypes.open_vcf(str(request.input.expanduser()), request.sample)
    build = input_build(source, request.build)
    target = _resolve(request, build)
    document: dict[str, object] = {
        "schema": SCHEMA,
        "query": {
            k: v
            for k, v in (
                ("rsid", request.rsid),
                ("locus", request.locus),
                ("gene", request.gene),
            )
            if v
        },
        "build": build,
        "input": {
            "path": source.path,
            "sample": source.sample,
            "kind": "gvcf" if source.is_gvcf else "vcf",
        },
        "resolution": target.resolution,
    }
    note = None
    if target.locus is not None:
        with tempfile.TemporaryDirectory(prefix="wgse_lookup_") as work:
            evidence, gvcf, note = _evidence(request, source, target.locus, Path(work))
            document["reference_evidence"] = (
                {"path": evidence.path, "origin": evidence.origin} if evidence else None
            )
            if target.region:
                document["region"] = genotypes.region_result(
                    source,
                    evidence,
                    target.locus,
                    request.thresholds,
                    limit=request.limit,
                    offset=offset,
                )
            else:
                document["site"] = genotypes.site_result(
                    source, evidence, target.locus, request.thresholds, gvcf
                )
        if target.genes and not request.gene:
            document["genes"] = target.genes
            target.resources.append(_gene_resource(build))
        document["interpretation"] = interpretation.join(
            request.report,
            request.input,
            [target.locus],
            target.genes,
            whole_genes=bool(request.gene),
            build=build,
            first_sample=source.first_sample,
        )
    if note:
        document["reference_evidence_unused"] = note
    document["resources"] = target.resources
    document["thresholds"] = {
        "min_depth": request.thresholds.min_depth,
        "high_depth": request.thresholds.high_depth,
        "min_gq": request.thresholds.min_gq,
        "high_gq": request.thresholds.high_gq,
    }
    document["disclaimer"] = vc.DISCLAIMER
    document["privacy"] = PRIVACY
    document["guide"] = GUIDE
    return document
