"""Join modkit HP partitions and summarize the shared qualifying CpGs."""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path

from wgsextract_cli.core.methylation_annotations import Chromosome, Region
from wgsextract_cli.core.methylation_phases import IntervalLookup, PhaseEvidence
from wgsextract_cli.core.methylation_regions import (
    REGION_TABLE,
    Counts,
    Site,
    read_sites,
    region_context,
    region_identity,
    sites_in_regions,
)
from wgsextract_cli.core.table_writer import Column, Value
from wgsextract_cli.core.utils import WGSExtractError

HP_TABLE = (
    *REGION_TABLE,
    Column("partition", "TEXT", "combined, hp1, hp2 or unphased."),
    Column(
        "phase_status", "TEXT", "Evidence for interpreting HP labels within the region."
    ),
    Column("phase_set", "INTEGER", "Verified PS, when there is a single coherent set."),
    Column(
        "shared_qualified_cpgs",
        "INTEGER",
        "CpGs qualifying in both HP groups, on the combined row.",
    ),
    Column(
        "hp1_minus_hp2_pp",
        "REAL",
        "HP1 minus HP2 pooled percent on jointly qualifying CpGs in one phase set, on the combined row.",
    ),
)
PARTITIONS = ("combined", "hp1", "hp2", "unphased")


def _location(site: Site) -> tuple[str, int]:
    return site.chrom, site.start


def _by_position(
    sites: Iterable[Site],
) -> Iterator[tuple[tuple[str, int], dict[str, Site]]]:
    for location, group in groupby(sites, key=_location):
        codes: dict[str, Site] = {}
        for site in group:
            if site.code in codes:
                raise WGSExtractError(f"Duplicate modkit partition row at {location}.")
            codes[site.code] = site
        yield location, codes


def _phased_counts(
    site: Site, groups: Sequence[dict[str, Site]]
) -> tuple[Site, Site, Site, Site]:
    empty = site._replace(depth=0, modified=0)
    hp1, hp2 = (group.pop(site.code, empty) for group in groups)
    unphased = site._replace(
        depth=site.depth - hp1.depth - hp2.depth,
        modified=site.modified - hp1.modified - hp2.modified,
    )
    if any(
        member.end != site.end
        or member.strand != site.strand
        or not 0 <= member.modified <= member.depth
        for member in (hp1, hp2, unphased)
    ):
        raise WGSExtractError(
            f"Inconsistent modkit phased counts at {site.chrom}:{site.start} ({site.code})."
        )
    return site, hp1, hp2, unphased


@dataclass
class PartitionCounts:
    groups: tuple[Counts, ...] = field(
        default_factory=lambda: tuple(Counts() for _ in PARTITIONS)
    )
    shared_sites: int = 0
    shared_hp1_depth: int = 0
    shared_hp2_depth: int = 0
    shared_hp1_modified: int = 0
    shared_hp2_modified: int = 0

    def add(self, sites: tuple[Site, Site, Site, Site], minimum: int) -> None:
        for group, site in zip(self.groups, sites, strict=True):
            group.add(site, minimum)
        _, hp1, hp2, _ = sites
        if min(hp1.depth, hp2.depth) >= minimum:
            self.shared_sites += 1
            self.shared_hp1_depth += hp1.depth
            self.shared_hp2_depth += hp2.depth
            self.shared_hp1_modified += hp1.modified
            self.shared_hp2_modified += hp2.modified

    def difference(self, min_sites: int, phase_set: int | None) -> float | None:
        if phase_set is None or self.shared_sites < min_sites:
            return None
        return 100 * (
            self.shared_hp1_modified / self.shared_hp1_depth
            - self.shared_hp2_modified / self.shared_hp2_depth
        )


def _tally_partitions(
    inputs: tuple[Path, Path, Path],
    build: str,
    regions: Sequence[Region],
    aliases: Mapping[str, Chromosome],
    lengths: Mapping[str, int],
    anchors: Mapping[str, Sequence[int]],
    codes: Sequence[str],
    min_depth: int,
) -> tuple[dict[tuple[int, str], PartitionCounts], set[str]]:
    # Within a position, modkit need not emit modification codes in the same order.
    combined, hp1, hp2 = inputs
    streams = [_by_position(read_sites(path, build)) for path in (hp1, hp2)]
    pending = [next(stream, None) for stream in streams]
    seen_codes: set[str] = set()
    matches = sites_in_regions(
        read_sites(combined, build),
        regions,
        aliases,
        lengths,
        anchors,
        codes,
        seen_codes,
        include_outside=True,
    )
    tallies: dict[tuple[int, str], PartitionCounts] = {}
    for location, group in groupby(matches, key=lambda match: _location(match[0])):
        partitions: list[dict[str, Site]] = [{}, {}]
        for i, item in enumerate(pending):
            if item is not None and item[0] == location:
                partitions[i] = {
                    code: site for code, site in item[1].items() if code in codes
                }
                pending[i] = next(streams[i], None)
        for site, active in group:
            counts = _phased_counts(site, partitions)
            for _, index in active:
                key = index, site.code
                if key not in tallies:
                    tallies[key] = PartitionCounts()
                tallies[key].add(counts, min_depth)
        if any(partitions):
            raise WGSExtractError(f"Missing modkit combined counts at {location}.")
    if any(item is not None for item in pending):
        raise WGSExtractError(
            "modkit's HP partitions do not align with the combined CpG table."
        )
    return tallies, seen_codes


def haplotype_rows(
    inputs: tuple[Path, Path, Path],
    build: str,
    regions: Sequence[Region],
    aliases: Mapping[str, Chromosome],
    lengths: Mapping[str, int],
    anchors: Mapping[str, Sequence[int]],
    codes: Sequence[str],
    min_depth: int,
    min_sites: int,
    evidence: Mapping[int, PhaseEvidence],
    blocks: Mapping[str, IntervalLookup] | None,
) -> Iterator[tuple[Value, ...]]:
    tallies, seen_codes = _tally_partitions(
        inputs, build, regions, aliases, lengths, anchors, codes, min_depth
    )
    available = {chrom.name for chrom in aliases.values()}
    empty_counts, empty_phase = PartitionCounts(), PhaseEvidence()
    for index, region in enumerate(regions):
        expected, unavailable = region_context(region, available, anchors)
        phase_status, phase_set = evidence.get(index, empty_phase).classify(
            region, blocks
        )
        for code in codes:
            counts = tallies.get((index, code), empty_counts)
            reason = unavailable or (
                "modification_not_tabulated"
                if seen_codes and code not in seen_codes
                else None
            )
            for partition, group in zip(PARTITIONS, counts.groups, strict=True):
                combined = partition == "combined"
                yield (
                    *region_identity(region, code),
                    *group.values(min_sites, expected, reason),
                    partition,
                    phase_status,
                    phase_set,
                    counts.shared_sites if combined else None,
                    counts.difference(min_sites, phase_set) if combined else None,
                )
