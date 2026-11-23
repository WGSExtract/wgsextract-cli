"""Read-depth copy-number screening on a short-read whole-genome alignment."""

import logging
import tempfile
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import NamedTuple

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.genotyping import TypingTargetCnvParams
from wgsextract_cli.commands.info.metrics import (
    contig_key,
    is_long_read_library,
    run_body_sample,
    sample_sex_evidence,
)
from wgsextract_cli.commands.info.targets import BedTarget, read_bed, target_depths
from wgsextract_cli.core.alignment.sequencing import read_group_platforms
from wgsextract_cli.core.panel import load_annotations
from wgsextract_cli.core.ref_library.assets import read_fai_lengths
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.sex_inference import nonpar_bounds
from wgsextract_cli.core.table_writer import Column, write_table
from wgsextract_cli.core.temp_directory import scratch_parent
from wgsextract_cli.core.utils import UsageError, run_command
from wgsextract_cli.core.variant_files import get_bam_header

from .common import Alignment, open_alignment
from .target_cnv_data import PRIMARY, IntervalMask, baseline_and_mask

MIN_BASELINE_DEPTH = 10
MIN_BASELINE_BREADTH = 0.8
MIN_BASELINE_WINDOWS = 20
MIN_GC_BIN_WINDOWS = 5
DEPTH_OPTIONS = ("-Q", "20", "-q", "20", "-s", "-G", "SUPPLEMENTARY")
SPECIALISTS = {"SMN1": "typing smn", "SMN2": "typing smn", "CYP2D6": "typing cyp2d6"}
TABLE = (
    Column("name", "TEXT", "BED name, or the interval if unnamed."),
    Column("contig", "TEXT"),
    Column("start", "INTEGER", "0-based BED start."),
    Column("end", "INTEGER", "Exclusive BED end."),
    Column("state", "TEXT"),
    Column("reason", "TEXT", "Why no copy number was reported."),
    Column("specialist", "TEXT", "An available typing command for the locus."),
    Column(
        "expected_copy_number", "INTEGER", "Normal copy number for the locus and sex."
    ),
    Column("copy_number", "TEXT", "Screened class: 0, 1, 2 or 3+."),
    Column("confidence", "TEXT", "Heuristic, not a calibrated probability."),
    Column("mean_depth", "REAL", spec=".3f"),
    Column("gc_fraction", "REAL", spec=".4f"),
    Column("gc_matched_depth", "REAL", spec=".3f"),
    Column("copy_number_estimate", "REAL", spec=".3f"),
)

Region = tuple[str, int, int]


def _region(target: BedTarget) -> Region:
    return target.chrom, target.start, target.end


def _gc_fractions(
    aln: Alignment, reference: str, targets: Sequence[BedTarget], scratch: Path
) -> dict[Region, float]:
    lengths = read_fai_lengths(reference)
    if not lengths:
        raise UsageError(f"Index the reference FASTA first: samtools faidx {reference}")
    queries: dict[str, Region] = {}
    for t in targets:
        if lengths.get(t.chrom) != aln.lengths[t.chrom]:
            raise UsageError(
                f"Reference FASTA and alignment disagree on {t.chrom} (name or length); "
                "supply the matching FASTA via --ref."
            )
        queries[f"{t.chrom}:{t.start + 1}-{t.end}"] = _region(t)
    regions = scratch / "gc.regions"
    regions.write_text("".join(f"{key}\n" for key in queries), newline="\n")
    # Read output as a stream: a user-provided interval can cover a chromosome.
    gc: dict[Region, float] = {}
    with tempfile.TemporaryFile(dir=scratch) as seq:
        run_command(["samtools", "faidx", "-r", regions, reference], stdout=seq)
        seq.seek(0)
        key = None
        bases = gc_bases = 0
        for line in seq:
            if line.startswith(b">"):
                if key is not None and bases == key[2] - key[1]:
                    gc[key] = gc_bases / bases
                key = queries.get(line[1:].rstrip().decode())
                bases = gc_bases = 0
            else:
                seq_bases = line.upper()
                g_and_c = seq_bases.count(b"G") + seq_bases.count(b"C")
                gc_bases += g_and_c
                bases += g_and_c + seq_bases.count(b"A") + seq_bases.count(b"T")
        if key is not None and bases == key[2] - key[1]:
            gc[key] = gc_bases / bases
    return gc


def _check_reads(aln: Alignment) -> None:
    header = get_bam_header(aln.path) or ""
    platforms = read_group_platforms(header)
    if len(platforms) > 1 or {"pacbio", "ont"}.intersection(platforms):
        raise UsageError(
            "typing target-cnv needs short-read WGS, not mixed or long reads."
        )
    if not set(platforms).intersection({"illumina", "dnbseq", "ultima"}):
        sample = run_body_sample(aln.path, aln.ref_opt if aln.is_cram else [])
        if is_long_read_library(sample.avg_len, header, sample.first_qname):
            raise UsageError("typing target-cnv needs short-read WGS, not long reads.")


@dataclass(frozen=True)
class GcBaseline:
    depth: float
    dispersion: float


def _normalized_baselines(
    baseline: Sequence[BedTarget], gc: dict[Region, float]
) -> tuple[float, dict[int, GcBaseline], set[str]]:
    usable = [t for t in baseline if _region(t) in gc]
    depths = [t.mean for t in usable]
    diploid = median(depths) if depths else 0
    covered = [
        t for t in usable if t.bases_at_least(1) >= MIN_BASELINE_BREADTH * t.length
    ]
    if (
        len(usable) < max(MIN_BASELINE_WINDOWS, len(baseline) * MIN_BASELINE_BREADTH)
        or len(covered) < len(usable) * MIN_BASELINE_BREADTH
        or diploid < MIN_BASELINE_DEPTH
    ):
        raise UsageError(
            "Insufficient target-CNV baseline coverage: use short-read whole-genome "
            f"data with at least {MIN_BASELINE_DEPTH}x median autosomal baseline depth."
        )
    by_gc = defaultdict[int, list[float]](list)
    for t in usable:
        by_gc[_gc_bin(gc[_region(t)])].append(t.mean)
    bins = {}
    for number, values in by_gc.items():
        if (
            len(values) >= MIN_GC_BIN_WINDOWS
            and (depth := median(values)) >= MIN_BASELINE_DEPTH
        ):
            mad = median(abs(x - depth) for x in values)
            bins[number] = GcBaseline(depth, mad / depth)
    return diploid, bins, {t.chrom for t in covered}


def _gc_bin(fraction: float) -> int:
    return min(19, int(fraction * 20))


def _copy_call(depth: float, baseline: GcBaseline) -> tuple[str, str, float]:
    estimate = 2 * depth / baseline.depth
    number = min(3, int(estimate + 0.5))
    distance = min(abs(estimate - boundary) for boundary in (0.5, 1.5, 2.5))
    noise = max(0.1, 1.4826 * baseline.dispersion * max(1.0, estimate))
    confidence = (
        "high" if distance >= 2 * noise else "medium" if distance >= noise else "low"
    )
    return "3+" if number == 3 else str(number), confidence, estimate


def _expected(t: BedTarget, aln: Alignment, sex: str) -> tuple[int | None, str]:
    chrom = contig_key(t.chrom)
    if chrom not in PRIMARY:
        return None, "not_primary_nuclear_contig"
    if chrom not in {"X", "Y"}:
        return 2, ""
    bounds = nonpar_bounds(chrom, aln.lengths[t.chrom])
    if bounds is None:
        return None, "unsupported_sex_chromosome"
    left, right = bounds
    if t.start < left or t.end > right:
        return None, "pseudoautosomal_region"
    if sex not in {"male", "female"}:
        return None, "sex_unknown"
    return (1 if sex == "male" else 2 if chrom == "X" else 0), ""


def _specialist(t: BedTarget, build: str) -> str:
    if build == "t2t":
        return ""
    loci = load_annotations(build).loci
    return ", ".join(dict.fromkeys(
        command for gene, command in SPECIALISTS.items()
        if any(
            contig_key(locus.chrom) == contig_key(t.chrom)
            and locus.start < t.end and t.start < locus.end
            for locus in loci.get(gene, ())
        )
    ))  # fmt: skip


class TargetCall(NamedTuple):
    name: str
    contig: str
    start: int
    end: int
    state: str
    reason: str | None
    specialist: str | None
    expected_copy_number: int | None
    copy_number: str | None
    confidence: str | None
    mean_depth: float
    gc_fraction: float | None
    gc_matched_depth: float | None
    copy_number_estimate: float | None


def _row(
    t: BedTarget,
    aln: Alignment,
    sex: str,
    mask: IntervalMask,
    fractions: dict[Region, float],
    bins: dict[int, GcBaseline],
    covered_autosomes: Collection[str],
) -> TargetCall:
    coords = (t.name, *_region(t))
    expected, reason = _expected(t, aln, sex)
    gc = fractions.get(_region(t))
    if mask.overlaps(contig_key(t.chrom), t.start, t.end):
        return TargetCall(
            *coords, "specialist_caller_required", "segdup_or_low_mappability",
            _specialist(t, aln.build), expected, None, None, t.mean, gc, None, None,
        )  # fmt: skip
    if expected == 0:
        return TargetCall(
            *coords, "not_applicable", "chrY_in_female", None, expected, None, None,
            t.mean, gc, None, None,
        )  # fmt: skip
    matched = bins.get(_gc_bin(gc)) if gc is not None else None
    if contig_key(t.chrom) not in {"X", "Y"} and t.chrom not in covered_autosomes:
        reason = reason or "chromosome_baseline_uncovered"
    if reason or matched is None:
        reason = reason or (
            "ambiguous_reference_bases" if gc is None else "no_gc_baseline"
        )
        return TargetCall(
            *coords,
            "no_call",
            reason,
            None,
            expected,
            None,
            None,
            t.mean,
            gc,
            None,
            None,
        )
    number, confidence, estimate = _copy_call(t.mean, matched)
    return TargetCall(
        *coords, "called", None, None, expected, number, confidence, t.mean, gc,
        matched.depth, estimate,
    )  # fmt: skip


def run_target_cnv(params: TypingTargetCnvParams) -> None:
    aln = open_alignment(params, purpose="typing target-cnv", any_build=True)
    targets = read_bed(params.bed, aln.lengths, option="--bed")
    baseline, mask = baseline_and_mask(aln.build, aln.lengths, detailed=params.download)
    reference = aln.require_reference()
    _check_reads(aln)
    target_mask = IntervalMask.from_rows(_region(t) for t in targets)
    baseline = [t for t in baseline if not target_mask.overlaps(*_region(t))]
    sex = "not_needed"
    if any(contig_key(t.chrom) in {"X", "Y"} for t in targets):
        effective, observed, declared = sample_sex_evidence(
            aln.path, aln.outdir, reference=reference
        )
        sex = effective.lower()
        current_result().add_metrics(
            effective_sex=sex, observed_sex=observed.sex, declared_sex=declared,
            sex_method=observed.method, sex_confidence=observed.confidence,
        )  # fmt: skip
    result = current_result()
    result.add_input(params.bed, "targets_bed")
    result.add_input(reference, "reference_fasta")
    regions = [*baseline, *targets]
    logging.info(
        "Sampling %d autosomal baseline windows and %d targets.",
        len(baseline),
        len(targets),
    )
    with tempfile.TemporaryDirectory(
        dir=scratch_parent(aln.outdir), prefix=".target-cnv-"
    ) as path:
        scratch = Path(path)
        gc = _gc_fractions(aln, reference, regions, scratch)
        target_depths(
            aln.path, reference, scratch / "depth.bed", regions, aln.lengths,
            depth_options=("-@", aln.threads, *DEPTH_OPTIONS),
        )  # fmt: skip
    diploid, bins, covered_autosomes = _normalized_baselines(baseline, gc)
    rows = [_row(t, aln, sex, mask, gc, bins, covered_autosomes) for t in targets]
    called = sum(row.state == "called" for row in rows)
    specialist = sum(row.state == "specialist_caller_required" for row in rows)
    result.add_metrics(
        target_cnv_build=aln.build,
        target_cnv_mask="giab_v3.5_exact" if params.download else "giab_v3.5_bundled",
        target_cnv_baseline_depth=round(diploid, 3),
        target_cnv_called=called,
        target_cnv_specialist_required=specialist,
        target_cnv_unresolved=len(rows) - called - specialist,
    )
    warn(
        "target_cnv_screening_only",
        "Target copy number is a depth-based screen, not a diagnosis.",
    )
    output = Path(aln.outdir, f"{aln.stem}.target-cnv.tsv")
    write_table(output, TABLE, rows, "tsv")
    result.add_output(output, "target_cnv")
    human_output(
        f"Target copy number: {called}/{len(rows)} called, {specialist} need a specialist. {output}"
    )


cmd_target_cnv = cli_handler(TypingTargetCnvParams, run_target_cnv)
