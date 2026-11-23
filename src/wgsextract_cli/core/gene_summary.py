"""``info gene``: one gene's span, transcripts, variants and read coverage.

Everything comes from this computer: the gene span from the bundled NCBI
RefSeq annotations (core/panel.py), transcript and exon models from the
library's ``ref gene-map`` download (core/gene_models.py), the sample's
variants and their annotations from its VCF (core/variant_table.py, as
``vcf table`` reads them) and depth from its BAM/CRAM (``samtools depth``).
What is missing is a notice naming the command that makes it; nothing is
downloaded. The document is the desktop app's gene page.
"""

from __future__ import annotations

import re
import subprocess
from array import array
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from shlex import quote

from wgsextract_cli.core import acmg, panel
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.builds import build_from_chr1_length
from wgsextract_cli.core.gene_models import (
    Transcript,
    read_transcripts,
    source_note,
    transcripts_path,
)
from wgsextract_cli.core.ref_library.assets import sequence_signature
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.utils import UsageError, WGSExtractError, popen, run_command
from wgsextract_cli.core.variant_confidence import is_pathogenic
from wgsextract_cli.core.variant_table import (
    COLUMN_NAMES,
    Row,
    VcfHeader,
    parse_header,
    variant_rows,
)

SCHEMA = "wgsextract.gene/1"
# gnomAD allele frequency below which a variant counts as rare.
RARE_AF = 0.01
DAMAGING_IMPACTS = frozenset({"HIGH", "MODERATE"})
FILTERS = ("rare_damaging", "clinvar_plp", "acmg_plp")
# Coverage profile resolution across the gene body.
BINS = 100
MAX_ROWS = 5000
TRANSCRIPT_BUILDS = ("hg19", "hg38")
# A path in a header line, kept as its file name.
_PATHS = re.compile(r"(?:[A-Za-z]:)?[\\/](?:[^\s,;'\"=]*[\\/])?([^\s,;'\"=\\/]+)")


@dataclass(frozen=True)
class GeneQuery:
    symbol: str
    alignment: str | None = None
    vcf: str | None = None
    reference: str | None = None  # a FASTA or folder, for CRAM
    reflib: str | None = None
    build: str | None = None
    sample: str | None = None
    min_depth: float = 10.0


@dataclass(frozen=True)
class GeneRegion:
    locus: panel.Locus  # annotation (chrN) names, 0-based
    names: Mapping[str, str]  # annotation contig -> the inputs' contig

    def contig(self) -> str:
        return self.names.get(self.locus.chrom, self.locus.chrom)

    def region(self) -> str:
        return f"{self.contig()}:{self.locus.start + 1}-{self.locus.end}"


@dataclass(frozen=True)
class Variants:
    rows: list[Row]
    header: VcfHeader
    sample: str | None


@dataclass
class GeneSummary:
    document: dict[str, object]
    warnings: list[tuple[str, str]]
    variants: Variants | None


class Notices:
    """Missing inputs and data, each with the command that provides it."""

    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, code: str, message: str, command: str = "", **extra: str) -> None:
        self.items.append(
            {"code": code, "message": message, "command": command, **extra}
        )


def _header_lengths(path: str) -> dict[str, int]:
    try:
        header = get_bam_header(path)
    except (WGSExtractError, OSError) as e:
        raise UsageError(f"Can't read the header of {path}: {e}") from e
    lengths = sequence_lengths(header)
    if not lengths:
        raise UsageError(f"{path} has no contig lengths in its header.")
    return lengths


def _build(query: GeneQuery, headers: Mapping[str, dict[str, int]]) -> str:
    found = {path: build_from_chr1_length(lengths) for path, lengths in headers.items()}
    builds = set(found.values())
    if len(builds) > 1:
        detail = ", ".join(
            f"{Path(p).name} is {b or 'unknown'}" for p, b in found.items()
        )
        raise UsageError(f"The inputs are on different builds: {detail}.")
    build = next(iter(builds), None)
    if headers and build is None:
        raise UsageError(
            "Can't tell the inputs' build from their chr1 length; gene spans "
            f"exist for {', '.join(panel.BUILDS)}."
        )
    if query.build and build and query.build != build:
        raise UsageError(f"The inputs are {build}, not --build {query.build}.")
    return build or query.build or "hg38"


def _region(
    query: GeneQuery,
    notes: panel.Annotations,
    lengths: Mapping[str, int] | None,
    warnings: list[tuple[str, str]],
) -> GeneRegion:
    resolved = panel.resolve_genes([query.symbol], notes)
    for given, symbol in resolved.aliases.items():
        warnings.append(("gene_alias", f"{given} is an alias of {symbol}."))
    names = {c: c for c in notes.contigs}
    if lengths:
        names, _ = panel.reference_contigs(lengths, notes)
    placed = [locus for locus in resolved.loci if locus.chrom in names]
    if not placed:
        contigs = ", ".join(sorted({x.chrom for x in resolved.loci}))
        raise UsageError(
            f"The inputs have none of {query.symbol}'s contigs ({contigs})."
        )
    if len(placed) > 1:
        others = ", ".join(f"{x.chrom}:{x.start + 1}-{x.end}" for x in placed[1:])
        warnings.append(
            (
                "gene_several_loci",
                f"{placed[0].symbol} is also annotated at {others}; showing "
                f"{placed[0].chrom}.",
            )
        )
    return GeneRegion(placed[0], names)


def _transcripts(
    query: GeneQuery, build: str, region: GeneRegion, notices: Notices
) -> tuple[list[Transcript], str]:
    if build not in TRANSCRIPT_BUILDS:
        notices.add(
            "transcripts_unsupported_build",
            f"Transcript models exist for {' and '.join(TRANSCRIPT_BUILDS)} only, "
            f"not {build}.",
        )
        return [], ""
    path = transcripts_path(query.reflib, build)
    if path is None:
        command = "wgsextract ref gene-map" + (
            f" --ref {quote(query.reflib)}" if query.reflib else ""
        )
        notices.add(
            "transcripts_missing",
            f"No {build} transcript and exon models are installed; download them "
            f"(about 17 MB for hg19 and hg38) with '{command}'.",
            command,
        )
        return [], ""
    locus = region.locus
    found = read_transcripts(path, locus.chrom, locus.start, locus.end, locus.symbol)
    if not found:
        notices.add(
            "transcripts_none",
            f"The installed refGene models have no transcript of {locus.symbol}.",
        )
    # Longest coding sequence first: the transcript the page shows first.
    found.sort(key=lambda t: (-t.cds_length, t.start - t.end, t.accession))
    return found, source_note(path)


def _transcript_document(t: Transcript, names: Mapping[str, str]) -> dict[str, object]:
    return {
        "accession": t.accession,
        "strand": t.strand,
        "chrom": names.get(t.chrom, t.chrom),
        "start": t.start + 1,
        "end": t.end,
        "cds_start": t.cds[0] + 1 if t.cds else None,
        "cds_end": t.cds[1] if t.cds else None,
        "exons": [[start + 1, end] for start, end in t.exons],
    }


_PROVENANCE_TERMS = {
    "vep": ("vep", "csq"),
    "snpeff": ("snpeff", "ann"),
    "clinvar": ("clnsig", "clinvar"),
    "gnomad": ("gnomad_af", "gnomad"),
    "acmg": (acmg.CLASS.lower(), "fastvep"),
}


def _header_provenance(header: VcfHeader) -> list[dict[str, object]]:
    """Each annotation's header lines: its INFO description, the tool and
    version, the command that added it (file paths cut to their names)."""
    meta = [
        line[2:]
        for line in header.lines
        if line.startswith("##")
        and not line.startswith(("##contig=", "##FORMAT=", "##FILTER=", "##ALT="))
    ]
    found: list[dict[str, object]] = []
    for name in header.annotations:
        terms = _PROVENANCE_TERMS.get(name, (name,))
        lines = [
            _PATHS.sub(r"\1", line)[:300]
            for line in meta
            if any(re.search(rf"\b{re.escape(t)}\b", line.lower()) for t in terms)
        ]
        found.append({"annotation": name, "header": lines[:4]})
    return found


def variant_filters(row: Mapping[str, object], annotations: Sequence[str]) -> list[str]:
    """The page filters a variant row passes."""
    tags = []
    af = row.get("gnomad_af")
    rare = "gnomad" in annotations and (af is None or float(str(af)) < RARE_AF)
    if rare and str(row.get("impact") or "") in DAMAGING_IMPACTS:
        tags.append("rare_damaging")
    if is_pathogenic(str(row.get("clinvar_significance") or "")):
        tags.append("clinvar_plp")
    if str(row.get("acmg_class") or "") in acmg.PATHOGENIC:
        tags.append("acmg_plp")
    return tags


def read_variants(query: GeneQuery, region: GeneRegion) -> Variants:
    """The sample's carried alleles in the gene, as ``vcf table`` rows."""
    from wgsextract_cli.core.variant_files import ensure_vcf_prepared, get_vcf_samples

    assert query.vcf
    vcf = ensure_vcf_prepared(query.vcf)
    samples = get_vcf_samples(vcf)
    if query.sample and query.sample not in samples:
        raise UsageError(
            f"Sample '{query.sample}' is not in {query.vcf}; it has: "
            f"{', '.join(samples) or 'no samples'}."
        )
    sample = query.sample or (samples[0] if samples else None)
    command = ["bcftools", "view", "--no-version", "-r", region.region()]
    if sample and len(samples) > 1:
        command += ["-s", sample, "-I"]  # -I: keep INFO as it is
    text = run_command([*command, vcf], capture_output=True).stdout.splitlines()
    split = next((i for i, line in enumerate(text) if line.startswith("#CHROM")), -1)
    header = parse_header(text[: split + 1])
    rows = list(
        variant_rows(text[split + 1 :], header, sample_index=0 if sample else None)
    )
    return Variants(rows, header, sample)


def _annotation_notices(vcf: str, annotations: Sequence[str], notices: Notices) -> None:
    path = quote(vcf)
    needed = (
        (("vep", "snpeff"), "consequence", "consequences", f"wgsextract vep run --vcf-input {path}"),
        (("clinvar",), "clinvar", "ClinVar significance", f"wgsextract vcf clinvar --vcf-input {path}"),
        (("gnomad",), "gnomad", "gnomAD frequencies", f"wgsextract vcf gnomad --vcf-input {path}"),
        (("acmg",), "acmg", "research-only ACMG classes", f"wgsextract vcf acmg --vcf-input {path}"),
    )  # fmt: skip
    for names, key, label, command in needed:
        if not any(name in annotations for name in names):
            notices.add(
                "annotation_missing",
                f"{Path(vcf).name} has no {label}; add them with '{command}', "
                "then open the VCF it writes.",
                command,
                annotation=key,
            )


def _variants(
    query: GeneQuery, region: GeneRegion, notices: Notices
) -> tuple[dict[str, object], Variants | None]:
    if not query.vcf:
        command = (
            f"wgsextract vcf call --input {quote(query.alignment)}"
            if query.alignment
            else ""
        )
        notices.add(
            "vcf_missing",
            "No VCF given, so there is no variant table. Give the sample's "
            "annotated VCF" + (f", or call one with '{command}'." if command else "."),
            command,
        )
        return {"status": "missing"}, None
    found = read_variants(query, region)
    annotations = found.header.annotations
    _annotation_notices(query.vcf, annotations, notices)
    rows = []
    for values in found.rows[:MAX_ROWS]:
        row: dict[str, object] = dict(zip(COLUMN_NAMES, values, strict=True))
        row["filters"] = variant_filters(row, annotations)
        rows.append(row)
    return {
        "status": "ok",
        "file": query.vcf,
        "sample": found.sample,
        "region": region.region(),
        "annotations": annotations,
        "provenance": _header_provenance(found.header),
        "rare_af": RARE_AF,
        "acmg_disclaimer": acmg.DISCLAIMER if "acmg" in annotations else None,
        "rows": rows,
        "total": len(found.rows),
        "truncated": len(found.rows) > MAX_ROWS,
    }, found


def _depths(
    alignment: str, reference: str | None, contig: str, start: int, end: int
) -> array[int]:
    """Depth at each base of contig:start-end (0-based, half-open), from
    ``samtools depth -a``."""
    depths = array("I", bytes(4 * (end - start)))
    command = ["samtools", "depth", "-a", "-r", f"{contig}:{start + 1}-{end}"]
    if alignment.lower().endswith(".cram") and reference:
        command += ["--reference", reference]
    proc = popen(
        [*command, alignment], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None and proc.stderr is not None
    try:
        for line in proc.stdout:
            _, pos, depth = line.split("\t")
            offset = int(pos) - 1 - start
            if 0 <= offset < len(depths):
                depths[offset] = int(depth)
    except ValueError as e:
        proc.kill()
        raise WGSExtractError(f"Unexpected samtools depth row: {e}") from e
    finally:
        error = proc.stderr.read()
        code = proc.wait()
    if code != 0:
        raise WGSExtractError(f"samtools depth failed on {alignment}: {error[-2000:]}")
    return depths


def _stats(values: Sequence[int], threshold: float) -> dict[str, float | int]:
    if not values:
        return {"mean": 0.0, "min": 0, "callable_fraction": 0.0}
    return {
        "mean": round(sum(values) / len(values), 2),
        "min": min(values),
        "callable_fraction": round(
            sum(v >= threshold for v in values) / len(values), 4
        ),
    }


def _exon_coverage(
    depths: Sequence[int],
    start: int,
    transcripts: Iterable[Transcript],
    threshold: float,
) -> list[dict[str, object]]:
    """Each distinct exon once, with the transcripts that share it."""
    exons: dict[tuple[int, int], list[str]] = {}
    for t in transcripts:
        for exon in t.exons:
            exons.setdefault(exon, []).append(t.accession)
    found: list[dict[str, object]] = []
    for (first, last), accessions in sorted(exons.items()):
        stats = _stats(depths[max(0, first - start) : max(0, last - start)], threshold)
        found.append(
            {
                "start": first + 1,
                "end": last,
                "transcripts": accessions,
                **stats,
                "below": stats["callable_fraction"] < 1,
            }
        )
    return found


def _bins(
    depths: Sequence[int], start: int, threshold: float
) -> list[dict[str, object]]:
    size = max(1, -(-len(depths) // BINS))
    return [
        {
            "start": start + offset + 1,
            "end": start + min(offset + size, len(depths)),
            **_stats(depths[offset : offset + size], threshold),
        }
        for offset in range(0, len(depths), size)
    ]


def _coverage(
    query: GeneQuery,
    region: GeneRegion,
    reference: str | None,
    transcripts: Sequence[Transcript],
    notices: Notices,
) -> dict[str, object]:
    if not query.alignment:
        notices.add(
            "alignment_missing",
            "No BAM or CRAM given, so there is no read coverage. Give the "
            "sample's aligned reads.",
        )
        return {"status": "missing"}
    if query.alignment.lower().endswith(".cram") and not reference:
        notices.add(
            "coverage_reference_missing",
            "The CRAM's reference genome isn't installed, so its reads can't be "
            "read; install it on the Library page or give --ref.",
        )
        return {"status": "missing"}
    locus = region.locus
    # Transcripts can reach past the annotated gene span.
    start = min([locus.start, *(t.start for t in transcripts)])
    end = max([locus.end, *(t.end for t in transcripts)])
    depths = _depths(query.alignment, reference, region.contig(), start, end)
    gene = depths[locus.start - start : locus.end - start]
    exons = _exon_coverage(depths, start, transcripts, query.min_depth)
    return {
        "status": "ok",
        "file": query.alignment,
        "threshold": query.min_depth,
        "gene": _stats(gene, query.min_depth),
        "bins": _bins(gene, locus.start, query.min_depth),
        "exons": exons,
        "exons_below": sum(1 for exon in exons if exon["below"]),
    }


def _alignment_reference(query: GeneQuery, lengths: dict[str, int]) -> str | None:
    if not query.alignment or not query.alignment.lower().endswith(".cram"):
        return None
    from wgsextract_cli.core.variant_files import resolve_reference

    resolved = resolve_reference(
        query.reference, sequence_signature(lengths), query.alignment
    )
    return resolved if resolved and Path(resolved).is_file() else None


def summarise_gene(query: GeneQuery) -> GeneSummary:
    """The gene page document, the warnings to record, and the variants read."""
    headers: dict[str, dict[str, int]] = {}
    for path in filter(None, (query.alignment, query.vcf)):
        if not Path(path).is_file():
            raise UsageError(f"Input not found: {path}")
        headers[path] = _header_lengths(path)
    build = _build(query, headers)
    if build not in panel.BUILDS:
        raise UsageError(
            f"Gene spans exist for {', '.join(panel.BUILDS)}, not {build}."
        )
    notes = panel.load_annotations(build)
    warnings: list[tuple[str, str]] = []
    region = _region(query, notes, next(iter(headers.values()), None), warnings)
    notices = Notices()
    transcripts, transcript_source = _transcripts(query, build, region, notices)
    variants, found = _variants(query, region, notices)
    reference = (
        _alignment_reference(query, headers[query.alignment])
        if query.alignment
        else None
    )
    coverage = _coverage(query, region, reference, transcripts, notices)
    locus = region.locus
    document: dict[str, object] = {
        "schema": SCHEMA,
        "query": query.symbol,
        "build": build,
        "gene": {
            "symbol": locus.symbol,
            "chrom": region.contig(),
            "start": locus.start + 1,
            "end": locus.end,
            "locus": region.region(),
            "source": notes.source,
        },
        "transcripts": {
            "source": transcript_source,
            "items": [_transcript_document(t, region.names) for t in transcripts],
        },
        "variants": variants,
        "coverage": coverage,
        "filters": list(FILTERS),
        "notices": notices.items,
    }
    return GeneSummary(document, warnings, found)
