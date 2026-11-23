"""Read strand-combined bedMethyl and tally it against CpG anchors."""

from __future__ import annotations

import gzip
from array import array
from bisect import bisect_left
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from statistics import median
from typing import NamedTuple

from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.methylation_annotations import Chromosome, Region
from wgsextract_cli.core.table_writer import Column, Value, require_pyarrow
from wgsextract_cli.core.utils import UsageError, run_command

REGION_TABLE = (
    Column("region", "TEXT", "Gene symbol or CpG island identifier."),
    Column("chrom", "TEXT", "Primary chromosome in the annotation."),
    Column("start", "INTEGER", "0-based start, inclusive."),
    Column("end", "INTEGER", "0-based end, exclusive."),
    Column("gene_id", "TEXT", "NCBI Gene ID for promoters."),
    Column(
        "transcript",
        "TEXT",
        "Selected RefSeq accession or GFF transcript ID for promoters.",
    ),
    Column("strand", "TEXT", "Transcript strand for promoters."),
    Column("mod_code", "TEXT", "m for 5mC, h for 5hmC."),
    Column("status", "TEXT", "Assessed, or reason methylation could not be assessed."),
    Column("expected_cpgs", "INTEGER", "Reference CpG C anchors in the region."),
    Column("observed_cpgs", "INTEGER", "CpGs with at least one valid call."),
    Column("qualified_cpgs", "INTEGER", "CpGs meeting the valid-call threshold."),
    Column("qualified_fraction", "REAL", "Fraction of expected CpGs that qualify."),
    Column("observed_valid_calls", "INTEGER", "Valid calls at all observed CpGs."),
    Column("valid_calls", "INTEGER", "Valid calls at qualifying CpGs."),
    Column("modified_calls", "INTEGER", "Modified calls at qualifying CpGs."),
    Column("median_valid_depth", "REAL", "Median valid calls per qualifying CpG."),
    Column("pooled_percent", "REAL", "Percent modified, weighting CpGs by depth."),
    Column("mean_percent", "REAL", "Unweighted mean percent across qualifying CpGs."),
    Column(
        "median_percent", "REAL", "Unweighted median percent across qualifying CpGs."
    ),
)


class Site(NamedTuple):
    chrom: str
    start: int
    end: int
    code: str
    strand: str
    depth: int
    modified: int


def read_sites(path: Path, build: str) -> Iterator[Site]:
    """Read modkit's extended counts; percentages in the input may be rounded."""
    if path.suffix.lower() == ".parquet":
        pq = require_pyarrow()
        columns = "chrom start end mod_code strand n_valid_cov n_mod".split()
        with pq.ParquetFile(path) as table:
            metadata = table.schema_arrow.metadata or {}
            declared = metadata.get(b"reference_build", b"").decode()
            if declared and canonical_build(declared) != build:
                raise UsageError(f"bedMethyl declares {declared}, not --build {build}.")
            if metadata.get(b"context", b"cpg") != b"cpg":
                raise UsageError("bedMethyl metadata must declare CpG context.")
            if not set(columns) <= set(table.schema_arrow.names):
                raise UsageError(
                    "Parquet input is missing the extended bedMethyl columns."
                )
            for batch in table.iter_batches(columns=columns):
                values = [batch.column(name).to_pylist() for name in columns]
                for row in zip(*values, strict=True):
                    if any(value is None for value in row):
                        raise UsageError(
                            "Parquet bedMethyl has nulls in required columns."
                        )
                    yield Site(*row)
        return
    opener = gzip.open if path.suffix.lower() in (".gz", ".bgz", ".bgzf") else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip() or line.startswith(("#", "track ", "browser ")):
                continue
            f = line.split(maxsplit=14)
            try:
                yield Site(
                    f[0], int(f[1]), int(f[2]), f[3], f[5], int(f[9]), int(f[11])
                )
            except (IndexError, ValueError) as exc:
                raise UsageError(
                    f"Invalid extended bedMethyl at line {number}."
                ) from exc


def resolve_contigs(
    specs: Sequence[Chromosome],
    lengths: Mapping[str, int],
    build: str,
    primary_accession_stems: set[str],
) -> tuple[dict[str, Chromosome], dict[str, str]]:
    """Validate primary aliases against the assembly report and FASTA lengths."""
    aliases: dict[str, Chromosome] = {}
    reference: dict[str, str] = {}
    names = {name for chrom in specs for name in chrom.aliases}
    for name in lengths:
        if name not in names and name.partition(".")[0] in primary_accession_stems:
            raise UsageError(
                f"--ref does not match {build}: incompatible primary accession {name}."
            )
    for chrom in specs:
        matches = [name for name in chrom.aliases if name in lengths]
        for name in matches:
            if lengths[name] != chrom.length:
                raise UsageError(
                    f"--ref does not match {build}: {name} has length {lengths[name]}, "
                    f"expected {chrom.length}."
                )
        if len(matches) > 1:
            raise UsageError(
                f"Ambiguous --ref aliases for {chrom.name}: {', '.join(matches)}."
            )
        if matches:
            reference[chrom.name] = matches[0]
            aliases.update((name, chrom) for name in chrom.aliases)
    if not reference:
        raise UsageError(f"--ref has no contig matching the {build} assembly report.")
    return aliases, reference


def merged_spans(regions: Iterable[Region]) -> Iterator[tuple[int, int]]:
    """Merge positions for I/O; callers group by chromosome first."""
    start, end = 0, 0
    bounds = sorted(
        (r.start, r.end)
        for r in regions
        if r.start is not None and r.end is not None and r.start < r.end
    )
    for left, right in bounds:
        if left > end:
            if end > start:
                yield start, end
            start, end = left, right
        else:
            end = max(end, right)
    if end > start:
        yield start, end


def reference_cpgs(
    fasta: Path,
    names: Mapping[str, str],
    lengths: Mapping[str, int],
    regions: Sequence[Region],
    work: Path,
) -> dict[str, array[int]]:
    """Query merged target spans in chunks, looking one base past each span."""
    queries: dict[str, tuple[str, int, int, int]] = {}
    for chrom, group in groupby(
        sorted(regions, key=lambda r: r.chrom), key=lambda r: r.chrom
    ):
        if chrom not in names:
            continue
        name = names[chrom]
        if ":" in name:
            raise UsageError(
                f"Unsupported FASTA contig name for indexed lookup: {name}"
            )
        length = lengths[name]
        chunk_size = 4 << 20
        for start, end in merged_spans(group):
            for left in range(start, end, chunk_size):
                right = min(left + chunk_size, end)
                padded = min(right + 1, length)
                queries[f"{name}:{left + 1}-{padded}"] = chrom, left, right, padded
    positions: dict[str, array[int]] = {}
    if not queries:
        return positions
    query_path = work / "reference-regions.txt"
    query_path.write_text("\n".join(queries) + "\n", encoding="utf-8", newline="\n")
    sequences = work / "reference-regions.fa"
    with sequences.open("wb") as output:
        run_command(["samtools", "faidx", "-r", query_path, fasta], stdout=output)
    with sequences.open("rb") as output:
        lines = groupby(output, lambda line: line.startswith(b">"))
        for query, (chrom, start, end, padded) in queries.items():
            checkpoint()
            header = next(lines, None)
            found = (
                [line.rstrip(b"\r\n") for line in header[1]]
                if header and header[0]
                else []
            )
            seq = next(lines, None)
            if found != [b">" + query.encode()] or not seq or seq[0]:
                raise UsageError(
                    f"Could not verify FASTA bases for {query}. Check the FASTA index."
                )
            bases = b"".join(line.strip() for line in seq[1]).upper()
            if len(bases) != padded - start:
                raise UsageError(
                    f"Unexpected FASTA sequence length for {query}. Check the FASTA index."
                )
            anchors = positions.setdefault(chrom, array("I"))
            offset = bases.find(b"CG")
            while 0 <= offset < end - start:
                anchors.append(start + offset)
                offset = bases.find(b"CG", offset + 2)
    return positions


@dataclass
class Counts:
    observed: int = 0
    observed_calls: int = 0
    valid_calls: int = 0
    modified: int = 0
    percentages: array[float] = field(default_factory=lambda: array("d"))
    depths: array[int] = field(default_factory=lambda: array("Q"))

    def add(self, site: Site, depth: int) -> None:
        self.observed += site.depth > 0
        self.observed_calls += site.depth
        if site.depth >= depth:
            self.valid_calls += site.depth
            self.modified += site.modified
            self.percentages.append(100 * site.modified / site.depth)
            self.depths.append(site.depth)

    def values(
        self, minimum: int, expected: int | None, reason: str | None
    ) -> tuple[Value, ...]:
        qualified = len(self.percentages)
        status = reason or (
            "no_reference_cpgs"
            if not expected
            else "no_valid_calls"
            if not self.observed
            else "insufficient_coverage"
            if qualified < minimum
            else "assessed"
        )
        summary: tuple[Value, ...] = (None, None, None)
        if status == "assessed":
            summary = (
                100 * self.modified / self.valid_calls,
                sum(self.percentages) / qualified,
                median(self.percentages),
            )
        return (
            status,
            expected,
            self.observed,
            qualified,
            qualified / expected if expected else None,
            self.observed_calls,
            self.valid_calls,
            self.modified,
            median(self.depths) if qualified else None,
            *summary,
        )


def sites_in_regions(
    sites: Iterable[Site],
    regions: Sequence[Region],
    aliases: Mapping[str, Chromosome],
    lengths: Mapping[str, int],
    anchors: Mapping[str, Sequence[int]],
    codes: Sequence[str],
    seen_codes: set[str],
    *,
    include_outside: bool = False,
) -> Iterator[tuple[Site, list[tuple[int, int]]]]:
    """Validate a sorted site stream and find all containing regions in one sweep."""
    by_chrom: dict[str, list[tuple[int, int, int]]] = {}
    for index, region in enumerate(regions):
        if region.start is not None and region.end is not None:
            by_chrom.setdefault(region.chrom, []).append(
                (region.start, region.end, index)
            )
    for bounds in by_chrom.values():
        bounds.sort()
    seen_contigs: set[str] = set()
    previous = ""
    last_position = -1
    site_codes: set[str] = set()
    cpgs: Sequence[int] = ()
    active: list[tuple[int, int]] = []
    bounds = []
    next_region = 0
    for n, site in enumerate(sites):
        if n % 100_000 == 0:
            checkpoint()
        chrom = aliases.get(site.chrom)
        length = chrom.length if chrom else lengths.get(site.chrom)
        location = f"{site.chrom}:{site.start}"
        if length is None:
            raise UsageError(
                f"bedMethyl contig {site.chrom} has no match in --ref for this build."
            )
        if not 0 <= site.start < site.end <= length or site.end != site.start + 1:
            raise UsageError(
                f"Expected a single-base bedMethyl interval at {location}."
            )
        if site.strand != ".":
            raise UsageError(
                f"Expected strand-combined CpG bedMethyl at {location} (strand '.')."
            )
        if not 0 <= site.modified <= site.depth:
            raise UsageError(f"Invalid modified/valid call counts at {location}.")
        if site.chrom != previous:
            canonical = chrom.name if chrom else site.chrom
            if canonical in seen_contigs:
                raise UsageError(
                    f"bedMethyl has repeated contigs or mixed aliases: {site.chrom}."
                )
            seen_contigs.add(canonical)
            last_position = -1
            previous = site.chrom
            cpgs = anchors.get(canonical, ())
            bounds = by_chrom.get(canonical, [])
            active = []
            next_region = 0
        if site.start < last_position:
            raise UsageError(f"bedMethyl is not sorted by position at {location}.")
        if site.start != last_position:
            site_codes.clear()
            last_position = site.start
        if site.code in site_codes:
            raise UsageError(f"Duplicate bedMethyl row for {site.code} at {location}.")
        site_codes.add(site.code)
        seen_codes.add(site.code)
        if site.code not in codes:
            continue
        if not bounds:
            if include_outside:
                yield site, []
            continue
        while next_region < len(bounds) and bounds[next_region][0] <= site.start:
            _, end, index = bounds[next_region]
            if end > site.start:
                active.append((end, index))
            next_region += 1
        active = [(end, index) for end, index in active if end > site.start]
        if active:
            offset = bisect_left(cpgs, site.start)
            if offset == len(cpgs) or cpgs[offset] != site.start:
                raise UsageError(f"bedMethyl site {location} is not a CpG C in --ref.")
        if active or include_outside:
            yield site, active

    if seen_codes and seen_codes.isdisjoint(codes):
        raise UsageError(
            f"bedMethyl has no rows for the requested modification(s) {', '.join(codes)}. "
            "Supply per-modification calls, not modkit --combine-mods output."
        )


def region_context(
    region: Region, available: set[str], anchors: Mapping[str, Sequence[int]]
) -> tuple[int | None, str | None]:
    if region.start is None or region.end is None:
        return None, "no_transcript"
    if region.chrom not in available:
        return None, "contig_absent"
    cpgs = anchors.get(region.chrom, ())
    return bisect_left(cpgs, region.end) - bisect_left(cpgs, region.start), None


def region_identity(region: Region, code: str) -> tuple[Value, ...]:
    return (
        region.name,
        region.chrom,
        region.start,
        region.end,
        region.gene_id,
        region.transcript,
        region.strand,
        code,
    )


def region_rows(
    sites: Iterable[Site],
    regions: Sequence[Region],
    aliases: Mapping[str, Chromosome],
    lengths: Mapping[str, int],
    anchors: Mapping[str, Sequence[int]],
    codes: Sequence[str],
    min_depth: int,
    min_sites: int,
) -> Iterator[tuple[Value, ...]]:
    """Keep only per-region summaries, not a genome-sized table of sample calls."""
    tallies: dict[tuple[int, str], Counts] = {}
    seen_codes: set[str] = set()
    for site, active in sites_in_regions(
        sites, regions, aliases, lengths, anchors, codes, seen_codes
    ):
        for _, index in active:
            key = index, site.code
            if key not in tallies:
                tallies[key] = Counts()
            tallies[key].add(site, min_depth)
    available = {chrom.name for chrom in aliases.values()}
    empty = Counts()
    for index, region in enumerate(regions):
        expected, reason = region_context(region, available, anchors)
        for code in codes:
            yield (
                *region_identity(region, code),
                *tallies.get((index, code), empty).values(
                    min_sites,
                    expected,
                    reason
                    or (
                        "modification_not_tabulated"
                        if seen_codes and code not in seen_codes
                        else None
                    ),
                ),
            )
