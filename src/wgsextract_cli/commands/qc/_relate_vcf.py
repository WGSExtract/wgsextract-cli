"""A VCF's calls at somalier's sites, as a VCF somalier sketches.

A provider's VCF lists only variants, so a site it lacks is usually
homozygous reference. That holds only for a whole-genome call set: in a
one-chromosome, exome or panel VCF a missing site was never looked at. So a
missing autosomal site is written as a hom-ref call only for a sample that
the file shows to be a whole-genome, variants-only call set. Otherwise it
stays unknown, and the sample is compared on its called sites alone.

Whole genome: the sample has non-reference calls on (nearly) every autosome,
at (nearly) as many of the sites as their allele frequencies predict
(1 - (1 - AF)^2 each). Whole-genome VCFs reach 0.89-1.00 of that (GIAB
HG002/HG003/HG004 v4.2.1 0.98, 1000 Genomes NA19240 0.89 and HG00513 0.92);
HG002's chr1-6 reach 0.38 and its chr20 0.03 (out/614/thr). Variants only:
(nearly) every record at the sites is non-reference in some sample, as in a
single-sample or joint-called variant VCF; an all-sites VCF's missing sites
weren't callable, so they stay unknown.
"""

from __future__ import annotations

import gzip
import statistics
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.utils import WGSExtractError, popen, run_command

# Share of the expected non-reference sites a whole-genome sample reaches.
WGS_MIN_RATIO = 0.8
# Share of the sites' autosomes on which it has non-reference calls.
WGS_MIN_AUTOSOMES = 0.95
# Records per autosomal megabase of the whole file. somalier's sites are
# chosen to work on exomes, so an exome VCF reaches the site ratio (GIAB HG002
# v4.2.1 in the xGen exome +-100 bp: 0.90 on 22/22 autosomes) and only the
# rest of the genome tells it apart: HG002's whole benchmark VCF has 1,408
# records/Mb, the same in the xGen exome 25/Mb, chr20 alone 30/Mb.
WGS_MIN_DENSITY = 400.0
# Share of the records at the sites that may be reference-only calls.
VARIANTS_ONLY_MAX_REF_RECORDS = 0.05
_REF_ONLY_ALTS = (".", "<*>", "<NON_REF>")
# Filters somalier itself accepts.
_PASSING = (".", "PASS", "RefCall")


@dataclass(frozen=True)
class Site:
    contig: str
    pos: int
    ref: str
    alt: str
    af: float | None


@dataclass(frozen=True)
class Coverage:
    """How one VCF sample covers the sites, and whether its missing autosomal
    sites were written as hom-ref."""

    sample: str
    ratio: float | None
    autosomes: float
    # Records per autosomal megabase in the whole file (None: unknown).
    density: float | None
    # Missing sites mean "not a variant", not "not looked at": an all-sites
    # VCF's hom-ref calls are real, and its missing sites stay unknown.
    variants_only: bool
    filled: bool


def is_sex_contig(contig: str) -> bool:
    return contig.removeprefix("chr") in ("X", "Y")


def read_sites(path: str) -> list[Site]:
    compressed = path.lower().endswith((".gz", ".bgz"))
    with gzip.open(path, "rt") if compressed else open(path) as f:
        sites = []
        for line in f:
            if line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            info = dict(kv.split("=", 1) for kv in fields[7].split(";") if "=" in kv)
            try:
                af: float | None = float(info["AF"].split(",")[0])
            except (KeyError, ValueError):
                af = None
            sites.append(Site(fields[0], int(fields[1]), fields[3], fields[4], af))
        return sites


def is_gvcf(vcf: str, header: str, records: int = 10_000) -> bool:
    """Whether the VCF has reference blocks (END, or a symbolic-only ALT such
    as <*> or <NON_REF>) among its first ``records`` records."""
    if "##INFO=<ID=END," not in header and "<NON_REF>" not in header:
        return False
    end = "%INFO/END" if "##INFO=<ID=END," in header else "."
    command = ["bcftools", "query", "-f", f"%ALT\t{end}\n", vcf]
    with popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    ) as proc:
        assert proc.stdout is not None
        try:
            for _, line in zip(range(records), proc.stdout, strict=False):
                alt, block_end = line.rstrip("\n").split("\t")
                if block_end not in (".", "") or alt in _REF_ONLY_ALTS[1:]:
                    return True
                if alt.endswith(",<NON_REF>") or alt.endswith(",<*>"):
                    return True
        finally:
            proc.kill()
    return False


def _int(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def _call(
    gt: str, ad: str, dp: str, k: int | None, typical: int
) -> tuple[int, int] | None:
    """(ref, alt) read counts for a call at a site whose alt is allele ``k``
    (None: a reference-only record), split as the genotype says: the caller
    has weighed the reads, and AD can disagree with GT (GIAB's sums several
    technologies). None unless the genotype is called and made only of REF
    and that ALT."""
    alleles = gt.replace("|", "/").split("/")
    allowed = {"0", str(k)} if k else {"0"}
    if not gt or "." in alleles or not set(alleles) <= allowed:
        return None
    n_alt = sum(a == str(k) for a in alleles) if k else 0
    depth = sum(c for c in map(_int, ad.split(",")) if c) or _int(dp) or typical
    return depth * (len(alleles) - n_alt) // len(alleles), depth * n_alt // len(alleles)


def _alt_reads(row: list[tuple[int, int] | None] | None, i: int) -> bool:
    call = row[i] if row else None
    return bool(call and call[1])


def _query(vcf: str, header: str, sites: str) -> str:
    """CHROM, POS, REF, ALT, FILTER, then GT, AD and DP of each sample ("."
    for a tag the VCF lacks), for the records at the sites."""
    cells = "".join(
        f"\t%{tag}" if f"##FORMAT=<ID={tag}," in header else "\t."
        for tag in ("GT", "AD", "DP")
    )
    fmt = f"%CHROM\t%POS\t%REF\t%ALT\t%FILTER[{cells}]\n"
    command = ["bcftools", "query", "-T", sites, "-f", fmt, vcf]
    return run_command(command, capture_output=True).stdout


def _autosome(contig: str) -> bool:
    name = contig.removeprefix("chr")
    return name.isdigit() and 1 <= int(name) <= 22


def record_density(vcf: str, header: str) -> float | None:
    """Records per megabase of the autosomes the header lists with lengths,
    from the index's counts (else by reading the records)."""
    from wgsextract_cli.core.reference_resolver import header_contig_lengths

    lengths = {c: n for c, n in header_contig_lengths(header).items() if _autosome(c)}
    if not lengths:
        return None
    counts: dict[str, int] = {}
    stats = run_command(
        ["bcftools", "index", "-s", vcf], capture_output=True, check=False
    )
    if stats.returncode == 0 and stats.stdout.strip():
        for line in stats.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) >= 3 and fields[2].isdigit():
                counts[fields[0]] = int(fields[2])
    else:
        names = run_command(
            ["bcftools", "query", "-f", "%CHROM\n", vcf], capture_output=True
        ).stdout
        for contig in names.splitlines():
            counts[contig] = counts.get(contig, 0) + 1
    records = sum(n for c, n in counts.items() if c in lengths)
    return records / (sum(lengths.values()) / 1e6)


def prepare(
    vcf: str, header: str, sites_path: str, folder: Path, names: list[str]
) -> tuple[str, list[Coverage]]:
    """A bgzipped VCF of ``vcf``'s calls at the sites (REF and the site's ALT
    only), with missing autosomal sites hom-ref for whole-genome samples."""
    if is_gvcf(vcf, header):
        raise WGSExtractError(
            f"{vcf} is a gVCF; qc relate can't tell its no-call blocks from "
            "reference blocks. Give the BAM/CRAM, or a variants-only VCF called "
            "from it."
        )
    sites = read_sites(sites_path)
    text = _query(vcf, header, sites_path)
    records: dict[tuple[str, int], list[list[str]]] = defaultdict(list)
    for line in text.splitlines():
        fields = line.split("\t")
        records[fields[0], int(fields[1])].append(fields)

    n = len(names)
    depths: list[list[int]] = [[] for _ in range(n)]
    for fields_list in records.values():
        for fields in fields_list:
            for i in range(n):
                ad, dp = fields[6 + 3 * i], fields[7 + 3 * i]
                total = sum(c for c in map(_int, ad.split(",")) if c) or _int(dp)
                if total:
                    depths[i].append(total)
    typical = [max(10, int(statistics.median(d))) if d else 20 for d in depths]

    matched: dict[Site, tuple[list[str], int | None]] = {}
    for site in sites:
        best: tuple[list[str], int | None] | None = None
        for fields in records.get((site.contig, site.pos), []):
            alts = fields[3].split(",")
            if fields[2] != site.ref:
                continue
            if site.alt in alts:
                best = (fields, alts.index(site.alt) + 1)
                break
            if best is None and set(alts) <= set(_REF_ONLY_ALTS):
                best = (fields, None)
        if best is not None:
            matched[site] = best

    calls: dict[Site, list[tuple[int, int] | None]] = {}
    for site, (fields, k) in matched.items():
        if fields[4] not in _PASSING:
            # Looked at, but not trusted: unknown, never filled.
            calls[site] = [None] * n
            continue
        calls[site] = [
            _call(
                fields[5 + 3 * i], fields[6 + 3 * i], fields[7 + 3 * i], k, typical[i]
            )
            for i in range(n)
        ]
    # DeepVariant's RefCall rows are candidates it called reference; they
    # don't make a VCF an all-sites one.
    passing = [calls[s] for s, (f, _k) in matched.items() if f[4] in (".", "PASS")]
    ref_only = sum(1 for row in passing if not any(x and x[1] for x in row))
    variants_only = bool(passing) and (
        ref_only <= VARIANTS_ONLY_MAX_REF_RECORDS * len(passing)
    )

    autosomal = [s for s in sites if not is_sex_contig(s.contig)]
    contigs = {s.contig for s in autosomal}
    afs = [s.af for s in autosomal]
    expected = (
        sum(1 - (1 - af) ** 2 for af in afs if af is not None)
        if afs and None not in afs
        else None
    )
    density = record_density(vcf, header) if variants_only else None
    coverage = []
    for i, name in enumerate(names):
        nonref = [s for s in autosomal if _alt_reads(calls.get(s), i)]
        ratio = len(nonref) / expected if expected else None
        on = len({s.contig for s in nonref}) / len(contigs) if contigs else 0.0
        filled = (
            variants_only
            and ratio is not None
            and ratio >= WGS_MIN_RATIO
            and on >= WGS_MIN_AUTOSOMES
            and density is not None
            and density >= WGS_MIN_DENSITY
        )
        coverage.append(Coverage(name, ratio, on, density, variants_only, filled))

    out = folder / "calls.vcf"
    with out.open("w", newline="\n") as f:
        f.write("##fileformat=VCFv4.2\n")
        f.write('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n')
        f.write('##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Depths">\n')
        for contig in dict.fromkeys(s.contig for s in sites):
            f.write(f"##contig=<ID={contig}>\n")
        f.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t")
        f.write("\t".join(names) + "\n")
        for site in sites:
            row = calls.get(site)
            if row is None:
                if is_sex_contig(site.contig):
                    continue
                row = [
                    (typical[i], 0) if c.filled else None
                    for i, c in enumerate(coverage)
                ]
            if not any(row):
                continue
            cells = [
                "./.:."
                if call is None
                else f"{'0/1' if all(call) else '1/1' if call[1] else '0/0'}:{call[0]},{call[1]}"
                for call in row
            ]
            f.write(
                f"{site.contig}\t{site.pos}\t.\t{site.ref}\t{site.alt}\t.\tPASS\t.\tGT:AD\t"
            )
            f.write("\t".join(cells) + "\n")
    gz = f"{out}.gz"
    with open(gz, "wb") as dest:
        run_command(["bgzip", "-c", str(out)], stdout=dest)
    run_command(["tabix", "-f", "-p", "vcf", gz])
    return gz, coverage
