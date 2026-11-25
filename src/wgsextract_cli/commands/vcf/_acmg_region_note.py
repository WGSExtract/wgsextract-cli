"""Look up available gnomAD masks for an ACMG VCF's classified records."""

from __future__ import annotations

import gzip
from array import array
from bisect import bisect_right
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from wgsextract_cli.core.acmg_regions import REGION_FLAGS, region_name
from wgsextract_cli.core.acmg_sites import SiteDatabase, contig_key

REGION_INFO = "ACMG_GNOMAD_REGION"
REGION_HEADER = (
    f'##INFO=<ID={REGION_INFO},Number=1,Type=String,Description="Research only: '
    "gnomAD mask at input POS (lcr, segdup, or lcr&segdup). fastVEP withholds "
    'population evidence when the matched gnomAD allele is flagged; this is a locus note">'
)


def region_coverage(
    gnomad: Iterable[tuple[Path, Iterable[str]]],
    site_dbs: list[SiteDatabase],
    manifest: dict[str, Any],
    contigs: list[str],
    tracks: dict[str, str],
) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """Missing evaluation, and where a local track matches the loaded databases."""
    library = {
        e["file"]: e for e in manifest.get("databases", {}).values() if e.get("file")
    }
    run = {
        p.name: db.meta for db in site_dbs for p in db.paths if db.source == "gnomad"
    }
    covered: dict[str, set[str]] = {}
    matching: dict[str, set[str]] = {}
    wanted = {contig_key(c) for c in contigs}
    for path, chromosomes in gnomad:
        entry = run.get(path.name)
        if entry is None:
            entry = library.get(path.name, {})
            if entry.get("bytes") != path.stat().st_size:
                entry = {}
        digests = entry.get("region_tracks", {})
        flags = set(digests)
        matched = {flag for flag in tracks if tracks[flag] == digests.get(flag)}
        for chrom in chromosomes:
            key = contig_key(chrom)
            if key not in wanted:
                continue
            covered[key] = covered.get(key, flags) & flags
            matching[key] = matching.get(key, matched) & matched
    missing = {
        flag: absent
        for flag in REGION_FLAGS
        if (
            absent := [c for c in contigs if flag not in covered.get(contig_key(c), ())]
        )
    }
    return missing, matching


class RegionNotes:
    """Compact interval sets: one sorted start and end array per mask/contig."""

    def __init__(
        self,
        folder: Path,
        matching: dict[str, set[str]],
    ) -> None:
        self.masks: dict[str, dict[str, tuple[array[int], array[int]]]] = {}
        for flag in REGION_FLAGS:
            wanted = {chrom for chrom, flags in matching.items() if flag in flags}
            if not wanted:
                continue
            track = {c: (array("I"), array("I")) for c in wanted}
            with gzip.open(folder / region_name(flag), "rt", encoding="ascii") as bed:
                for line in bed:
                    chrom, lo, hi = line.split()
                    if (intervals := track.get(chrom.removeprefix("chr"))) is None:
                        continue
                    starts, ends = intervals
                    start, end = int(lo), int(hi)
                    if ends and start <= ends[-1]:
                        ends[-1] = max(ends[-1], end)
                    else:
                        starts.append(start)
                        ends.append(end)
            for key, intervals in track.items():
                if intervals[0]:
                    self.masks.setdefault(key, {})[flag] = intervals

    def at(self, chrom: str, pos: str) -> str:
        masks = self.masks.get(contig_key(chrom))
        if not masks:
            return ""
        index = int(pos) - 1
        flags = []
        for flag, (starts, ends) in masks.items():
            row = bisect_right(starts, index) - 1
            if row >= 0 and index < ends[row]:
                flags.append(flag)
        return "&".join(flags)
