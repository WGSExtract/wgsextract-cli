"""`typing str` on Nanopore and PacBio reads: LongTR (default) or STRkit.

Both size the repeats of the bundled catalog (str_catalog/, built by
scripts/build_str_catalog.py): ExpansionHunter's disease repeats, at its
reference regions, so their counts are in the units of ``repeats.THRESHOLDS``.
LongTR has no threads; it runs one process per chromosome.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import logging
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from pathlib import Path

from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.utils import WGSExtractError, run_command

from .common import Alignment, RepeatCall, env_tool

CATALOG = files("wgsextract_cli.commands.genotyping").joinpath("str_catalog")
# ONT R9.4.1 basecaller models, as Dorado and Guppy name them in @RG DS/@PG.
_R9 = re.compile(r"r9\.?4|r941", re.IGNORECASE)


def is_r9(header: str) -> bool:
    return bool(_R9.search(header))


def catalog_rows(aln: Alignment, caller: str, catalog: Path | None) -> list[str]:
    """The catalog's lines, with contig names as in the input (the bundled
    ones name contigs chr1, ...)."""
    if catalog:
        path = expand_path(catalog)
        if not Path(path).is_file():
            raise WGSExtractError(f"Repeat catalog not found: {path}")
        rows = [
            line
            for line in Path(path).read_text().splitlines()
            if line.strip() and not line.startswith(("#", "track", "browser"))
        ]
        for line in rows:
            if len(line.split("\t")) < 4:
                raise WGSExtractError(
                    f"Repeat catalog {path}: expected tab-separated contig, "
                    f"start, end and motif or ID=...;MOTIF=... columns: {line!r}"
                )
        return rows
    text = CATALOG.joinpath(f"{aln.build}.{caller}.bed").read_text()
    rows = []
    for line in text.splitlines():
        chrom, rest = line.split("\t", 1)
        rows.append(f"{aln.contig(chrom.removeprefix('chr'))}\t{rest}")
    return rows


# A read soft-clipped at a repeat often holds an expansion longer than the
# aligner would place as an insertion; neither caller sizes those reads.
CLIP_MIN = 100
CLIP_WINDOW = 50
CLIPPED_READS_MIN = 3
CLIPPED_FRACTION_MIN = 0.05
_CIGAR = re.compile(r"(\d+)([MIDNSHP=X])")


def _clip(ops: list[tuple[int, str]]) -> int:
    """Bases clipped at the end ``ops`` starts from (hard and soft together,
    as in 10H100S)."""
    total = 0
    for n, op in ops:
        if op not in "SH":
            break
        total += n
    return total


def clipped_reads(aln: Alignment, chrom: str, start: int, end: int) -> int:
    """Primary reads with a soft or hard clip of CLIP_MIN bases or more
    at the 0-based repeat [start, end), when there are enough of them to
    suggest an unsized expansion; else 0."""
    out = run_command(
        ["samtools", "view", "-F", "0xF04", "-q", "20", *aln.ref_opt, aln.path,
         f"{chrom}:{max(1, start + 1 - CLIP_WINDOW)}-{end + CLIP_WINDOW}"],
        capture_output=True,
    ).stdout  # fmt: skip
    reads = clipped = 0
    for line in out.splitlines():
        fields = line.split("\t", 6)
        ops = [(int(n), op) for n, op in _CIGAR.findall(fields[5])]
        if not ops:
            continue
        reads += 1
        left = int(fields[3]) - 1
        right = left + sum(n for n, op in ops if op in "MDN=X")
        if any(
            _clip(end_ops) >= CLIP_MIN
            and start - CLIP_WINDOW <= at <= end + CLIP_WINDOW
            for end_ops, at in ((ops, left), (ops[::-1], right))
        ):
            clipped += 1
    if clipped >= CLIPPED_READS_MIN and clipped >= CLIPPED_FRACTION_MIN * reads:
        return clipped
    return 0


def with_clipped_reads(
    aln: Alignment, rows: list[str], calls: list[RepeatCall], start_base: int
) -> list[RepeatCall]:
    """``calls`` plus a no-call for every catalog repeat without one, each
    with its count of clipped reads. ``start_base`` is 1 for LongTR's
    1-based catalog starts, 0 for STRkit's BED."""
    by_id = {call.variant_id: call for call in calls}
    result = []
    for row in rows:
        chrom, start, end, *rest = row.split("\t")
        name = rest[-1] if start_base else rest[0].split(";")[0].removeprefix("ID=")
        begin = int(start) - start_base
        call = by_id.get(name) or RepeatCall(
            variant_id=name,
            repeat_unit=re.split("[,;]", rest[0].split("MOTIF=")[-1])[0],
            region=f"{chrom}:{begin}-{end}",
            alleles=(),
            intervals=None,
            coverage=0.0,
        )
        result.append(
            dataclasses.replace(
                call, clipped_reads=clipped_reads(aln, chrom, begin, int(end))
            )
        )
    return result


def _failure(tool: str, error: subprocess.CalledProcessError) -> WGSExtractError:
    lines = (error.stderr or "").strip().splitlines()
    return WGSExtractError(
        f"{tool} failed (exit {error.returncode})"
        + (f": {lines[-1]}" if lines else ".")
    )


def run_longtr(
    aln: Alignment, reference: str, rows: list[str], sex: str, prefix: str
) -> str:
    """LongTR's calls, one process per chromosome, merged into
    ``<prefix>.vcf.gz``; returns its path."""
    longtr = env_tool("LongTR", "longtr")
    chroms = [c for c in aln.lengths if any(r.split("\t", 1)[0] == c for r in rows)]
    if not chroms:
        raise WGSExtractError(
            "None of the catalog's repeats is on the input's contigs."
        )
    haploid = [aln.contig("Y")] + ([aln.contig("X")] if sex == "male" else [])
    output = f"{prefix}.vcf.gz"
    label = aln.stem.replace(",", "_")  # LongTR splits these at commas.
    workers = max(1, min(int(aln.threads), len(chroms)))
    with tempfile.TemporaryDirectory(prefix=".longtr_", dir=aln.outdir) as work:
        regions = Path(work, "regions.bed")
        regions.write_text("".join(f"{row}\n" for row in rows), newline="\n")
        # One path per line: --bams splits at commas.
        bams = Path(work, "bams.txt")
        bams.write_text(f"{aln.path}\n", newline="\n")

        def call(chrom: str) -> str:
            vcf = str(Path(work, f"{chroms.index(chrom)}.vcf.gz"))
            try:
                run_command(
                    [*longtr, "--bam-files", bams, "--fasta", reference,
                     "--regions", regions, "--tr-vcf", vcf, "--chrom", chrom,
                     "--bam-samps", label, "--bam-libs", label,
                     "--haploid-chrs", ",".join(haploid), "--quiet"],
                    capture_output=True,
                )  # fmt: skip
            except subprocess.CalledProcessError as e:
                raise _failure("LongTR", e) from e
            return vcf

        logging.info(
            f"Sizing repeat expansions with LongTR ({len(chroms)} chromosomes, "
            f"{workers} at a time)..."
        )
        with ThreadPoolExecutor(workers) as pool:
            futures = [pool.submit(call, chrom) for chrom in chroms]
            vcfs = []
            for future in futures:
                vcfs.append(future.result())
                checkpoint()
        run_command(
            ["bcftools", "concat", "--no-version", "-Oz", "-o", output, *vcfs],
            capture_output=True,
        )
    run_command(["bcftools", "index", "-f", "-t", output], capture_output=True)
    return output


def parse_longtr(vcf: str) -> list[RepeatCall]:
    calls = []
    with gzip.open(vcf, "rt") as f:
        for line in f:
            if line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            info = dict(
                item.split("=", 1) for item in fields[7].split(";") if "=" in item
            )
            sample = dict(zip(fields[8].split(":"), fields[9].split(":"), strict=False))
            gb = sample.get("GB", ".")
            if "." in gb:
                continue
            start, end, period = (int(info[k]) for k in ("START", "END", "PERIOD"))
            length = end - start + 1
            diffs = sorted(int(d) for d in re.split("[|/]", gb))
            # MALLREADS: each read's most likely bp difference, as diff|reads.
            reads_at = {
                int(diff): int(n)
                for diff, n in (
                    item.split("|")
                    for item in sample.get("MALLREADS", "").split(";")
                    if "|" in item
                )
            }
            calls.append(
                RepeatCall(
                    variant_id=fields[2],
                    repeat_unit=info["MOTIF"].split(",")[0],
                    region=f"{fields[0]}:{start - 1}-{end}",
                    alleles=tuple(round((length + d) / period) for d in diffs),
                    intervals=None,
                    coverage=float(sample.get("DP") or 0),
                    allele_reads=tuple(reads_at.get(d, 0) for d in diffs)
                    if reads_at
                    else None,
                )
            )
    return calls


def run_strkit(
    aln: Alignment,
    reference: str,
    rows: list[str],
    sex: str,
    prefix: str,
    platform: str | None,
) -> tuple[str, str]:
    """STRkit's calls: (``<prefix>.vcf``, ``<prefix>.strkit.json``). PacBio
    HiFi reads are realigned to recover expansions the aligner soft-clipped
    (STRkit recommends that for HiFi only)."""
    strkit = env_tool("strkit", "strkit")
    vcf, report = f"{prefix}.vcf", f"{prefix}.strkit.json"
    with tempfile.TemporaryDirectory(prefix=".strkit_", dir=aln.outdir) as work:
        loci = Path(work, "loci.bed")
        loci.write_text("".join(f"{row}\n" for row in rows), newline="\n")
        logging.info("Sizing repeat expansions with STRkit...")
        try:
            run_command(
                [*strkit, "call", aln.path, "--ref", reference, "--loci", loci,
                 "--sample-id", aln.stem, "--json", report, "--vcf", vcf,
                 "--processes", aln.threads, "--respect-ref", "--seed", "1",
                 "--sex-chr", "XY" if sex == "male" else "XX",
                 "--log-level", "warning",
                 *(["--realign"] if platform == "pacbio" else [])],
                capture_output=True,
            )  # fmt: skip
        except subprocess.CalledProcessError as e:
            raise _failure("STRkit", e) from e
    return vcf, report


def parse_strkit(report: str) -> list[RepeatCall]:
    with open(report) as f:
        results = json.load(f).get("results", [])
    calls = []
    for locus in results:
        call, cis = locus.get("call"), locus.get("call_95_cis")
        if not call or None in call:
            continue
        # One peak for a homozygous call: both alleles share its reads.
        peak_reads = (locus.get("peaks") or {}).get("n_reads") or []
        if len(peak_reads) == 1:
            peak_reads = peak_reads * len(call)
        calls.append(
            RepeatCall(
                variant_id=locus["locus_id"],
                repeat_unit=locus["motif"],
                region=f"{locus['contig']}:{locus['start']}-{locus['end']}",
                alleles=tuple(int(a) for a in call),
                intervals=tuple((int(lo), int(hi)) for lo, hi in cis) if cis else None,
                coverage=float(len(locus.get("reads") or {})),
                allele_reads=tuple(int(n) for n in peak_reads)
                if len(peak_reads) == len(call)
                else None,
            )
        )
    return calls
