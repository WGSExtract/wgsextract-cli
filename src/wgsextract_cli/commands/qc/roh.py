"""`qc roh`: runs of homozygosity with bcftools roh and population allele
frequencies, and an isodisomy review flag over imprinted regions (#1174).

bcftools roh (Narasimhan et al. 2016, Bioinformatics) fits an HMM whose
emission probabilities come from each site's population allele frequency, so
real frequencies matter: a constant AF (OwnGenome's 0.4) overcalls short runs
(#821). The markers are the sample's biallelic SNV calls with PASS (or no)
FILTER, a genotype and GQ >= ``min_gq``, at sites of the AF table; genotypes,
not PLs, are used (``--GTs-only 30``) so callers' differently calibrated
likelihoods don't change the result.

A long run overlapping a build-matched imprinted region is flagged for
review: isodisomy (one form of uniparental disomy) is one explanation, others
are parents who share ancestry and a deletion, and a run can't show which
parent the chromosome came from or detect heterodisomy. See docs/roh.md.
"""

from __future__ import annotations

import bisect
import dataclasses
import gzip
import json
import logging
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.qc import QcRohParams
from wgsextract_cli.core import roh_af, roh_document, roh_regions
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.errors import CodedError
from wgsextract_cli.core.panel import load_annotations
from wgsextract_cli.core.paths import absolute_path, expand_path
from wgsextract_cli.core.ref_library.assets import input_stem
from wgsextract_cli.core.ref_library.resolver import header_contig_lengths
from wgsextract_cli.core.results import current_result, tool_version, warn
from wgsextract_cli.core.roh_document import Position
from wgsextract_cli.core.roh_regions import bare
from wgsextract_cli.core.temp_directory import scratch_parent
from wgsextract_cli.core.utils import UsageError, popen, run_command, run_pipeline
from wgsextract_cli.core.variant_files import verify_paths_exist

from ._relate_vcf import is_gvcf

GUIDE = roh_af.GUIDE
# bcftools roh's model: genotype error rate as a phred score for --GTs-only,
# and its default HMM transition probabilities, passed explicitly so a new
# bcftools default can't silently change results.
GT_ERROR_PHRED = 30
HW_TO_AZ = 6.7e-8
AZ_TO_HW = 5e-9
# Evidence a run needs before it can be flagged.
FLAG_MIN_MARKERS = 100
FLAG_MIN_QUALITY = 30.0
# Runs counted in F_ROH (McQuillan et al. 2008), and "long" runs.
FROH_MIN_BP = 1_500_000
# A run ends where consecutive markers are further apart than this (as PLINK's
# --homozyg-gap): a centromere or an uncalled stretch carries no evidence.
MAX_GAP_BP = 1_000_000
# A run is terminal only if it reaches a marker this close to the end.
TERMINAL_MARGIN_BP = 1_000_000
LONG_BP = 5_000_000
VARIANTS_ONLY = (
    "The VCF has no hom-ref calls, so runs are found from heterozygous and "
    "hom-alt calls only; their ends are less precise."
)
ACROCENTRIC = frozenset({"13", "14", "15", "21", "22"})
_RANGE = re.compile(r"^(?:chr)?([0-9]+)(?::([0-9_]+)-([0-9_]+))?$", re.IGNORECASE)
_PROCESSED = re.compile(r"overlapping with --AF-file/processed:\s*(\d+)/(\d+)")


@dataclass(frozen=True)
class Region:
    contig: str  # bare autosome name
    start: int  # 1-based inclusive
    end: int
    whole: bool

    def text(self, chr_prefix: bool) -> str:
        name = f"chr{self.contig}" if chr_prefix else self.contig
        return name if self.whole else f"{name}:{self.start}-{self.end}"


@dataclass(frozen=True)
class RohSegment:
    chrom: str
    start: int
    end: int
    length: int
    markers: int
    quality: float


def _sample(vcf: str, wanted: str | None) -> str:
    names = run_command(["bcftools", "query", "-l", vcf], capture_output=True)
    samples = names.stdout.splitlines()
    if wanted:
        if wanted not in samples:
            raise UsageError(
                f"{Path(vcf).name} has no sample {wanted!r} (it has: "
                f"{', '.join(samples[:10])})."
            )
        return wanted
    if len(samples) != 1:
        raise UsageError(
            f"{Path(vcf).name} has {len(samples)} samples; choose one with "
            f"--sample ({', '.join(samples[:10])})."
        )
    return samples[0]


def _chr_prefix(vcf: str, header_contigs: list[str]) -> bool:
    if header_contigs:
        return any(c.lower().startswith("chr") for c in header_contigs)
    first = run_command(
        ["bcftools", "query", "-f", "%CHROM\n", vcf], capture_output=True, check=False
    ).stdout.split("\n", 1)[0]
    return first.lower().startswith("chr")


def _build(params: QcRohParams, lengths: dict[str, int], vcf: str) -> str:
    declared = canonical_build(params.build) if params.build else None
    found = roh_af.build_of_lengths(lengths)
    if declared and found and declared != found:
        raise UsageError(
            f"--build says {params.build}, but {Path(vcf).name}'s contig lengths "
            f"are {found}."
        )
    build = found or declared
    if build is None:
        raise UsageError(
            f"Can't tell which build {Path(vcf).name} is on (its header has no "
            "autosome lengths); pass --build."
        )
    return build


def parse_scope(text: str | None, header: list[str], build: str) -> list[Region]:
    """The assessed regions: ``text`` (autosomes or ranges, 1-based), else
    every autosome the header lists (all 22 when it lists none)."""
    lengths = {bare(k): v for k, v in load_annotations(build).contigs.items()}
    if not text:
        listed = {bare(c) for c in header}
        contigs = [c for c in roh_af.AUTOSOMES if c in listed] or list(roh_af.AUTOSOMES)
        return [Region(c, 1, lengths[c], True) for c in contigs]
    regions: list[Region] = []
    for item in filter(None, (part.strip() for part in text.split(","))):
        found = _RANGE.match(item)
        contig = found[1] if found else None
        if found is None or contig not in roh_af.AUTOSOMES:
            raise UsageError(
                f"--regions {item!r}: qc roh assesses autosomes (1-22), as a "
                "whole (chr20) or a range (chr20:1000000-9000000)."
            )
        length = lengths[contig]
        start = int(found[2].replace("_", "")) if found[2] else 1
        end = int(found[3].replace("_", "")) if found[3] else length
        if not 1 <= start <= end <= length:
            raise UsageError(
                f"--regions {item!r}: {build} chr{contig} is {length:,} bp long."
            )
        region = Region(contig, start, end, start == 1 and end == length)
        if any(
            r.contig == contig and r.start <= end and start <= r.end for r in regions
        ):
            raise UsageError(f"--regions {item!r} overlaps another of its ranges.")
        regions.append(region)
    return sorted(regions, key=lambda r: (int(r.contig), r.start))


def _indexed(vcf: str) -> bool:
    return any(Path(vcf + ext).is_file() for ext in (".tbi", ".csi"))


def _select_markers(
    vcf: str,
    sample: str,
    scope: str,
    min_gq: int | None,
    work: Path,
) -> tuple[Path, Counter[str]]:
    """The sample's candidate markers (``markers.bcf``) and their counts by
    class: records, missing_genotype, weak_genotype, hom_ref, het, hom_alt."""
    candidates = work / "candidates.bcf"
    # INFO and other FORMAT fields aren't needed, and dropping them first
    # spares a sample subset from failing on an undeclared INFO tag.
    keep = "INFO,^FORMAT/GT" + (",FORMAT/GQ" if min_gq is not None else "")
    run_pipeline(
        [
            [
                "bcftools",
                "annotate",
                "-x",
                keep,
                "-r" if _indexed(vcf) else "-t",
                scope,
                "-Ou",
                vcf,
            ],
            [
                "bcftools",
                "view",
                "-s",
                sample,
                "-m2",
                "-M2",
                "-v",
                "snps",
                "-f",
                "PASS,.",
                "-Ob",
                "-o",
                str(candidates),
                "-",
            ],
        ],  # fmt: skip
        description="Selecting markers",
        stderr_dir=work,
    )
    counts: Counter[str] = Counter()
    fmt = "[%GT]\t[%GQ]\n" if min_gq is not None else "[%GT]\t.\n"
    with popen(
        ["bcftools", "query", "-f", fmt, str(candidates)],
        stdout=subprocess.PIPE,
        text=True,
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            gt, gq = line.rstrip("\n").split("\t")
            counts["records"] += 1
            alleles = gt.replace("|", "/").split("/")
            if "." in alleles:
                counts["missing_genotype"] += 1
            elif min_gq is not None and gq not in (".", "") and float(gq) < min_gq:
                counts["weak_genotype"] += 1
            elif len(set(alleles)) > 1:
                counts["het"] += 1
            else:
                counts["hom_ref" if alleles[0] == "0" else "hom_alt"] += 1
    if proc.returncode:
        raise CodedError("roh_input_unreadable", f"bcftools couldn't read {vcf}.")
    markers = work / "markers.bcf"
    exclude = 'GT="mis"' + (f" || FMT/GQ<{min_gq}" if min_gq is not None else "")
    run_command(
        ["bcftools", "view", "-e", exclude, "-Ob", "-o", str(markers), str(candidates)]
    )
    return markers, counts


def _run_roh(markers: Path, af_table: Path, work: Path) -> tuple[Path, int]:
    """Run bcftools roh; returns its output and the number of sites used."""
    output = work / "roh.txt"
    # contract: --GTs-only and the AF table are the model the issue specifies
    # (#1174): genotypes with real population frequencies, never --AF-dflt.
    command = ["bcftools", "roh", "--GTs-only", str(GT_ERROR_PHRED),
               "--AF-file", str(af_table), "--hw-to-az", str(HW_TO_AZ),
               "--az-to-hw", str(AZ_TO_HW), "-O", "s", "-o", str(output),
               str(markers)]  # fmt: skip
    done = run_command(command, capture_output=True)
    processed = _PROCESSED.search(done.stderr or "")
    return output, int(processed[2]) if processed else 0


@dataclass
class _Run:
    chrom: str
    start: int
    end: int
    markers: int = 0
    quality: float = 0.0

    def segment(self) -> RohSegment:
        # bcftools roh's RG line, which is this run of AZ sites, but one
        # decimal of its mean site quality.
        return RohSegment(self.chrom, self.start, self.end,
                          self.end - self.start + 1, self.markers,
                          round(self.quality / self.markers, 1))  # fmt: skip


def _parse_roh(
    output: Path, markers_out: Path, scope: list[Region]
) -> tuple[list[RohSegment], dict[str, tuple[int, int]], int]:
    """Runs of AZ sites, ended at each scope range's edge and where
    consecutive markers are more than ``MAX_GAP_BP`` apart; each contig's
    first and last marker; and the bases the markers cover (the F_ROH
    denominator: from first to last marker of each range, less those gaps).
    Writes the per-site states (ST lines) to ``markers_out``."""
    segments: list[RohSegment] = []
    spans: dict[str, tuple[int, int]] = {}
    starts: dict[str, list[int]] = {}
    for region in scope:
        starts.setdefault(region.contig, []).append(region.start)
    covered = 0
    run: _Run | None = None
    block_start = previous = 0
    block: tuple[str, int] | None = None

    def close() -> None:
        nonlocal run
        if run is not None:
            segments.append(run.segment())
            run = None

    with (
        open(output, encoding="utf-8") as f,
        atomic_output(markers_out) as staged,
        gzip.open(staged, "wt", encoding="utf-8", newline="\n") as sites,
    ):
        sites.write("chrom\tpos\tstate\tquality\n")
        for line in f:
            if not line.startswith("ST\t"):
                continue
            _, _, chrom, pos, state, quality = line.rstrip("\n").split("\t")
            position = int(pos)
            here = (chrom, bisect.bisect_right(starts.get(bare(chrom), []), position))
            if here != block or position - previous > MAX_GAP_BP:
                close()
                if block is not None:
                    covered += previous - block_start + 1
                block_start, block = position, here
            first, _ = spans.get(chrom, (position, position))
            spans[chrom] = (first, position)
            previous = position
            if state == "1":
                run = run or _Run(chrom, position, position)
                run.end = position
                run.markers += 1
                run.quality += float(quality)
            else:
                close()
            sites.write(
                f"{chrom}\t{pos}\t{'AZ' if state == '1' else 'HW'}\t{quality}\n"
            )
        close()
        if block is not None:
            covered += previous - block_start + 1
    return segments, spans, covered


def _position(
    segment: RohSegment,
    whole: set[str],
    spans: dict[str, tuple[int, int]],
    lengths: dict[str, int],
) -> Position:
    """terminal when the run reaches a marker within ``TERMINAL_MARGIN_BP`` of
    the chromosome's end (its start too, but not the centromeric start of an
    acrocentric one); undetermined when it reaches a last marker that isn't
    near the end, or the scope was part of the chromosome."""
    contig = bare(segment.chrom)
    if contig not in whole:
        return "undetermined"
    first, last = spans[segment.chrom]
    ends = []
    if segment.end >= last:
        ends.append(lengths[contig] - last <= TERMINAL_MARGIN_BP)
    if segment.start <= first and contig not in ACROCENTRIC:
        ends.append(first <= TERMINAL_MARGIN_BP)
    if not ends:
        return "interstitial"
    return "terminal" if any(ends) else "undetermined"


def _document_region(
    region: roh_regions.ImprintedRegion,
) -> roh_document.ImprintedRegion:
    return roh_document.ImprintedRegion(
        id=region.id,
        band=region.band,
        chrom=region.chrom,
        start=region.start + 1,
        end=region.end,
        genes=list(region.genes),
        conditions=[roh_document.Condition(c.name, c.upd) for c in region.conditions],
    )


def _flag_message(
    segment: RohSegment, position: Position, overlaps: list[roh_regions.ImprintedRegion]
) -> str:
    where = ", ".join(
        f"{r.band or r.id}" + (f" ({', '.join(r.genes)})" if r.genes else "")
        for r in overlaps
    )
    conditions = "; ".join(
        f"{c.name}" + (f" ({c.upd})" if c.upd else "")
        for r in overlaps
        for c in r.conditions
    )
    associated = (
        f" Uniparental disomy of this region is associated with {conditions}."
        if conditions
        else ""
    )
    return (
        f"Investigate possible isodisomy: a {segment.length / 1e6:.1f} Mb {position} "
        f"run of homozygosity on {segment.chrom} ({segment.markers:,} markers) "
        f"overlaps the imprinted region {where}.{associated} This is not a "
        "diagnosis: a run of homozygosity also arises when parents share "
        "ancestry, or from a deletion; it can't show which parent the region "
        "came from, and heterodisomy leaves none. Methylation or parental testing "
        "through a clinical genetics service can tell."
    )


def run_roh(params: QcRohParams) -> None:
    verify_dependencies(["bcftools", "bgzip", "tabix"])
    vcf = expand_path(params.input)
    verify_paths_exist({"--input": vcf})
    header = run_command(["bcftools", "view", "-h", vcf], capture_output=True).stdout
    if is_gvcf(vcf, header):
        raise CodedError(
            "roh_gvcf",
            f"{Path(vcf).name} is a gVCF; give the VCF of its calls (its reference "
            "blocks aren't markers bcftools roh can use).",
        )
    sample = _sample(vcf, params.sample)
    lengths = header_contig_lengths(header)
    contigs = roh_af.header_contig_ids(header)
    build = _build(params, lengths, vcf)
    chr_prefix = _chr_prefix(vcf, contigs)
    scope = parse_scope(params.regions, contigs, build)
    scope_text = ",".join(r.text(chr_prefix) for r in scope)
    scope_contigs = sorted({r.contig for r in scope}, key=int)

    source = roh_af.inspect(
        Path(expand_path(params.af_file)), params.af_tag, params.af_build,
        params.af_release,
    )  # fmt: skip
    if source.build != build:
        raise CodedError(
            "roh_af_build_mismatch",
            f"{source.path.name} is on {source.build} but {Path(vcf).name} is on "
            f"{build}; use population frequencies for {build} (see {GUIDE}).",
        )
    imprinted = (
        roh_regions.from_bed(Path(expand_path(params.imprinted_regions)), build)
        if params.imprinted_regions
        else roh_regions.bundled(build)
    )
    has_gq = "##FORMAT=<ID=GQ," in header
    if not has_gq and params.min_gq > 0:
        raise CodedError(
            "roh_no_gq",
            f"{Path(vcf).name} has no genotype qualities (FORMAT/GQ), so "
            f"--min-gq {params.min_gq} can't be applied. Pass --min-gq 0 to use "
            "its genotypes without a quality floor.",
        )
    min_gq = params.min_gq if has_gq else None

    outdir = Path(expand_path(params.outdir or absolute_path(vcf).parent))
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{input_stem(vcf)}.roh"
    result = current_result()
    result.add_input(vcf, "input")
    result.add_input(source.path, "population_af")
    if params.imprinted_regions:
        result.add_input(expand_path(params.imprinted_regions), "imprinted_regions")
    result.expect_output(outdir / f"{stem}.tsv")
    markers_out = outdir / f"{stem}.markers.tsv.gz"
    with tempfile.TemporaryDirectory(
        dir=scratch_parent(outdir), prefix=".wgse_roh_"
    ) as tmp:
        work = Path(tmp)
        with roh_af.prepared(
            source, scope_contigs, chr_prefix, roh_af.cache_root(), work
        ) as table:
            if table.status == "reused":
                logging.info(
                    "Reusing the allele-frequency table prepared from "
                    f"{source.path.name}."
                )
            logging.info(f"Selecting {sample}'s markers in {scope_text}...")
            markers, counts = _select_markers(vcf, sample, scope_text, min_gq, work)
            logging.info("Finding runs of homozygosity with bcftools roh...")
            output, used = _run_roh(markers, table.path, work)
        segments, spans, covered = _parse_roh(output, markers_out, scope)
    if used == 0:
        raise CodedError(
            "roh_no_markers",
            f"None of {sample}'s calls in {scope_text} are at sites of "
            f"{source.path.name}. Check that both are on {build}, the scope, and "
            "--af-tag.",
        )
    whole = {r.contig for r in scope if r.whole}
    document = _document(
        params, vcf, sample, build, scope, chr_prefix, spans, covered, source,
        table, imprinted, counts, used, min_gq, segments, whole,
    )  # fmt: skip
    _write(document, outdir, stem, markers_out)


def _document(
    params: QcRohParams,
    vcf: str,
    sample: str,
    build: str,
    scope: list[Region],
    chr_prefix: bool,
    spans: dict[str, tuple[int, int]],
    covered: int,
    source: roh_af.AfSource,
    table: roh_af.PreparedTable,
    imprinted: roh_regions.RegionSet,
    counts: Counter[str],
    used: int,
    min_gq: int | None,
    segments: list[RohSegment],
    whole: set[str],
) -> roh_document.RohDocument:
    terminal_bp = round(params.flag_terminal_mb * 1e6)
    interstitial_bp = round(params.flag_interstitial_mb * 1e6)
    lengths = {bare(k): v for k, v in load_annotations(build).contigs.items()}
    rows: list[roh_document.Segment] = []
    flags: list[roh_document.Flag] = []
    for segment in segments:
        position = _position(segment, whole, spans, lengths)
        overlaps = [
            r for r in imprinted.regions
            if r.overlaps(segment.chrom, segment.start, segment.end)
        ]  # fmt: skip
        threshold = terminal_bp if position == "terminal" else interstitial_bp
        flagged = bool(
            overlaps
            and segment.length >= threshold
            and segment.markers >= FLAG_MIN_MARKERS
            and segment.quality >= FLAG_MIN_QUALITY
        )
        rows.append(
            roh_document.Segment(
                segment.chrom,
                segment.start,
                segment.end,
                segment.length,
                segment.markers,
                segment.quality,
                position,
                [r.id for r in overlaps],
                flagged,
            )  # fmt: skip
        )
        if flagged:
            kind = "terminal" if position == "terminal" else "interstitial"
            flags.append(
                roh_document.Flag(
                    code=roh_document.FLAG_CODE,
                    chrom=segment.chrom,
                    start=segment.start,
                    end=segment.end,
                    length_bp=segment.length,
                    position=position,
                    criterion=f"{kind} run of at least {threshold / 1e6:g} Mb over "
                    "an imprinted region",
                    regions=[_document_region(r) for r in overlaps],
                    message=_flag_message(segment, position, overlaps),
                )
            )
    assessed = {bare(c) for c in spans}
    denominator = covered
    froh_bp = sum(s.length for s in segments if s.length >= FROH_MIN_BP)
    # Every autosome in scope as a whole, and with markers.
    whole_genome = assessed >= set(roh_af.AUTOSOMES) and whole >= set(roh_af.AUTOSOMES)
    long_chroms = sorted(
        {s.chrom for s in segments if s.length >= LONG_BP}, key=lambda c: int(bare(c))
    )
    scope_names = [r.text(chr_prefix) for r in scope]
    notes = []
    if not whole_genome:
        notes.append(
            f"F_ROH is for the assessed scope only ({', '.join(scope_names)}; "
            f"{denominator:,} bp covered by markers), not the whole "
            "genome."
        )
    if counts["hom_ref"] == 0:
        notes.append(VARIANTS_ONLY)
    if len(long_chroms) > 1:
        notes.append(
            f"Runs of 5 Mb or more on {len(long_chroms)} chromosomes are a pattern "
            "more typical of parents who share ancestry than of uniparental disomy."
        )
    identity = table.source
    return roh_document.RohDocument(
        schema=roh_document.SCHEMA_ID,
        input=str(absolute_path(vcf)),
        sample=sample,
        build=build,
        scope=roh_document.Scope(
            regions=scope_names,
            contigs=[
                f"chr{c}" if chr_prefix else c
                for c in sorted({r.contig for r in scope}, key=int)
            ],  # fmt: skip
            contigs_without_markers=[
                f"chr{r.contig}" if chr_prefix else r.contig
                for r in scope
                if r.contig not in assessed
            ],
            declared_bp=sum(r.end - r.start + 1 for r in scope),
            denominator_bp=denominator,
            whole_genome=whole_genome,
        ),
        population_frequencies=roh_document.PopulationFrequencies(
            path=str(source.path.absolute()),
            kind=source.kind,
            tag=source.tag,
            release=source.release,
            build=source.build,
            size=identity["size"],
            digest=identity["fingerprint"],
            digest_strength=identity["digest"]["strength"],
            prepared=table.status,
            prepared_reason=table.reason,
            prepared_sites=table.rows,
        ),
        imprinted_regions=roh_document.ImprintedRegions(
            build=imprinted.build,
            source=imprinted.source,
            path=imprinted.path,
            citations=list(imprinted.citations),
            regions=[_document_region(r) for r in imprinted.regions],
        ),
        markers=roh_document.Markers(
            records=counts["records"],
            missing_genotype=counts["missing_genotype"],
            weak_genotype=counts["weak_genotype"],
            hom_ref=counts["hom_ref"],
            het=counts["het"],
            hom_alt=counts["hom_alt"],
            not_modelled=counts["records"]
            - counts["missing_genotype"]
            - counts["weak_genotype"]
            - used,
            used=used,
            min_gq=min_gq,
            reference_calls=counts["hom_ref"] > 0,
        ),
        parameters=roh_document.Parameters(
            bcftools=tool_version("bcftools"),
            gt_error_phred=GT_ERROR_PHRED,
            hw_to_az=HW_TO_AZ,
            az_to_hw=AZ_TO_HW,
            flag_terminal_bp=terminal_bp,
            flag_interstitial_bp=interstitial_bp,
            flag_min_markers=FLAG_MIN_MARKERS,
            flag_min_quality=FLAG_MIN_QUALITY,
            froh_min_bp=FROH_MIN_BP,
            max_marker_gap_bp=MAX_GAP_BP,
        ),
        summary=roh_document.Summary(
            segments=len(segments),
            total_bp=sum(s.length for s in segments),
            longest_bp=max((s.length for s in segments), default=0),
            segments_over_1mb=sum(s.length >= 1_000_000 for s in segments),
            segments_over_5mb=sum(s.length >= LONG_BP for s in segments),
            froh=round(froh_bp / denominator, 6) if denominator else None,
            chromosomes_with_long_roh=long_chroms,
        ),
        segments=rows,
        flags=flags,
        notes=notes,
    )


_COLUMNS = ("chrom", "start", "end", "length_bp", "markers", "quality",
            "position", "imprinted_regions", "flagged")  # fmt: skip


def _write(
    document: roh_document.RohDocument, outdir: Path, stem: str, markers_out: Path
) -> None:
    segments_path = outdir / f"{stem}.tsv"
    report_path = outdir / f"{stem}.json"
    with atomic_output(segments_path) as staged, open(staged, "w", newline="\n") as f:
        f.write("\t".join(_COLUMNS) + "\n")
        for s in document.segments:
            values = (s.chrom, s.start, s.end, s.length_bp, s.markers, s.quality,
                      s.position, ",".join(s.imprinted_regions) or ".",
                      "yes" if s.flagged else "no")  # fmt: skip
            f.write("\t".join(str(v) for v in values) + "\n")
    data = dataclasses.asdict(document)
    with atomic_output(report_path) as staged, open(staged, "w", newline="\n") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    result = current_result()
    result.set_manifest_stem(outdir / stem)
    result.add_output(segments_path, "roh_segments", kind="tsv")
    result.add_output(report_path, "roh_report", kind="json")
    result.add_output(markers_out, "roh_markers", kind="tsv")
    result.set_details(data)
    summary, scope = document.summary, document.scope
    result.add_metrics(
        roh_segments=summary.segments,
        roh_total_bp=summary.total_bp,
        roh_longest_bp=summary.longest_bp,
        roh_froh=summary.froh,
        roh_markers=document.markers.used,
        roh_flags=len(document.flags),
        roh_whole_genome=scope.whole_genome,
        roh_af_prepared=document.population_frequencies.prepared,
    )
    froh = f"{summary.froh:.4f}" if summary.froh is not None else "n/a"
    logging.info(
        f"{summary.segments:,} runs of homozygosity, {summary.total_bp / 1e6:.1f} Mb "
        f"in total (longest {summary.longest_bp / 1e6:.2f} Mb) from "
        f"{document.markers.used:,} markers; F_ROH {froh} "
        + (
            "(whole genome)."
            if scope.whole_genome
            else f"(assessed scope {', '.join(scope.regions)} only)."
        )
    )
    for flag in document.flags:
        warn(roh_document.FLAG_CODE, flag.message, chrom=flag.chrom, start=flag.start,
             end=flag.end, length_bp=flag.length_bp)  # fmt: skip
    if scope.contigs_without_markers:
        warn(
            "roh_contigs_without_markers",
            f"No markers on {', '.join(scope.contigs_without_markers)}; they were "
            "not assessed.",
            contigs=",".join(scope.contigs_without_markers),
        )
    if not document.markers.reference_calls:
        warn("roh_variants_only", VARIANTS_ONLY)


cmd_roh = cli_handler(QcRohParams, run_roh)
