"""Layered VCF reference model and Major Build detection (header first, then body sampling).

Detection layers, strongest first:
1. VCF header ``##contig=<ID=...,length=...>`` MD5 signature matching ``REFERENCE_MODELS``
   (exact reference model code + tag, identical to BAM ``@SQ`` hashing).
2. VCF header ``##contig`` chr1 length, unique contig count, ``##assembly=``, or
   ``##reference=`` filename (Major Build: GRCh37, GRCh38, T2T CHM13, etc.).
3. Streaming body sampling (up to ``max_snps`` SNVs; stops immediately once sampled):
   a. REF-allele fingerprinting against installed reference FASTAs (``samtools faidx``),
      which works even on raw unannotated VCFs without rsIDs and across all installed
      Major Builds (GRCh37, GRCh38, T2T CHM13, T2T HG002, T2T PR1).
   b. rsID -> coordinate matching against installed offline dbSNP SQLite lookup indexes
      and a compact bundled table of verified diagnostic SNPs.
   c. Chromosome coordinate bounds (e.g. chr1 > 248,956,422 rules out GRCh38 & CHM13).
"""

from __future__ import annotations

import gzip
import logging
import os
import re
import sqlite3
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.builds import (
    CHR1_LENGTH_BUILDS,
    build_from_path,
    canonical_build,
    display_build,
    reference_header_name,
)
from wgsextract_cli.core.constants import MODEL_TAGS_BY_CODE, REFERENCE_MODELS
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.ref_library.assets import (
    fasta_signature,
    read_fai_lengths,
    reference_genomes_dir,
)
from wgsextract_cli.core.ref_library.models import (
    ReferenceModel,
    identify_reference,
    sequence_lengths,
)
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.core.variant_files import calculate_bam_md5

_RSID_RE = re.compile(r"^rs(\d{1,12})$", re.IGNORECASE)
_FASTA_SUFFIXES = (".fa.gz", ".fna.gz", ".fasta.gz", ".fa", ".fna", ".fasta")

# Verified diagnostic SNPs (checked against hs37d5.fa.gz and hg38.fa.gz):
# rsid -> (canonical_chrom_without_chr, {"hg19": pos, "hg38": pos})
DIAGNOSTIC_RSIDS: dict[str, tuple[str, dict[str, int]]] = {
    "rs429358": ("19", {"hg19": 45411941, "hg38": 44908684}),
    "rs7412": ("19", {"hg19": 45412079, "hg38": 44908822}),
    "rs12913832": ("15", {"hg19": 28365618, "hg38": 28120472}),
    "rs4988235": ("2", {"hg19": 136608646, "hg38": 135851076}),
    "rs1426654": ("15", {"hg19": 48426484, "hg38": 48134287}),
    "rs16891982": ("5", {"hg19": 33951693, "hg38": 33951588}),
    "rs1801133": ("1", {"hg19": 11856378, "hg38": 11796321}),
    "rs1801131": ("1", {"hg19": 11854476, "hg38": 11794419}),
    "rs6025": ("1", {"hg19": 169519049, "hg38": 169549811}),
    "rs1799945": ("6", {"hg19": 26091179, "hg38": 26090951}),
    "rs1800562": ("6", {"hg19": 26093141, "hg38": 26092913}),
    "rs762551": ("15", {"hg19": 75041917, "hg38": 74749576}),
    "rs4680": ("22", {"hg19": 19951271, "hg38": 19963748}),
    "rs1815739": ("11", {"hg19": 66328095, "hg38": 66560624}),
    "rs3827760": ("2", {"hg19": 109513601, "hg38": 108897145}),
    "rs671": ("12", {"hg19": 112241766, "hg38": 111803962}),
}


@dataclass(frozen=True)
class SampledVcfSite:
    chrom: str
    pos: int
    rsid: str | None
    ref: str


@dataclass(frozen=True)
class VcfReferenceDetection:
    model: ReferenceModel | None
    build: str | None  # internal canonical build ("hg19", "hg38", "t2t") or None
    major_build: (
        str | None
    )  # user-facing Major Build ("GRCh37", "GRCh38", "T2T CHM13", ...)
    signature: str | None
    naming: str  # "Chr", "Num", or ""
    evidence: str  # e.g. "header signature", "body REF match (248/250 vs GRCh38)"

    def describe(self) -> str:
        if self.model is not None:
            tag = f" [{self.model.tag}]" if self.model.tag else ""
            naming = f" ({self.model.naming})" if self.model.naming else ""
            parts = [f"{self.model.name}{tag}{naming}"]
            if self.model.mito:
                parts.append(self.model.mito)
            parts.append(f"{self.model.contigs} SNs")
            base = ", ".join(parts)
            return f"{base} ({self.evidence})" if self.evidence else base
        if self.major_build:
            naming = f" ({self.naming})" if self.naming else ""
            return (
                f"{self.major_build}{naming} ({self.evidence})"
                if self.evidence
                else f"{self.major_build}{naming}"
            )
        return "Unknown"


def _canonical_chrom(name: str) -> str:
    clean = name.strip()
    if clean.lower().startswith("chr"):
        clean = clean[3:]
    if clean.upper() in ("M", "MT"):
        return "M"
    return clean.upper()


def _naming_from_chroms(chroms: Sequence[str]) -> str:
    if any(c.startswith(("chr", "CHR")) for c in chroms):
        return "Chr"
    if any(c in {"1", "2", "X", "Y", "MT"} for c in chroms):
        return "Num"
    return ""


def _read_vcf_header(vcf_path: str) -> str:
    bcftools = get_tool_path("bcftools")
    if bcftools:
        res = run_command(
            [bcftools, "view", "-h", vcf_path], capture_output=True, check=False
        )
        if res.returncode == 0 and res.stdout:
            return res.stdout
    # Plain/gzip fallback when bcftools is unavailable or header-only reading suffices.
    path = Path(vcf_path)
    if not path.is_file() or path.suffix.lower() == ".bcf":
        return ""
    try:
        opener = gzip.open if path.suffix.lower() in (".gz", ".bgz") else open
        lines: list[str] = []
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("#"):
                    lines.append(line)
                else:
                    break
        return "".join(lines)
    except OSError:
        return ""


def _stream_vcf_snps(
    vcf_path: str, *, max_snps: int = 250, max_records: int = 10_000
) -> tuple[list[SampledVcfSite], dict[str, int], str]:
    """Stream up to ``max_snps`` SNVs from the VCF body without scanning the whole file."""
    sites: list[SampledVcfSite] = []
    max_pos: dict[str, int] = {}
    seen_chroms: list[str] = []
    seen_set: set[str] = set()

    def _consume(lines: Iterator[str]) -> None:
        records = 0
        for line in lines:
            if not line or line.startswith("#"):
                continue
            records += 1
            parts = line.split("\t", 5)
            if len(parts) < 4:
                continue
            chrom, pos_str, vid, ref = parts[0], parts[1], parts[2], parts[3].upper()
            if not pos_str.isdigit():
                continue
            pos = int(pos_str)
            if chrom not in seen_set:
                seen_set.add(chrom)
                seen_chroms.append(chrom)
            canon = _canonical_chrom(chrom)
            if pos > max_pos.get(canon, 0):
                max_pos[canon] = pos
            rsid: str | None = None
            if vid and vid != ".":
                for token in vid.split(";"):
                    if _RSID_RE.match(token):
                        rsid = token.lower()
                        break
            if len(ref) == 1 and ref in {"A", "C", "G", "T"} and len(sites) < max_snps:
                sites.append(SampledVcfSite(chrom=chrom, pos=pos, rsid=rsid, ref=ref))
            if len(sites) >= max_snps and records >= max_snps:
                break
            if records >= max_records:
                break

    path = Path(vcf_path)
    if path.suffix.lower() == ".bcf":
        bcftools = get_tool_path("bcftools")
        if bcftools:
            proc = subprocess.Popen(
                [bcftools, "view", "-H", vcf_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                assert proc.stdout is not None
                _consume(iter(proc.stdout))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
    else:
        try:
            opener = gzip.open if path.suffix.lower() in (".gz", ".bgz") else open
            with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
                _consume(iter(fh))
        except OSError:
            logging.debug("Unable to stream VCF body from %s", vcf_path, exc_info=True)

    return sites, max_pos, _naming_from_chroms(seen_chroms)


def _candidate_fastas(
    ref_lib_dir: str | None, reference_fasta: str | None
) -> list[tuple[str, str, Path]]:
    """Return one indexed FASTA per Major Build: [(major_build, canonical_build, fasta_path)]."""
    candidates: list[Path] = []
    if reference_fasta and Path(reference_fasta).is_file():
        candidates.append(Path(reference_fasta))
    if ref_lib_dir:
        genomes_dir = Path(reference_genomes_dir(ref_lib_dir))
        if genomes_dir.is_dir():
            for entry in sorted(genomes_dir.iterdir()):
                if entry.is_file() and any(
                    entry.name.lower().endswith(s) for s in _FASTA_SUFFIXES
                ):
                    candidates.append(entry)

    by_major: dict[str, tuple[str, Path]] = {}
    for fasta in candidates:
        if not Path(str(fasta) + ".fai").is_file():
            continue
        lengths = read_fai_lengths(str(fasta))
        if not lengths:
            continue
        sig = fasta_signature(str(fasta))
        model_info = REFERENCE_MODELS.get(sig) if sig else None
        code = model_info[0] if model_info else ""
        tag = MODEL_TAGS_BY_CODE.get(code, "")
        chr1 = next((lengths[n] for n in ("chr1", "1", "CHR1") if n in lengths), None)
        canon = CHR1_LENGTH_BUILDS.get(chr1) if chr1 is not None else None
        major: str | None
        if tag in ("T2T HG002", "T2T PR1"):
            major = tag
        else:
            major = display_build(canon or code or build_from_path(fasta.name))
        if not major or major in ("Dog", "Cat"):
            continue
        if major not in by_major:
            by_major[major] = (canon or canonical_build(major) or "", fasta)
    return [(major, canon, fasta) for major, (canon, fasta) in by_major.items()]


def _match_ref_bases(
    sites: Sequence[SampledVcfSite],
    candidates: Sequence[tuple[str, str, Path]],
) -> tuple[str, str, int, int, int | None] | None:
    """Match sampled SNV REF alleles against candidate FASTAs via samtools faidx.

    Returns (major_build, canonical_build, matched, compared, runner_up_matched) or None.
    """
    samtools = get_tool_path("samtools")
    if not samtools or not sites or not candidates:
        return None

    scores: list[tuple[float, int, int, str, str]] = []
    for major, canon, fasta in candidates:
        lengths = read_fai_lengths(str(fasta))
        if not lengths:
            continue
        # Map canonical chrom -> exact contig name in this FASTA
        contig_map: dict[str, str] = {}
        for name in lengths:
            c = _canonical_chrom(name)
            contig_map.setdefault(c, name)

        queries: list[tuple[str, str]] = []
        for site in sites:
            target_chrom = contig_map.get(_canonical_chrom(site.chrom))
            if not target_chrom or site.pos > lengths[target_chrom]:
                continue
            queries.append((f"{target_chrom}:{site.pos}-{site.pos}", site.ref))
        if len(queries) < 10:
            continue

        # Pass regions in batches of 200 to stay well under argv limits on all OSes.
        matched = 0
        compared = 0
        for offset in range(0, len(queries), 200):
            batch = queries[offset : offset + 200]
            regions = [r for r, _ in batch]
            res = run_command(
                [samtools, "faidx", str(fasta), *regions],
                capture_output=True,
                check=False,
            )
            if res.returncode != 0 or not res.stdout:
                continue
            fetched: dict[str, str] = {}
            current_region: str | None = None
            for line in res.stdout.splitlines():
                if line.startswith(">"):
                    current_region = line[1:].strip().split()[0]
                    fetched[current_region] = ""
                elif current_region is not None:
                    fetched[current_region] += line.strip().upper()
            for region, expected_ref in batch:
                base = fetched.get(region, "")
                if len(base) == 1 and base in {"A", "C", "G", "T"}:
                    compared += 1
                    if base == expected_ref:
                        matched += 1

        if compared >= 10:
            scores.append((matched / compared, matched, compared, major, canon))

    if not scores:
        return None
    scores.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_rate, best_match, best_total, best_major, best_canon = scores[0]
    if best_rate < 0.90:
        return None
    runner_up_match: int | None = None
    if len(scores) > 1:
        second_rate, second_match, _, _, _ = scores[1]
        if best_rate - second_rate < 0.25:
            return None
        runner_up_match = second_match
    return best_major, best_canon, best_match, best_total, runner_up_match


def _match_rsids(
    sites: Sequence[SampledVcfSite], ref_lib_dir: str | None
) -> tuple[str, str, int, int] | None:
    """Match sampled (rsID, chrom, pos) records against local dbSNP SQLite and bundled panel."""
    rsid_sites = [s for s in sites if s.rsid]
    if not rsid_sites:
        return None

    counts: dict[str, int] = {"hg19": 0, "hg38": 0}
    tested: dict[str, int] = {"hg19": 0, "hg38": 0}
    sqlite_tested: dict[str, set[str]] = {"hg19": set(), "hg38": set()}

    # 1. Check installed offline dbSNP SQLite indexes (fast indexed primary-key lookup).
    if ref_lib_dir:
        from wgsextract_cli.core.dbsnp import find_dbsnp_sqlite

        for build in ("hg38", "hg19"):
            db_path = find_dbsnp_sqlite(ref_lib_dir, build)
            if not db_path:
                continue
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    cur = conn.cursor()
                    for site in rsid_sites:
                        assert site.rsid is not None
                        m = _RSID_RE.match(site.rsid)
                        if not m:
                            continue
                        row = cur.execute(
                            "SELECT c.name, r.pos FROM rsids r JOIN chroms c ON c.cid = r.cid WHERE r.rsid = ?",
                            (int(m.group(1)),),
                        ).fetchone()
                        if row is not None:
                            sqlite_tested[build].add(site.rsid)
                            tested[build] += 1
                            if (
                                _canonical_chrom(str(row[0]))
                                == _canonical_chrom(site.chrom)
                                and int(row[1]) == site.pos
                            ):
                                counts[build] += 1
                finally:
                    conn.close()
            except sqlite3.Error:
                logging.debug("dbSNP lookup skipped for %s", db_path, exc_info=True)

    # 2. Also check our compact bundled diagnostic rsID panel (works with zero downloads).
    for site in rsid_sites:
        assert site.rsid is not None
        diag = DIAGNOSTIC_RSIDS.get(site.rsid)
        if not diag:
            continue
        diag_chrom, positions = diag
        for build, pos in positions.items():
            if site.rsid in sqlite_tested[build]:
                continue
            tested[build] += 1
            if _canonical_chrom(site.chrom) == diag_chrom and site.pos == pos:
                counts[build] += 1

    ranked = [
        (counts[b] / tested[b], counts[b], tested[b], b)
        for b in ("hg38", "hg19")
        if tested[b] >= 3 and counts[b] >= 3
    ]
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    rate, matched, total, build = ranked[0]
    if rate < 0.80:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.25:
        return None
    return display_build(build) or build, build, matched, total


def identify_vcf_reference(
    vcf_path: str | os.PathLike[str],
    *,
    ref_lib_dir: str | None = None,
    reference_fasta: str | None = None,
    header: str | None = None,
    allow_body_sampling: bool = True,
    max_snps: int = 250,
) -> VcfReferenceDetection:
    """Identify a VCF's reference model (header first) or Major Build (body sampling fallback)."""
    path_str = os.fspath(vcf_path)
    hdr = header if header is not None else _read_vcf_header(path_str)
    lengths = sequence_lengths(hdr)
    signature = calculate_bam_md5(path_str, header=hdr) if lengths else None

    # Layer 1 & 2: Header signature, chr1 length, contig count.
    model = identify_reference(hdr, signature, None)
    if model is not None:
        canon = canonical_build(model.name) or canonical_build(model.tag)
        major = (
            model.tag
            if model.tag in ("T2T CHM13", "T2T HG002", "T2T PR1")
            else display_build(canon or model.name)
        )
        return VcfReferenceDetection(
            model=model,
            build=canon,
            major_build=major,
            signature=signature,
            naming=model.naming,
            evidence=f"header {model.evidence}",
        )

    # Layer 2b: Header ##reference= / ##assembly= / ##contig= metadata tokens.
    metadata = "\n".join(
        reference_header_name(line.partition("=")[2])
        if line.lower().startswith("##reference=")
        else line
        for line in hdr.splitlines()
        if line.lower().startswith(("##reference=", "##assembly=", "##contig="))
    )
    header_build = build_from_path(metadata) if metadata else None
    header_naming = _naming_from_chroms(list(lengths.keys()))
    if header_build:
        return VcfReferenceDetection(
            model=None,
            build=header_build,
            major_build=display_build(header_build),
            signature=signature,
            naming=header_naming,
            evidence="header reference",
        )

    if not allow_body_sampling:
        return VcfReferenceDetection(
            model=None,
            build=None,
            major_build=None,
            signature=signature,
            naming=header_naming,
            evidence="",
        )

    # Layer 3: Fast streaming body sampling.
    sites, max_pos, body_naming = _stream_vcf_snps(path_str, max_snps=max_snps)
    naming = header_naming or body_naming

    # Layer 3a: REF-allele fingerprinting against installed reference FASTAs.
    if ref_lib_dir is None:
        from wgsextract_cli.core.reference_assets import resolve_reference_library

        ref_lib_dir = resolve_reference_library(None)
    candidates = _candidate_fastas(ref_lib_dir, reference_fasta)
    ref_match = _match_ref_bases(sites, candidates)
    if ref_match is not None:
        major, canon, matched, compared, runner_up = ref_match
        detail = (
            f"body REF match ({matched}/{compared} vs {major}; next {runner_up}/{compared})"
            if runner_up is not None
            else f"body REF match ({matched}/{compared} vs {major})"
        )
        return VcfReferenceDetection(
            model=None,
            build=canon or canonical_build(major),
            major_build=major,
            signature=signature,
            naming=naming,
            evidence=detail,
        )

    # Layer 3b: rsID -> coordinate match (local dbSNP SQLite + bundled diagnostic panel).
    rsid_match = _match_rsids(sites, ref_lib_dir)
    if rsid_match is not None:
        major, canon, matched, total = rsid_match
        return VcfReferenceDetection(
            model=None,
            build=canon,
            major_build=major,
            signature=signature,
            naming=naming,
            evidence=f"body rsID match ({matched}/{total} vs {major})",
        )

    # Layer 3c: Coordinate bounds on chr1 (GRCh37 is uniquely longest at 249,250,621 bp).
    chr1_max = max_pos.get("1", 0)
    if 248_956_422 < chr1_max <= 249_250_621:
        return VcfReferenceDetection(
            model=None,
            build="hg19",
            major_build="GRCh37",
            signature=signature,
            naming=naming,
            evidence=f"body coordinate bounds (chr1 max {chr1_max:,} bp)",
        )

    return VcfReferenceDetection(
        model=None,
        build=None,
        major_build=None,
        signature=signature,
        naming=naming,
        evidence="",
    )
