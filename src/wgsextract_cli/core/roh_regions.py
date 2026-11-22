"""Imprinted regions for `qc roh`'s isodisomy review flag (#1174).

The bundled list (assets/roh/imprinted_regions.json) names each region by its
anchor genes; its coordinates are the span of those genes in the bundled
RefSeq gene table of the sample's build, so they always match the build. A
user's own BED must declare its build in a UCSC ``track``/``browser`` line
(``db=hg38``), and is refused for another build.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path

from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.errors import CodedError
from wgsextract_cli.core.panel import load_annotations
from wgsextract_cli.core.utils import WGSExtractError

ASSET = "assets/roh/imprinted_regions.json"
_DB = re.compile(r"\bdb=(\S+)")


@dataclass(frozen=True)
class Condition:
    name: str
    upd: str


@dataclass(frozen=True)
class ImprintedRegion:
    id: str
    band: str | None
    chrom: str  # as the build's gene table names it (chr15)
    start: int  # 0-based
    end: int  # exclusive
    genes: tuple[str, ...]
    conditions: tuple[Condition, ...]

    def overlaps(self, chrom: str, start: int, end: int) -> bool:
        """Whether 1-based inclusive ``start``-``end`` on ``chrom`` (any naming)
        overlaps the region."""
        return (
            bare(chrom) == bare(self.chrom) and start <= self.end and end > self.start
        )


@dataclass(frozen=True)
class RegionSet:
    build: str
    source: str  # what the coordinates come from
    path: str | None  # a user's BED, else None (the bundled list)
    regions: tuple[ImprintedRegion, ...]
    citations: tuple[str, ...]


def bare(chrom: str) -> str:
    return chrom[3:] if chrom.lower().startswith("chr") else chrom


@cache
def _curated() -> dict[str, object]:
    text = resources.files("wgsextract_cli").joinpath(ASSET).read_text("utf-8")
    data: dict[str, object] = json.loads(text)
    return data


def bundled(build: str) -> RegionSet:
    """The curated regions with coordinates from ``build``'s gene table."""
    notes = load_annotations(build)
    data = _curated()
    sources: dict[str, str] = data["sources"]  # type: ignore[assignment]
    regions = []
    for entry in data["regions"]:  # type: ignore[attr-defined]
        # A build's annotation may lack a gene (T2T's has no KCNQ1OT1); the
        # region then spans the anchor genes it has.
        genes = [g for g in entry["genes"] if g.upper() in notes.loci]
        loci = [locus for gene in genes for locus in notes.loci[gene.upper()]]
        chroms = {locus.chrom for locus in loci}
        if len(chroms) != 1:
            raise WGSExtractError(
                f"Imprinted region {entry['id']}: its genes are on {sorted(chroms)} "
                f"in the {build} gene table."
            )
        regions.append(
            ImprintedRegion(
                id=entry["id"],
                band=entry["band"],
                chrom=chroms.pop(),
                start=min(locus.start for locus in loci),
                end=max(locus.end for locus in loci),
                genes=tuple(genes),
                conditions=tuple(Condition(**c) for c in entry["conditions"]),
            )
        )
    cited = sorted({key for entry in data["regions"] for key in entry["sources"]})  # type: ignore[attr-defined]
    return RegionSet(
        build=build,
        source=f"anchor-gene spans, {notes.source} ({ASSET})",
        path=None,
        regions=tuple(regions),
        citations=tuple(sources[key] for key in cited),
    )


def from_bed(path: Path, build: str) -> RegionSet:
    """A user's BED of imprinted regions: chrom, start, end, then optional name
    and conditions (``;``-separated). Its ``track``/``browser`` line must give
    the build (``db=hg38``), which must be ``build``."""
    declared: str | None = None
    regions = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise CodedError(
            "roh_regions_missing", f"--imprinted-regions {path}: {e.strerror or e}"
        ) from e
    for number, line in enumerate(lines, 1):
        if line.startswith(("track", "browser", "#")):
            if found := _DB.search(line):
                declared = found[1]
            continue
        if not line.strip():
            continue
        fields = line.rstrip("\n").split("\t")
        try:
            chrom, start, end = fields[0], int(fields[1]), int(fields[2])
        except (IndexError, ValueError):
            chrom, start, end = "", 0, 0
        if not chrom or not 0 <= start < end:
            raise CodedError(
                "roh_regions_unreadable",
                f"--imprinted-regions {path} line {number} isn't a BED interval "
                "(chrom, 0-based start, end after start, name, conditions).",
            )
        name = (
            fields[3] if len(fields) > 3 and fields[3] else f"{chrom}:{start + 1}-{end}"
        )
        conditions = tuple(
            Condition(text.strip(), "")
            for text in (fields[4] if len(fields) > 4 else "").split(";")
            if text.strip()
        )
        regions.append(ImprintedRegion(name, None, chrom, start, end, (), conditions))
    found_build = canonical_build(declared)
    if found_build is None:
        raise CodedError(
            "roh_regions_build_unknown",
            f"--imprinted-regions {path} doesn't say which build its coordinates "
            "are on; add a first line such as 'track name=imprinted db=hg38'.",
        )
    if found_build != build:
        raise CodedError(
            "roh_regions_build_mismatch",
            f"--imprinted-regions {path} is for {declared} but the sample is "
            f"{build}; use intervals on the sample's build (or lift them over).",
        )
    return RegionSet(
        found_build, f"user BED {path.name}", str(path), tuple(regions), ()
    )
