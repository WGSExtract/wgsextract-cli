"""`qc ancestry`: admixture-style ancestry proportions of one sample.

The sample's genotypes at the ancestry reference's ~187,000 LD-pruned SNPs
(core/ancestry_reference.py) are projected onto the reference's fixed
ADMIXTURE allele frequencies at K=7 and K=12 (core/ancestry.py). Inputs:

- BAM/CRAM: reads at the sites (``bcftools mpileup``), as genotype
  likelihoods, so low coverage widens the intervals rather than biasing them;
- VCF: the sample's calls. A variants-only VCF omits hom-ref sites, so its
  missing sites are hom-ref only when it is a whole-genome call set (as `qc
  relate` decides); otherwise ancestry from it would be biased and it is
  refused. An all-sites VCF's missing sites are unknown;
- microarray raw data (23andMe, AncestryDNA, FTDNA, MyHeritage...): the
  genotypes at the chip's markers that are reference sites, on the forward
  strand of whichever of GRCh37 or GRCh38 they match.

See docs/ancestry.md.
"""

from __future__ import annotations

import io
import logging
import re
import subprocess
import tempfile
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.qc import QcAncestryParams
from wgsextract_cli.core import ancestry, ancestry_reference, sample_workspace
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.builds import build_from_chr1_length, canonical_build
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.microarray_revisions import decode_text, read_raw_bytes
from wgsextract_cli.core.paths import absolute_path, expand_path
from wgsextract_cli.core.reference_assets import (
    input_stem,
    resolve_reference_library,
)
from wgsextract_cli.core.reference_resolver import (
    header_contig_lengths,
    library_roots,
)
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.utils import (
    WGSExtractError,
    get_resource_defaults,
    popen,
    run_command,
)
from wgsextract_cli.core.variant_files import verify_paths_exist

from ..genotyping.common import open_alignment
from ._relate_vcf import (
    VARIANTS_ONLY_MAX_REF_RECORDS,
    WGS_MIN_AUTOSOMES,
    WGS_MIN_DENSITY,
    WGS_MIN_RATIO,
    is_gvcf,
    record_density,
)

FACT = "ancestry"
GUIDE = "docs/ancestry.md"
# Sites with data below which the proportions are refused, and below which
# they are reported with a warning (docs/ancestry.md, measured in #620).
MIN_SITES = 2_000
WARN_SITES = 10_000
_VCF_SUFFIXES = (".vcf", ".vcf.gz", ".vcf.bgz", ".bcf")
_PASSING = (".", "PASS")
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Genotypes:
    """The sample's data at the reference sites it covers."""

    rows: np.ndarray  # rows of the reference arrays, in genome order
    likelihoods: np.ndarray  # (n, 3)
    build: str
    method: str
    sites_available: int  # reference sites on the input's build
    depth: float | None = None  # mean reads per covered site (BAM/CRAM)


def _slug(name: str) -> str:
    return _SLUG.sub("_", name.lower()).strip("_")


def _input_kind(path: str) -> str:
    lower = path.lower()
    if lower.endswith((".bam", ".cram")):
        return "alignment"
    if lower.endswith(_VCF_SUFFIXES):
        return "vcf"
    return "microarray"


def _index(
    items: Iterable[tuple[tuple[str, int], int]],
) -> dict[tuple[str, int], list[int]]:
    """Site rows by (contig, position); a few positions have two SNPs."""
    index: dict[tuple[str, int], list[int]] = {}
    for key, row in items:
        index.setdefault(key, []).append(row)
    return index


def _given_build(params: QcAncestryParams) -> str | None:
    return canonical_build(params.build) if params.build else None


def _contig(chrom: int, chr_prefix: bool) -> str:
    return f"chr{chrom}" if chr_prefix else str(chrom)


# --- BAM / CRAM ---------------------------------------------------------------


def _pileup(
    path: str, reference: str, regions: Path, contig: str
) -> Iterator[tuple[int, str, str, list[int]]]:
    """(pos, REF, ALTs, AD) of the sites in ``regions`` (one contig's) with
    reads."""
    mpileup = ["bcftools", "mpileup", "-f", reference, "-R", str(regions), "-a", "FORMAT/AD", "-B", "-I", "-q", "20", "-Q", "20",
               "-d", "500", "-Ou", path]  # fmt: skip
    query = ["bcftools", "query", "-f", "%POS\t%REF\t%ALT\t[%AD]\n"]
    with popen(mpileup, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as pile:
        with popen(query, stdin=pile.stdout, stdout=subprocess.PIPE, text=True) as q:
            assert pile.stdout is not None and q.stdout is not None
            pile.stdout.close()
            for line in q.stdout:
                pos, ref, alts, ad = line.rstrip("\n").split("\t")
                yield (
                    int(pos),
                    ref,
                    alts,
                    [int(x) if x != "." else 0 for x in ad.split(",")],
                )
        _, error = pile.communicate()
        if pile.returncode:
            raise WGSExtractError(
                f"bcftools mpileup failed on {contig}: {error.decode()[-800:]}"
            )


def _alignment_genotypes(
    params: QcAncestryParams,
    reference: ancestry_reference.AncestryReference,
    work: Path,
) -> tuple[Genotypes, str, str | None]:
    aln = open_alignment(params, purpose="qc ancestry", any_build=True)
    fasta = aln.require_reference()
    build = _given_build(params) or aln.build
    sites = reference.sites(build)
    verify_dependencies(["bcftools"])
    by_contig: dict[str, list[int]] = {}
    for i, chrom in enumerate(sites.chrom):
        contig = _contig(int(chrom), aln.chr_prefix)
        if contig in aln.lengths:
            by_contig.setdefault(contig, []).append(i)
    if not by_contig:
        raise WGSExtractError(
            f"{Path(aln.path).name} has none of the autosomes; ancestry needs them."
        )
    for n, (contig, rows) in enumerate(by_contig.items()):
        with (work / f"sites.{n}.tsv").open("w", newline="\n") as f:
            f.writelines(f"{contig}\t{sites.pos[i]}\n" for i in rows)
    regions = {contig: work / f"sites.{n}.tsv" for n, contig in enumerate(by_contig)}
    lookup = _index(
        ((contig, int(sites.pos[i])), i)
        for contig, rows in by_contig.items()
        for i in rows
    )
    available = sum(len(rows) for rows in by_contig.values())
    counts: dict[int, tuple[int, int]] = {}

    def one(contig: str) -> list[tuple[int, int, int]]:
        found = []
        for pos, ref, alts, ad in _pileup(aln.path, fasta, regions[contig], contig):
            names = alts.split(",")
            for i in lookup.get((contig, pos), ()):
                if ref.upper() != sites.ref[i]:
                    continue
                alt = sites.alt[i]
                alt_reads = ad[names.index(alt) + 1] if alt in names else 0
                if ad[0] + alt_reads:
                    found.append((i, ad[0], alt_reads))
        return found

    logging.info(
        f"Reading {Path(aln.path).name} at {available:,} ancestry sites ({build})..."
    )
    workers = max(1, min(int(aln.threads), len(by_contig)))
    cancel = cancel_event()
    with ThreadPoolExecutor(workers) as pool:
        for found in pool.map(one, sorted(by_contig, key=lambda c: -len(by_contig[c]))):
            if cancel is not None and cancel.is_set():
                raise WGSExtractError("Cancelled.")
            counts.update((i, (r, a)) for i, r, a in found)
    order = sorted(counts)
    ref_reads = np.array([counts[i][0] for i in order])
    alt_reads = np.array([counts[i][1] for i in order])
    genotypes = Genotypes(
        rows=sites.index[order],
        likelihoods=ancestry.genotype_likelihoods(ref_reads, alt_reads),
        build=build,
        method="read counts at the sites (bcftools mpileup), as genotype likelihoods",
        sites_available=available,
        depth=float((ref_reads + alt_reads).mean()) if order else 0.0,
    )
    return genotypes, aln.path, fasta


# --- VCF ------------------------------------------------------------------------


def _vcf_sample(vcf: str, wanted: str | None) -> str:
    names = run_command(
        ["bcftools", "query", "-l", vcf], capture_output=True
    ).stdout.split()
    if wanted:
        if wanted not in names:
            raise WGSExtractError(
                f"{vcf} has no sample {wanted!r} (it has: {', '.join(names[:10])})."
            )
        return wanted
    if len(names) != 1:
        raise WGSExtractError(
            f"{vcf} has {len(names)} samples; choose one with --sample "
            f"({', '.join(names[:10])})."
        )
    return names[0]


def _dosage(gt: str, k: int | None) -> int | None:
    """Copies of ALT allele ``k`` (None: a reference-only record) in a call
    made only of REF and that allele."""
    alleles = gt.replace("|", "/").split("/")
    allowed = {"0", str(k)} if k else {"0"}
    if not gt or "." in alleles or not set(alleles) <= allowed or len(alleles) != 2:
        return None
    return sum(a == str(k) for a in alleles) if k else 0


def _vcf_genotypes(
    params: QcAncestryParams,
    reference: ancestry_reference.AncestryReference,
    work: Path,
) -> tuple[Genotypes, str, str | None]:
    vcf = expand_path(params.input)
    verify_dependencies(["bcftools"])
    header = run_command(["bcftools", "view", "-h", vcf], capture_output=True).stdout
    lengths = header_contig_lengths(header)
    build = _given_build(params) or build_from_chr1_length(lengths) or ""
    if not build:
        raise WGSExtractError(
            f"{Path(vcf).name}'s header doesn't give chr1's length, so its build "
            "is unknown; pass --build."
        )
    if is_gvcf(vcf, header):
        raise WGSExtractError(
            f"{Path(vcf).name} is a gVCF; its no-call blocks can't be told from "
            "reference blocks. Give the BAM/CRAM, or a VCF called from it."
        )
    sample = _vcf_sample(vcf, params.sample)
    sites = reference.sites(build)
    chr_prefix = "chr1" in lengths or not any(c == "1" for c in lengths)
    regions = work / "sites.tsv"
    with regions.open("w", newline="\n") as f:
        f.writelines(
            f"{_contig(int(c), chr_prefix)}\t{p}\n"
            for c, p in zip(sites.chrom, sites.pos, strict=True)
        )
    lookup = _index(
        ((_contig(int(c), chr_prefix), int(p)), i)
        for i, (c, p) in enumerate(zip(sites.chrom, sites.pos, strict=True))
    )
    query = ["bcftools", "query", "-T", str(regions), "-s", sample,
             "-f", "%CHROM\t%POS\t%REF\t%ALT\t%FILTER[\t%GT]\n", vcf]  # fmt: skip
    called: dict[int, int] = {}
    unknown: set[int] = set()
    ref_only = passing = 0
    for line in run_command(query, capture_output=True).stdout.splitlines():
        contig, pos, ref, alts, filt, gt = line.split("\t")
        names = alts.split(",")
        for i in lookup.get((contig, int(pos)), ()):
            if ref.upper() != sites.ref[i] or i in called:
                continue
            k = names.index(sites.alt[i]) + 1 if sites.alt[i] in names else None
            if k is None and not set(names) <= {".", "<*>", "<NON_REF>"}:
                continue
            dosage = _dosage(gt, k) if filt in _PASSING else None
            if dosage is None:
                unknown.add(i)
                continue
            passing += 1
            ref_only += dosage == 0
            called[i] = dosage
    variants_only = passing > 0 and ref_only <= VARIANTS_ONLY_MAX_REF_RECORDS * passing
    filled = 0
    if variants_only:
        freq = reference.pca()["frequency"][sites.index].astype(np.float64)
        expected = float((1 - (1 - freq) ** 2).sum())
        nonref = [i for i, d in called.items() if d]
        ratio = len(nonref) / expected if expected else 0.0
        autosomes = len({int(sites.chrom[i]) for i in nonref}) / 22
        density = record_density(vcf, header)
        whole_genome = (
            ratio >= WGS_MIN_RATIO
            and autosomes >= WGS_MIN_AUTOSOMES
            and density is not None
            and density >= WGS_MIN_DENSITY
        )
        if not whole_genome:
            raise WGSExtractError(
                f"{Path(vcf).name} lists only variants, and not across the whole "
                f"genome ({ratio:.0%} of the expected variant sites, on "
                f"{autosomes:.0%} of the autosomes), so a site it lacks may be "
                "hom-ref or never looked at. Ancestry from its variants alone "
                "would be biased; give the BAM/CRAM, an all-sites VCF or a "
                f"whole-genome VCF. See {GUIDE}."
            )
        for i in range(len(sites.pos)):
            if i not in called and i not in unknown:
                called[i] = 0
                filled += 1
    order = sorted(called)
    method = "VCF genotype calls"
    if filled:
        method += f" (whole-genome variants-only VCF: {filled:,} missing sites hom-ref)"
    genotypes = Genotypes(
        rows=sites.index[order],
        likelihoods=ancestry.hard_call_likelihoods(
            np.array([called[i] for i in order], dtype=np.int64)
        ),
        build=build,
        method=method,
        sites_available=len(sites.pos),
    )
    return genotypes, vcf, None


# --- Microarray raw data -------------------------------------------------------------


def _raw_calls(path: str) -> list[tuple[str, int, str]]:
    """(chromosome, position, genotype letters) of each called marker."""
    try:
        text = decode_text(read_raw_bytes(Path(path)))
    except (OSError, EOFError, UnicodeDecodeError, ValueError) as e:
        raise WGSExtractError(f"Cannot read microarray file {path}: {e}") from e
    calls = []
    for line in io.StringIO(text):
        if not line.strip() or line.startswith("#"):
            continue
        fields = [f.strip().strip('"') for f in re.split(r"[\t,]", line.rstrip("\r\n"))]
        if len(fields) < 4 or not fields[2].isdigit():
            continue
        genotype = fields[3] if len(fields) == 4 else fields[3] + fields[4]
        chrom = fields[1].removeprefix("chr")
        if (
            chrom.isdigit()
            and 1 <= int(chrom) <= 22
            and re.fullmatch("[ACGT]{2}", genotype)
        ):
            calls.append((chrom, int(fields[2]), genotype))
    if not calls:
        raise WGSExtractError(
            f"{Path(path).name} has no autosomal genotype calls in a microarray "
            "raw-data layout (ID, chromosome, position, genotype)."
        )
    return calls


def _microarray_genotypes(
    params: QcAncestryParams,
    reference: ancestry_reference.AncestryReference,
    work: Path,
) -> tuple[Genotypes, str, str | None]:
    path = expand_path(params.input)
    calls = _raw_calls(path)
    best: tuple[int, str, dict[int, int], int] | None = None
    given = _given_build(params)
    for build in (given,) if given else ("hg19", "hg38"):
        sites = reference.sites(build)
        lookup = _index(
            ((str(c), int(p)), i)
            for i, (c, p) in enumerate(zip(sites.chrom, sites.pos, strict=True))
        )
        dosages: dict[int, int] = {}
        for chrom, pos, genotype in calls:
            for i in lookup.get((chrom, pos), ()):
                if set(genotype) <= {sites.ref[i], sites.alt[i]}:
                    dosages[i] = genotype.count(sites.alt[i])
        if best is None or len(dosages) > best[0]:
            best = (len(dosages), build, dosages, len(sites.pos))
    assert best is not None
    _, build, dosages, available = best
    sites = reference.sites(build)
    order = sorted(dosages)
    genotypes = Genotypes(
        rows=sites.index[order],
        likelihoods=ancestry.hard_call_likelihoods(
            np.array([dosages[i] for i in order], dtype=np.int64)
        ),
        build=build,
        method=f"microarray genotype calls ({len(calls):,} autosomal markers, positions on {ancestry_reference.BUILDS[build]})",
        sites_available=available,
    )
    return genotypes, path, None


# --- Projection and output --------------------------------------------------------------


def _confidence(sites: int, widest: float) -> sample_workspace.Confidence:
    if sites < WARN_SITES or widest > 0.2:
        return "low"
    if widest > 0.1:
        return "medium"
    return "high"


def run_ancestry(params: QcAncestryParams) -> None:
    path = expand_path(params.input)
    verify_paths_exist({"--input": path})
    kind = _input_kind(path)
    # An external FASTA chooses only the sequence; the reference is in the
    # configured library (or the library the FASTA is in).
    root = library_roots(expand_path(params.ref) if params.ref else None)
    reflib = resolve_reference_library(root.resource_root(ancestry_reference.FOLDER))
    reference = ancestry_reference.load(reflib, cancel=cancel_event())
    outdir = Path(expand_path(params.outdir or absolute_path(path).parent))
    outdir.mkdir(parents=True, exist_ok=True)
    result = current_result()
    with tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_ancestry_") as tmp:
        work = Path(tmp)
        if kind == "alignment":
            genotypes, source, fasta = _alignment_genotypes(params, reference, work)
        elif kind == "vcf":
            genotypes, source, fasta = _vcf_genotypes(params, reference, work)
        else:
            genotypes, source, fasta = _microarray_genotypes(params, reference, work)
    result.add_input(source, "input")
    result.add_input(reference.path, "ancestry_reference")
    if fasta:
        result.set_reference(fasta)
    n = len(genotypes.rows)
    if n < MIN_SITES:
        raise WGSExtractError(
            f"Only {n:,} of the {genotypes.sites_available:,} ancestry sites have "
            f"data in {Path(source).name} (at least {MIN_SITES:,} are needed). "
            f"Is it a subset of the genome, or very low coverage? See {GUIDE}."
        )
    stem = f"{input_stem(source)}.ancestry"
    table = outdir / f"{stem}.tsv"
    threads, _ = get_resource_defaults(params.threads, None)
    projections = {}
    for k in ancestry_reference.KS:
        logging.info(f"Estimating proportions at K={k} from {n:,} sites...")
        projections[k] = ancestry.project(
            reference.frequencies(k)[genotypes.rows],
            genotypes.likelihoods,
            bootstrap=params.bootstrap,
        )
    with atomic_output(table) as staged, open(staged, "w", newline="\n") as f:
        f.write(
            "k\tcomponent\tregion\tproportion\tlower_95\tupper_95\treference_populations\n"
        )
        for k, projection in projections.items():
            components = reference.components(k)
            for j in np.argsort(-projection.proportions):
                c = components[j]
                pops = ", ".join(c.top_populations)
                f.write(
                    f"{k}\t{c.name}\t{c.region}\t{projection.proportions[j]:.4f}\t"
                    f"{projection.lower[j]:.4f}\t{projection.upper[j]:.4f}\t{pops}\n"
                )
    result.set_manifest_stem(outdir / stem)
    result.add_output(table, "ancestry_table", kind="tsv")
    k7 = projections[7]
    widest = float((k7.upper - k7.lower).max()) if params.bootstrap else 0.0
    confidence = _confidence(n, widest)
    metrics: dict[str, float | int | str | None] = {
        "ancestry_sites": n,
        "ancestry_sites_available": genotypes.sites_available,
        "ancestry_depth": round(genotypes.depth, 2)
        if genotypes.depth is not None
        else None,
        "ancestry_build": genotypes.build,
        "ancestry_reference_version": reference.version,
        "ancestry_confidence": confidence,
    }
    for k, projection in projections.items():
        for c, value, lo, hi in zip(reference.components(k), projection.proportions,
                                    projection.lower, projection.upper, strict=True):  # fmt: skip
            metrics[f"ancestry_k{k}_{_slug(c.name)}"] = round(float(value), 4)
            if params.bootstrap:
                metrics[f"ancestry_k{k}_{_slug(c.name)}_lower"] = round(float(lo), 4)
                metrics[f"ancestry_k{k}_{_slug(c.name)}_upper"] = round(float(hi), 4)
    result.add_metrics(**metrics)
    _report(projections, reference, genotypes, n)
    value = {
        c.name: round(float(q), 3)
        for c, q in zip(reference.components(7), k7.proportions, strict=True)
        if q >= 0.005
    }
    sample_workspace.record(
        FACT,
        source,
        value,
        method=f"ADMIXTURE-model projection, K=7, HGDP+1KG reference v{reference.version}, "
        f"{n:,} sites, {genotypes.method}",
        confidence=confidence,
    )
    if n < WARN_SITES:
        warn(
            "ancestry_few_sites",
            f"Only {n:,} ancestry sites have data, so the proportions are rough "
            f"(see the intervals in {table.name}).",
            sites=n,
        )


def _report(
    projections: dict[int, ancestry.Projection],
    reference: ancestry_reference.AncestryReference,
    genotypes: Genotypes,
    n: int,
) -> None:
    depth = (
        f", mean depth {genotypes.depth:.1f}x" if genotypes.depth is not None else ""
    )
    human_output(
        f"Ancestry proportions from {n:,} of {genotypes.sites_available:,} sites "
        f"({genotypes.build}{depth}). These are shares of reference populations "
        "(HGDP and 1000 Genomes), not an ethnicity."
    )
    for k, projection in projections.items():
        human_output(f"\nK={k}:")
        components = reference.components(k)
        for j in np.argsort(-projection.proportions):
            if projection.upper[j] < 0.005:
                continue
            human_output(
                f"  {components[j].name:<42} {projection.proportions[j]:6.1%}  "
                f"({projection.lower[j]:.1%}-{projection.upper[j]:.1%})"
            )


cmd_ancestry = cli_handler(QcAncestryParams, run_ancestry)
