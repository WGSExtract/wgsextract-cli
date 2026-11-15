"""Local, additive, autosomal PGS sums; missing calls are never imputed.

The on-disk genotype index bounds memory independently of the size of the
VCF or scoring file. Catalog harmonization supplies coordinates, not proof
of allele orientation; we do not guess strand flips or normalize indels.
"""

from __future__ import annotations

import bisect
import csv
import gzip
import hashlib
import json
import math
import re
import sqlite3
import struct
import subprocess
import time
import urllib.error
import urllib.request
import zlib
from collections import Counter, OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any, TextIO

from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.utils import UsageError, WGSExtractError, popen

DISCLAIMER = (
    "Research use only; not a diagnosis. A polygenic score is not absolute "
    "disease risk, and must not guide treatment or screening."
)
# IDs and trait labels only: scoring files are never redistributed.
CURATED = {
    "cad": "PGS000018",
    "breast-cancer": "PGS000004",
    "height": "PGS000297",
    "t2d": "PGS000020",
    "bmi": "PGS000027",
    "stroke": "PGS000038",
    "triglycerides": "PGS000063",
    "ldl-cholesterol": "PGS000065",
    "prostate-cancer": "PGS000067",
    "lung-cancer": "PGS000070",
    "thyroid-cancer": "PGS000087",
}


def text_file(path: Path) -> TextIO:
    return (
        gzip.open(path, "rt", encoding="utf-8-sig")
        if path.suffix == ".gz"
        else path.open(encoding="utf-8-sig")
    )


def catalog_file(pgs_id: str, build: str, folder: Path) -> Path:
    """Download metadata and harmonized weights, retrying transient failures."""
    if not re.fullmatch(r"PGS[0-9]{6}", pgs_id):
        raise UsageError("A PGS ID has the form PGS000018.")
    folder.mkdir(parents=True, exist_ok=True)
    metadata = folder / f"{pgs_id}.json"
    _download(f"https://www.pgscatalog.org/rest/score/{pgs_id}/", metadata)
    try:
        info = json.loads(metadata.read_text(encoding="utf-8"))
        url = info["ftp_harmonized_scoring_files"][build]["positions"]
        expected = f"https://ftp.ebi.ac.uk/pub/databases/spot/pgs/scores/{pgs_id}/"
        if not isinstance(url, str) or not url.startswith(expected):
            raise ValueError("unexpected scoring-file host")
    except (ValueError, KeyError, TypeError) as exc:
        raise WGSExtractError(
            f"The Catalog has no harmonized {build} scoring file for {pgs_id}."
        ) from exc
    path = folder / f"{pgs_id}_{build}.txt.gz"
    _download(url, path)
    return path


def _download(url: str, path: Path) -> None:
    for attempt in range(3):
        checkpoint()
        try:
            with atomic_output(path, retire_indexes=False) as staged:
                with urllib.request.urlopen(url, timeout=60) as response:
                    content_length = response.headers.get("Content-Length")
                    try:
                        expected = (
                            int(content_length) if content_length is not None else None
                        )
                    except ValueError as exc:
                        raise OSError("Invalid PGS download Content-Length.") from exc
                    received = 0
                    with open(staged, "wb") as target:
                        while data := response.read(1024 * 1024):
                            checkpoint()
                            target.write(data)
                            received += len(data)
                    if received == 0:
                        raise EOFError("Empty PGS download.")
                    if expected is not None and received != expected:
                        raise OSError(
                            f"Incomplete PGS download: received {received} bytes, expected {expected}."
                        )
                if path.suffix == ".gz":
                    with gzip.open(staged, "rb") as compressed:
                        while compressed.read(1024 * 1024):
                            checkpoint()
                elif path.suffix == ".json":
                    json.loads(Path(staged).read_text(encoding="utf-8"))
            return
        except (
            OSError,
            urllib.error.URLError,
            HTTPException,
            EOFError,
            zlib.error,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            if attempt == 2:
                raise WGSExtractError(f"PGS download failed: {url}: {exc}") from exc
            time.sleep(2**attempt)


@dataclass(frozen=True)
class Variant:
    chrom: str
    pos: int
    effect: str
    other: str
    weight: float
    supported: bool


class ScoringFile:
    def __init__(
        self, path: Path, build: str, *, catalog_metadata: Path | None = None
    ) -> None:
        self.path = path
        self.catalog_metadata = catalog_metadata
        self.metadata: dict[str, str] = {}
        with text_file(path) as source:
            for line in source:
                if not line.startswith("#"):
                    self.columns = line.rstrip("\n").split("\t")
                    break
                key, sep, value = line.lstrip("#").strip().partition("=")
                if sep:
                    self.metadata[key] = value
            else:
                raise WGSExtractError(f"Empty scoring file: {path}")
        if "pgs_id" in self.metadata and not re.fullmatch(
            r"PGS[0-9]{6}", self.metadata["pgs_id"]
        ):
            raise UsageError("The scoring file has an invalid pgs_id header.")
        harmonized = "hm_chr" in self.columns and "hm_pos" in self.columns
        actual = self.metadata.get("HmPOS_build" if harmonized else "genome_build")
        if actual != build:
            raise UsageError(
                f"{path.name}: scoring build {actual!r} is not {build}. "
                "Supply a scoring file with matching coordinates and build metadata."
            )
        self.chrom_column = "hm_chr" if harmonized else "chr_name"
        self.pos_column = "hm_pos" if harmonized else "chr_position"
        required = {
            self.chrom_column,
            self.pos_column,
            "effect_allele",
            "effect_weight",
        }
        missing = required.difference(self.columns)
        if missing:
            raise WGSExtractError(
                f"{path.name}: missing scoring columns: {', '.join(sorted(missing))}"
            )
        if any(
            c in self.columns
            for c in (
                "is_dominant",
                "is_recessive",
                "is_haplotype",
                "is_diplotype",
                "dosage_0_weight",
                "dosage_1_weight",
                "dosage_2_weight",
            )
        ):
            raise UsageError(
                "Only additive, single-variant scoring files are supported."
            )

    def variants(self) -> Iterator[Variant]:
        with text_file(self.path) as source:
            reader = csv.DictReader(
                (line for line in source if not line.startswith("#")), delimiter="\t"
            )
            for number, row in enumerate(reader, 1):
                if number % 10000 == 0:
                    checkpoint()
                try:
                    chrom = row[self.chrom_column].removeprefix("chr")
                    position = row[self.pos_column]
                    pos = int(position) if position and position != "NA" else 0
                    weight = float(row["effect_weight"])
                    effect = row["effect_allele"].upper()
                    other = (
                        row.get("other_allele") or row.get("hm_inferOtherAllele") or ""
                    ).upper()
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    raise WGSExtractError(
                        f"{self.path.name}: invalid scoring row {number}."
                    ) from exc
                if not math.isfinite(weight):
                    raise WGSExtractError(
                        f"{self.path.name}: non-finite weight at row {number}."
                    )
                supported = (
                    chrom in {str(n) for n in range(1, 23)}
                    and pos > 0
                    and effect in {"A", "C", "G", "T"}
                    and other in {"A", "C", "G", "T"}
                    and effect != other
                )
                yield Variant(chrom, pos, effect, other, weight, supported)

    def provenance(self) -> dict[str, Any]:
        with self.path.open("rb") as stream:
            sha = hashlib.sha256()
            for data in iter(lambda: stream.read(1024 * 1024), b""):
                sha.update(data)
        info = (
            json.loads(self.catalog_metadata.read_text(encoding="utf-8"))
            if self.catalog_metadata is not None
            else {}
        )
        return {
            "pgs_id": self.metadata.get("pgs_id", "custom"),
            "trait": self.metadata.get("trait_reported", "custom"),
            "citation": self.metadata.get("citation", "Not supplied"),
            "license": info.get("license")
            or self.metadata.get(
                "license", "Not supplied; consult the score author before use"
            ),
            "sha256": sha.hexdigest(),
            "scoring_file": str(self.path.resolve()),
        }


class IntervalIndex:
    """Merged zero-based, half-open BED intervals, queried with one-based positions."""

    def __init__(self, intervals: dict[str, list[tuple[int, int]]]) -> None:
        self.intervals: dict[str, list[tuple[int, int]]] = {}
        self.starts: dict[str, list[int]] = {}
        for chrom, values in intervals.items():
            merged: list[tuple[int, int]] = []
            for start, end in sorted(values):
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            self.intervals[chrom] = merged
            self.starts[chrom] = [v[0] for v in merged]

    def contains(self, chrom: str, pos: int) -> bool:
        """Test one-based VCF ``pos``: start < pos <= end for BED [start, end)."""
        index = bisect.bisect_right(self.starts.get(chrom, []), pos - 1) - 1
        return index >= 0 and pos <= self.intervals[chrom][index][1]


class ConfidentBed(IntervalIndex):
    def __init__(self, path: Path) -> None:
        intervals: dict[str, list[tuple[int, int]]] = {}
        with text_file(path) as source:
            for line in source:
                if line.startswith(("#", "track", "browser")) or not line.strip():
                    continue
                fields = line.split()
                try:
                    start, end = int(fields[1]), int(fields[2])
                    if start < 0 or end <= start:
                        raise ValueError
                except (IndexError, ValueError) as exc:
                    raise UsageError(
                        f"Invalid confident BED interval: {line.strip()}"
                    ) from exc
                intervals.setdefault(fields[0].removeprefix("chr"), []).append(
                    (start, end)
                )
        super().__init__(intervals)


class Reference:
    """Random-access FASTA, including BGZF through its samtools .gzi index.

    Catalog scoring files need not be position-sorted. A chromosome cache
    would repeatedly decompress entire chromosomes on those files.
    """

    def __init__(self, path: Path, build: str | None = None) -> None:
        index = Path(str(path) + ".fai")
        if not index.is_file():
            raise UsageError("--ref needs an existing samtools .fai index.")
        self.entries: dict[str, tuple[int, int, int, int]] = {}
        for line in index.read_text().splitlines():
            fields = line.split("\t")
            length, offset, bases, width = map(int, fields[1:5])
            self.entries[fields[0].removeprefix("chr")] = (length, offset, bases, width)
        chr1_length = self.entries.get("1", (0, 0, 0, 0))[0]
        if build is not None and chr1_length in (248956422, 249250621):
            expected = 248956422 if build == "GRCh38" else 249250621
            if chr1_length != expected:
                raise UsageError(
                    "Reference FASTA contig lengths disagree with --build."
                )
        self.compressed = path.suffix in (".gz", ".bgz")
        self.offsets = [0]
        self.compressed_offsets = [0]
        if self.compressed:
            gzi = Path(str(path) + ".gzi")
            if not gzi.is_file():
                raise UsageError("BGZF --ref needs an existing samtools .gzi index.")
            data = gzi.read_bytes()
            count = struct.unpack_from("<Q", data)[0]
            if len(data) != 8 + count * 16:
                raise WGSExtractError("Invalid reference .gzi index.")
            for n in range(count):
                compressed, uncompressed = struct.unpack_from("<QQ", data, 8 + n * 16)
                self.offsets.append(uncompressed)
                self.compressed_offsets.append(compressed)
        self.stream = path.open("rb")
        self.cache: OrderedDict[int, bytes] = OrderedDict()

    def close(self) -> None:
        self.stream.close()

    def base(self, chrom: str, pos: int) -> str:
        entry = self.entries.get(chrom)
        if entry is None or not 0 < pos <= entry[0]:
            return ""
        _, start, bases, width = entry
        offset = start + (pos - 1) // bases * width + (pos - 1) % bases
        if not self.compressed:
            self.stream.seek(offset)
            return self.stream.read(1).decode("ascii").upper()
        block = bisect.bisect_right(self.offsets, offset) - 1
        if block not in self.cache:
            compressed = self.compressed_offsets[block]
            self.stream.seek(compressed)
            size = (
                self.compressed_offsets[block + 1] - compressed
                if block + 1 < len(self.compressed_offsets)
                else -1
            )
            self.cache[block] = gzip.decompress(self.stream.read(size))
            if len(self.cache) > 256:
                self.cache.popitem(last=False)
        self.cache.move_to_end(block)
        return chr(self.cache[block][offset - self.offsets[block]]).upper()


class Genotypes:
    def __init__(
        self, vcf: Path, database: Path, sample: str | None, build: str
    ) -> None:
        self.db = sqlite3.connect(database)
        try:
            self._load(vcf, sample, build)
        except BaseException:
            self.db.close()
            raise

    def _load(self, vcf: Path, sample: str | None, build: str) -> None:
        self.db.execute("PRAGMA journal_mode=OFF")
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute(
            "CREATE TABLE calls (chrom TEXT, pos INTEGER, end INTEGER, ref TEXT, alt TEXT, gt TEXT, filter TEXT)"
        )
        # Build incrementally, avoiding an external index sort in OS temp.
        self.db.execute("CREATE INDEX locus ON calls(chrom,pos)")
        names = subprocess.run(
            ["bcftools", "query", "-l", str(vcf)],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if names.returncode:
            raise WGSExtractError(f"Cannot read VCF: {names.stderr[-500:]}")
        samples = names.stdout.splitlines()
        if (sample is None and len(samples) != 1) or (
            sample is not None and sample not in samples
        ):
            raise UsageError(
                "VCF must contain one sample, or select one with --sample."
            )
        self.sample = sample or samples[0]
        header = subprocess.run(
            ["bcftools", "view", "-h", str(vcf)],
            capture_output=True,
            text=True,
            check=True,
            errors="replace",
        ).stdout
        expected = "248956422" if build == "GRCh38" else "249250621"
        chr1 = re.search(r"##contig=<ID=(?:chr)?1,length=(\d+)", header)
        if chr1 and chr1[1] in {"248956422", "249250621"} and chr1[1] != expected:
            raise UsageError("VCF contig lengths disagree with --build.")
        self.has_blocks = False
        command = [
            "bcftools",
            "query",
            "--allow-undef-tags",
            "-s",
            self.sample,
            "-f",
            "%CHROM\t%POS\t%INFO/END\t%REF\t%ALT\t[%GT\t%FT]\t%FILTER\n",
            str(vcf),
        ]
        with popen(command, stdout=subprocess.PIPE, text=True) as process:
            assert process.stdout is not None
            batch = []
            for line in process.stdout:
                chrom, pos, end, ref, alt, gt, sample_filter, filt = line.rstrip(
                    "\n"
                ).split("\t")
                if sample_filter not in (".", "PASS"):
                    filt = sample_filter
                end_pos = max(
                    int(end) if end != "." else int(pos), int(pos) + len(ref) - 1
                )
                self.has_blocks |= end_pos > int(pos)
                batch.append(
                    (chrom.removeprefix("chr"), int(pos), end_pos, ref, alt, gt, filt)
                )
                if len(batch) == 10000:
                    self.db.executemany(
                        "INSERT INTO calls VALUES (?,?,?,?,?,?,?)", batch
                    )
                    batch.clear()
                    checkpoint()
            self.db.executemany("INSERT INTO calls VALUES (?,?,?,?,?,?,?)", batch)
        if process.returncode:
            raise WGSExtractError(
                f"bcftools query failed (exit {process.returncode}); no score was produced."
            )
        self.db.commit()
        blocked: dict[str, list[tuple[int, int]]] = {}
        for chrom, pos, end in self.db.execute(
            "SELECT chrom,pos,end FROM calls WHERE end>pos "
            "AND NOT (gt IN ('0/0','0|0') AND filter IN ('.','PASS'))"
        ):
            blocked.setdefault(chrom, []).append((pos, end))
        self.blocked = IntervalIndex(blocked)

    def close(self) -> None:
        self.db.close()

    def dosage(
        self,
        variant: Variant,
        confident: ConfidentBed | None,
        reference: Reference | None,
        keep_ambiguous: bool = False,
    ) -> tuple[int | None, str]:
        if not variant.supported:
            return None, "unsupported"
        if not keep_ambiguous and variant.effect + variant.other in (
            "AT",
            "TA",
            "CG",
            "GC",
        ):
            return None, "strand_ambiguous"
        chrom, pos = variant.chrom, variant.pos
        if confident is not None and not confident.contains(chrom, pos):
            return None, "outside_confident"
        if self.blocked.contains(chrom, pos):
            return None, "overlapping_nonreference"
        rows = self.db.execute(
            "SELECT ref,alt,gt,filter FROM calls WHERE chrom=? AND pos=?", (chrom, pos)
        ).fetchall()
        if rows:
            if len(rows) != 1:
                return None, "ambiguous"
            ref, alt, gt, filt = rows[0]
            if filt not in (".", "PASS"):
                return None, "filtered"
            alleles = [ref, *alt.split(",")]
            if not {variant.effect, variant.other}.issubset(alleles):
                # Explicit reference calls often have ALT='.' or '<NON_REF>'.
                if not (
                    gt in ("0/0", "0|0")
                    and ref in {variant.effect, variant.other}
                    and alt in (".", "<NON_REF>", "<*>")
                ):
                    return None, "allele_mismatch"
            try:
                indices = re.split(r"[/|]", gt)
                if len(indices) != 2 or "." in indices:
                    return None, "no_call"
                called = [alleles[int(i)] for i in indices]
                if any(a not in {variant.effect, variant.other} for a in called):
                    return None, "allele_mismatch"
                return called.count(variant.effect), "called"
            except (ValueError, IndexError):
                return None, "no_call"
        reason = "confident_reference" if confident is not None else ""
        if not reason and self.has_blocks:
            block = self.db.execute(
                "SELECT end,alt,gt,filter FROM calls WHERE chrom=? AND pos<? ORDER BY pos DESC LIMIT 1",
                (chrom, pos),
            ).fetchone()
            if (
                block
                and block[0] >= pos
                and block[1] in ("<NON_REF>", "<*>")
                and block[2] in ("0/0", "0|0")
                and block[3] in (".", "PASS")
            ):
                reason = "reference_block"
        if reason and reference is not None:
            base = reference.base(chrom, pos)
            if base in {variant.effect, variant.other}:
                return 2 * (base == variant.effect), reason
            return None, "allele_mismatch"
        return None, "missing"


def score(
    scoring: ScoringFile,
    genotypes: Genotypes,
    *,
    confident: ConfidentBed | None = None,
    reference: Reference | None = None,
    min_match: float = 0.75,
    matched_file: Path | None = None,
    keep_ambiguous: bool = False,
) -> dict[str, Any]:
    """Sum observed effect-allele dosages; count every source row in coverage."""
    import numpy as np

    counts: Counter[str] = Counter()
    sums: list[float] = []
    weights: list[float] = []
    dosages: list[int] = []
    target = (
        matched_file.open("w", encoding="utf-8", newline="\n") if matched_file else None
    )
    try:
        if target:
            target.write("id\tchrom\tpos\teffect\tother\tweight\tdosage\n")
        for number, variant in enumerate(scoring.variants(), 1):
            dosage, reason = genotypes.dosage(
                variant, confident, reference, keep_ambiguous
            )
            counts[reason] += 1
            if dosage is None:
                continue
            weights.append(variant.weight)
            dosages.append(dosage)
            if target:
                target.write(
                    f"v{number}\t{variant.chrom}\t{variant.pos}\t{variant.effect}\t{variant.other}\t{variant.weight:.17g}\t{dosage}\n"
                )
            if len(weights) == 10000:
                sums.append(float(np.dot(np.array(weights), np.array(dosages))))
                weights.clear()
                dosages.clear()
        sums.append(float(np.dot(np.array(weights), np.array(dosages))))
    finally:
        if target:
            target.close()
    total = sum(counts.values())
    matched = sum(
        counts[k] for k in ("called", "confident_reference", "reference_block")
    )
    rate = matched / total if total else 0.0
    if total == 0:
        raise WGSExtractError("The scoring file contains no variants.")
    total_score = math.fsum(sums)
    if not math.isfinite(total_score):
        raise WGSExtractError(
            "The weighted score sum is non-finite; check the scoring weights."
        )
    return {
        **scoring.provenance(),
        "sample": genotypes.sample,
        "score_sum": total_score if matched else None,
        "keep_ambiguous": keep_ambiguous,
        "variants_total": total,
        "variants_matched": matched,
        "match_rate": rate,
        "match_counts": dict(counts),
        "confidence": "low" if rate < min_match else "uncalibrated",
        "percentile": None,
        "percentile_unavailable_reason": "Needs a score-specific reference distribution.",
        "percentile_status": "below_match_threshold"
        if rate < min_match
        else "reference_calibration_unavailable",
        "minimum_match_rate": min_match,
        "disclaimer": DISCLAIMER,
    }
