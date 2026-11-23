"""Offline row selection shared by ``vcf filter`` and ``vcf table``."""

from __future__ import annotations

import hashlib
import logging
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.api.vcf import VcfFilterParams, VcfTableParams
from wgsextract_cli.core import panel, read_scope
from wgsextract_cli.core.builds import build_from_chr1_length, canonical_build
from wgsextract_cli.core.paths import append_suffix
from wgsextract_cli.core.ref_library.assets import read_fai_lengths
from wgsextract_cli.core.ref_library.resolver import header_contig_lengths
from wgsextract_cli.core.region_selection import (
    Intervals,
    bed_intervals,
    literal_region,
    merge_ranges,
    region_items,
)
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import UsageError, run_command
from wgsextract_cli.core.variant_files import (
    ensure_vcf_prepared,
    match_chromosome_name,
    vcf_header_chromosomes,
    vcf_index_chromosomes,
)

_UNKNOWN_LENGTH = 2**31 - 1


@dataclass(frozen=True)
class PanelSlice:
    intervals: Intervals
    build: str | None
    prepared: str | None

    def args(self, vcf: str, work: str) -> list[str]:
        if not self.intervals:
            return ["--header-only"]
        regions = Path(work, "panel-regions.tsv")
        # bcftools emits contigs in regions-file order. Tabix uses the VCF's
        # record order, which can differ from the header's declaration order.
        order = dict.fromkeys([*vcf_index_chromosomes(vcf), *self.intervals])
        regions.write_text(
            "".join(
                f"{chrom}\t{start + 1}\t{end}\n"
                for chrom in order
                for start, end in self.intervals.get(chrom, ())
            ),
            encoding="utf-8",
            newline="\n",
        )
        return ["-R", str(regions), "--regions-overlap", "record"]


def indexed_vcf(path: str) -> str:
    """Prepare an indexed VCF, or index a BCF in place with bcftools."""
    if not path.lower().endswith(".bcf"):
        return ensure_vcf_prepared(path)
    index = Path(path + ".csi")
    if not index.exists() or index.stat().st_mtime < Path(path).stat().st_mtime:
        logging.info(f"Indexing {path}...")
        run_command(["bcftools", "index", "-f", path])
    return path


def _length_build(lengths: dict[str, int]) -> str | None:
    if build := build_from_chr1_length(lengths):
        return build
    if not lengths:
        return None
    # A chromosome slice may have no chr1. Mitochondrial length alone
    # doesn't distinguish the assemblies.
    matches = []
    for candidate in panel.BUILDS:
        names, differ = panel.reference_contigs(
            lengths, panel.load_annotations(candidate)
        )
        if names.keys() - {"chrM"} and not set(differ) - {"chrM"}:
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def _gene_annotations(
    header: str,
    vcf_lengths: dict[str, int],
    ref_lengths: dict[str, int],
    *,
    explicit_ref: bool,
) -> panel.Annotations:
    evidence = {
        build
        for line in header.splitlines()
        if line.startswith(("##assembly=", "##reference="))
        if (build := canonical_build(line.partition("=")[2].strip().strip('"<>')))
    }
    if build := _length_build(vcf_lengths):
        evidence.add(build)
    if explicit_ref or not evidence:
        if build := _length_build(ref_lengths):
            evidence.add(build)
    if len(evidence) > 1:
        raise UsageError(
            f"Conflicting assembly evidence: {', '.join(sorted(evidence))}."
        )
    if not evidence:
        raise UsageError(
            "Cannot determine the VCF's assembly for gene selection. Use a VCF "
            "with recognizable assembly metadata or pass a matching indexed "
            "FASTA with --ref."
        )
    notes = panel.load_annotations(evidence.pop())
    _, differ = panel.reference_contigs(vcf_lengths, notes)
    if wrong := [chrom for chrom in differ if chrom != "chrM"]:
        raise UsageError(
            f"VCF contig lengths do not match {notes.build}: {', '.join(wrong)}."
        )
    return notes


def _panel_loci(
    params: VcfFilterParams | VcfTableParams, notes: panel.Annotations
) -> list[panel.Locus]:
    names = [
        gene
        for value in (params.gene or "", *params.genes)
        for gene in value.replace(",", " ").split()
    ]
    if params.gene_file:
        gene_file = params.gene_file.expanduser()
        try:
            names.extend(panel.read_gene_file(gene_file))
        except UnicodeError as exc:
            raise UsageError(f"--gene-file {gene_file}: expected UTF-8 text.") from exc
        current_result().add_input(gene_file, "gene_file")
    resolved = panel.resolve_genes(dict.fromkeys(names), notes)
    for preset in params.presets:
        resolved.loci.extend(panel.preset_loci(preset, notes))
    if not resolved.loci:
        raise UsageError("No genes were provided for panel selection.")
    return resolved.loci


def _bed_rows(path: Path) -> tuple[list[tuple[str, int, int]], str]:
    try:
        rows = bed_intervals(str(path))
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
    except (OSError, ValueError, EOFError) as exc:
        raise UsageError(f"--bed {path}: {exc}") from exc
    current_result().add_input(path, "regions_bed")
    return rows, digest.hexdigest()


def _region_ranges(region: str, lengths: dict[str, int]) -> Intervals:
    ranges: Intervals = {}
    # Only literals are allowed here. File reads use the typed --bed field.
    for item in region_items(region, commas=True, files=False):
        contig = item if item in lengths else item.rsplit(":", 1)[0]
        named = contig if contig in lengths else match_chromosome_name(contig, lengths)
        if named is None:
            raise UsageError(f"--region {item}: unknown VCF contig {contig}.")
        try:
            _, start, end = literal_region(named + item[len(contig) :], lengths)
        except ValueError as exc:
            raise UsageError(f"--region: {exc}") from exc
        ranges.setdefault(named, []).append((start, end))
    if not ranges:
        raise UsageError("--region must name at least one interval.")
    return {chrom: merge_ranges(spans) for chrom, spans in ranges.items()}


def _intersect(panel_ranges: Intervals, bounds: Intervals) -> Intervals:
    clipped: Intervals = {}
    for chrom, spans in panel_ranges.items():
        regions = bounds.get(chrom, [])
        ends = [end for _, end in regions]
        for start, end in spans:
            index = bisect_right(ends, start)
            while index < len(regions) and regions[index][0] < end:
                a, b = regions[index]
                clipped.setdefault(chrom, []).append((max(a, start), min(b, end)))
                index += 1
    return clipped


def resolve_panel_slice(
    params: VcfFilterParams | VcfTableParams, vcf: str
) -> PanelSlice | None:
    """Resolve and record the gene/BED union, intersected with --region."""
    if not (params.has_gene_panel or params.bed):
        return None
    header = run_command(
        ["bcftools", "view", "--no-version", "-h", vcf], capture_output=True
    ).stdout
    declared = vcf_header_chromosomes(header)
    vcf_lengths = header_contig_lengths(header)
    build = build_from_chr1_length(vcf_lengths)
    source = bed_sha256 = None
    genes: list[str] = []
    rows: list[tuple[str, int, int]] = []
    bundled_lengths: dict[str, int] = {}
    if params.has_gene_panel:
        ref = params.ref.expanduser() if params.ref else None
        ref_lengths = (
            read_fai_lengths(ref)
            if ref
            and read_scope.may_follow(ref)
            and ref.is_file()
            and read_scope.may_follow(append_suffix(ref, ".fai"))
            else {}
        )
        notes = _gene_annotations(
            header, vcf_lengths, ref_lengths, explicit_ref=params.explicitly_set("ref")
        )
        build, source = notes.build, notes.source
        bundled_lengths = dict(notes.contigs)
        loci = _panel_loci(params, notes)
        genes = sorted({locus.symbol for locus in loci})
        pad = round(min(params.pad_kb, max(notes.contigs.values()) / 1000) * 1000)
        rows.extend(
            (target.chrom, target.start, target.end)
            for target in panel.build_targets(loci, pad, notes.contigs)
        )
    if params.bed:
        bed_rows, bed_sha256 = _bed_rows(params.bed.expanduser())
        rows.extend(bed_rows)

    prepared = None
    if not declared:
        prepared = indexed_vcf(vcf)
        declared = vcf_index_chromosomes(prepared)
    lengths = {
        name: vcf_lengths.get(name)
        or bundled_lengths.get(match_chromosome_name(name, bundled_lengths) or "")
        or _UNKNOWN_LENGTH
        for name in declared
    }
    names = {
        chrom: chrom if chrom in lengths else match_chromosome_name(chrom, lengths)
        for chrom in {row[0] for row in rows}
    }
    if absent := sorted(chrom for chrom, name in names.items() if name is None):
        warn(
            "panel_contigs_not_in_vcf",
            f"Panel contigs absent from the VCF were skipped: {', '.join(absent)}.",
            contigs=",".join(absent),
        )
    unmerged: Intervals = {}
    for chrom, start, end in rows:
        if (name := names[chrom]) is not None and start < (
            end := min(end, lengths[name])
        ):
            unmerged.setdefault(name, []).append((start, end))
    intervals = {chrom: merge_ranges(spans) for chrom, spans in unmerged.items()}
    if params.region:
        intervals = _intersect(intervals, _region_ranges(params.region, lengths))

    result = current_result()
    result.set_details(
        {
            "panel": {
                "genes": genes,
                "presets": list(params.presets),
                "reference_build": build,
                "annotation_source": source,
                "bed_sha256": bed_sha256,
                "coordinate_system": "0-based half-open",
                "intervals": [
                    {"chrom": chrom, "start": start, "end": end}
                    for chrom, spans in intervals.items()
                    for start, end in spans
                ],
            },
        }
    )
    result.add_metrics(
        region_intervals=sum(map(len, intervals.values())),
        region_bases=sum(
            end - start for spans in intervals.values() for start, end in spans
        ),
    )
    return PanelSlice(intervals, build, prepared)
