"""Local whole-gene depth calls for research panel audits."""

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Any

from wgsextract_cli.core.lookup.identifiers import contig_key
from wgsextract_cli.core.panel import Annotations
from wgsextract_cli.core.utils import UsageError

CALLER_CONTRACT = "wgsextract.target-cnv/1"
WEIGHT_KEYS = ("deletion_weight", "duplication_weight")
MATCH_ON = ("gc_matched_depth_5x", "gc_fraction_5pct", "mappability")
GC_BINS = 20
DEPTH_BIN_WIDTH = 5
AUTOSOMES = frozenset(map(str, range(1, 23)))
COPY_CLASSES = {"0", "1", "2", "3+"}
REQUIRED_COLUMNS = {
    "name", "contig", "start", "end", "state", "reason", "specialist",
    "expected_copy_number", "copy_number", "confidence", "gc_fraction",
    "gc_matched_depth", "gc_bin", "expected_coverage_bin",
}  # fmt: skip


def enabled(group: Mapping[str, Any]) -> bool:
    return any(group.get(key, 0) > 0 for key in WEIGHT_KEYS)


def gc_bin(fraction: float) -> int:
    return min(GC_BINS - 1, int(fraction * GC_BINS))


def expected_coverage_bin(depth: float) -> int:
    return int(depth // DEPTH_BIN_WIDTH)


@dataclass(frozen=True)
class Call:
    copy_number: str | None
    confidence: str | None
    exclusion: str | None
    stratum: tuple[int, int, str] | None = None
    region: tuple[str, int, int] | None = None

    def components(self, group: Mapping[str, Any]) -> tuple[Fraction, Fraction]:
        lost = {"0": 2, "1": 1}.get(self.copy_number or "", 0)
        return (
            lost * Fraction(str(group.get("deletion_weight", 0))),
            int(self.copy_number == "3+")
            * Fraction(str(group.get("duplication_weight", 0))),
        )

    def document(self, group: Mapping[str, Any]) -> dict[str, object]:
        document: dict[str, object] = {
            "state": "callable" if self.exclusion is None else "not_callable",
            "reason": self.exclusion,
            "copy_number": self.copy_number if self.exclusion is None else None,
            "confidence": self.confidence,
        }
        if self.exclusion and self.copy_number:
            document["screened_copy_number"] = self.copy_number
        if self.region:
            chrom, start, end = self.region
            document["target"] = {"contig": chrom, "start": start, "end": end}
        if self.stratum is not None:
            deletion, duplication = self.components(group)
            document.update(
                strata=dict(zip(MATCH_ON, self.stratum, strict=True)),
                deletion_burden=float(deletion),
                duplication_burden=float(duplication),
            )
        return document


@dataclass(frozen=True)
class Evidence:
    calls: dict[str, Call]
    caller_version: str
    mask: str
    sha256: str

    def describe(self) -> dict[str, object]:
        return {
            "caller_contract": CALLER_CONTRACT,
            "caller_version": self.caller_version,
            "mask": self.mask,
            "sha256": self.sha256,
            "target_count": len(self.calls),
            "confidence_policy": "Unmasked diploid autosomes; medium/high confidence only.",
            "note": "Same-sample depth evidence; research only. No SNV–CNV phase inferred.",
        }


def _provenance(
    manifest: object, digest: str, sample: str, build: str
) -> tuple[str, str]:
    if not isinstance(manifest, dict) or (
        manifest.get("schema") != "wgsextract.result/1"
        or manifest.get("command") != "typing target-cnv"
        or manifest.get("status") != "ok"
        or manifest.get("exit_code") != 0
    ):
        raise ValueError("manifest is not from a successful typing target-cnv run")
    outputs = manifest.get("outputs")
    matches = [
        row for row in outputs if isinstance(row, dict) and row.get("role") == "target_cnv"
    ] if isinstance(outputs, list) else []  # fmt: skip
    metadata = matches[0].get("metrics") if len(matches) == 1 else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("caller_contract") != CALLER_CONTRACT
    ):
        raise ValueError(
            "manifest lacks supported caller provenance; rerun typing target-cnv"
        )
    if metadata.get("sha256") != digest:
        raise ValueError("TSV checksum does not match the manifest")
    if metadata.get("sample") != sample or not sample:
        raise ValueError(f"manifest does not identify the VCF sample {sample!r}")
    inputs = manifest.get("inputs")
    roles = {
        row.get("role") for row in inputs
        if isinstance(row, dict) and isinstance(row.get("role"), str)
    } if isinstance(inputs, list) else set()  # fmt: skip
    if not {"targets_bed", "reference_fasta", "input"} <= roles:
        raise ValueError(
            "manifest lacks alignment, target design or reference provenance"
        )
    metrics, provenance = manifest.get("metrics"), manifest.get("provenance")
    if not isinstance(metrics, dict) or metrics.get("target_cnv_build") != build:
        raise ValueError("manifest build does not match the VCF build")
    mask = metrics.get("target_cnv_mask")
    version = provenance.get("version") if isinstance(provenance, dict) else None
    if (
        mask not in ("giab_v3.5_bundled", "giab_v3.5_exact")
        or not isinstance(version, str)
        or not version
    ):
        raise ValueError("manifest lacks supported mask or caller version")
    return version, mask


def _call(row: dict[str, str], chrom: str) -> Call:
    cn, confidence = row["copy_number"] or None, row["confidence"] or None
    if chrom not in AUTOSOMES:
        return Call(cn, confidence, "non_autosomal_target")
    if row["specialist"] or row["reason"] in {
        "segdup_or_low_mappability",
        "masked_region_curated_call",
    }:
        return Call(cn, confidence, "masked_or_specialist")
    if row["state"] != "called":
        return Call(cn, confidence, row["reason"] or "no_call")
    if row["reason"]:
        raise ValueError("unexpected reason attached to a called autosomal target")
    if row["expected_copy_number"] != "2" or cn not in COPY_CLASSES:
        raise ValueError(
            "called autosomal target lacks a valid diploid copy-number class"
        )
    if confidence == "low":
        return Call(cn, confidence, "low_confidence")
    if confidence not in {"medium", "high"}:
        raise ValueError("called autosomal target lacks a valid confidence")
    gc, depth = int(row["gc_bin"]), int(row["expected_coverage_bin"])
    if not 0 <= gc < GC_BINS or not 0 <= depth:
        raise ValueError("invalid GC or GC-matched coverage bin")
    return Call(cn, confidence, None, (depth, gc, "unmasked"))


def load(tsv: Path, manifest: Path, sample: str, notes: Annotations) -> Evidence:
    try:
        data = tsv.read_bytes()
        digest = sha256(data).hexdigest()
        version, mask = _provenance(
            json.loads(manifest.read_text(encoding="utf-8")),
            digest,
            sample,
            notes.build,
        )
        rows = csv.DictReader(
            io.StringIO(data.decode("utf-8"), newline=""), delimiter="\t"
        )
        header = rows.fieldnames or []
        if not REQUIRED_COLUMNS <= set(header) or len(set(header)) != len(header):
            raise ValueError("invalid target-CNV TSV header")
        calls: dict[str, Call] = {}
        seen: set[tuple[str, int, int]] = set()
        for row in rows:
            if None in row or None in row.values():
                raise ValueError(f"invalid TSV line {rows.line_num}")
            symbol = row["name"].upper()
            chrom, start, end = (
                contig_key(row["contig"]),
                int(row["start"]),
                int(row["end"]),
            )
            loci = notes.loci.get(symbol, ())
            if len(loci) != 1:
                raise ValueError(f"{symbol!r} is not an unambiguous canonical gene")
            locus = loci[0]
            region = (chrom, start, end)
            if not 0 <= start < end or region != (
                contig_key(locus.chrom),
                locus.start,
                locus.end,
            ):
                raise ValueError(
                    f"{symbol} needs its exact 0-based half-open whole-gene target"
                )
            if symbol in calls or region in seen:
                raise ValueError(f"duplicate whole-gene target for {symbol}")
            calls[symbol] = replace(_call(row, chrom), region=region)
            seen.add(region)
        if not calls:
            raise ValueError("TSV has no targets")
        for (chrom, _, end), (next_chrom, start, _) in pairwise(sorted(seen)):
            if chrom == next_chrom and end > start:
                raise ValueError(f"whole-gene targets overlap at {chrom}:{start}")
    except (OSError, ValueError, csv.Error, ArithmeticError) as exc:
        raise UsageError(f"Invalid --target-cnv evidence: {exc}.") from exc
    return Evidence(calls, version, mask, digest)


def describe_groups(groups: list[dict[str, Any]], evidence: Evidence) -> None:
    missing = Call(None, None, "missing_target")
    for group in groups:
        if not enabled(group):
            continue
        excluded = {}
        for gene in group["genes"]:
            symbol = gene.get("symbol")
            call = (
                evidence.calls.get(symbol.upper(), missing)
                if symbol
                else Call(None, None, "unresolved_gene")
            )
            gene["target_cnv"] = call.document(group)
            if call.exclusion:
                excluded[gene["given"]] = call.exclusion
        group["cnv_calibration"] = {
            "state": "not_run",
            "callability": {
                "target_genes": len(group["genes"]),
                "callable_target_genes": len(group["genes"]) - len(excluded),
                "excluded_targets": excluded,
            },
        }
