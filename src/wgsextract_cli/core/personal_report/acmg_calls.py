"""The carried calls of `vcf acmg`'s VCF, for the report's research-only section.

`vcf acmg` (issue 629) classifies every ALT of a VCF; the report counts the
classes of the alleles the sample carries and lists the P/LP ones with their
gene, consequence and sequencing confidence. The VCF is read in one pass, and
only its P/LP records are parsed in full, so a whole genome takes seconds.
"""

from __future__ import annotations

import gzip
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from wgsextract_cli.core import acmg, variant_confidence, variant_table

_KEY = f"{acmg.CLASS}="


@dataclass
class Call:
    """A carried P/LP allele: its variant-table row (column -> value), and the
    sequencing confidence of the call (variant_confidence.classify)."""

    row: dict[str, Any]
    confidence: str
    reasons: list[str]
    alt_fraction: float | None
    phase_set: str | None


@dataclass
class Scan:
    header: variant_table.VcfHeader
    # A sites-only VCF has no genotypes: its every classified ALT counts.
    sites_only: bool = False
    carried: Counter[str] = field(default_factory=Counter)
    calls: list[Call] = field(default_factory=list)
    # Carried P/LP alleles left out of the calls: imputed, and rare or of
    # unknown panel frequency (variant_confidence.rare_imputed).
    rare_imputed: int = 0


@contextmanager
def _lines(path: Path) -> Iterator[IO[str]]:
    if path.name.endswith(("gz", "bgz")):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as stream:
            yield stream
    else:
        with open(path, encoding="utf-8", errors="replace") as stream:
            yield stream


def scan(
    path: Path,
    thresholds: variant_confidence.Thresholds,
    *,
    contaminated: bool = False,
) -> Scan:
    """The classes of the ALTs the first sample carries (every ALT of a
    sites-only VCF), and its P/LP calls. ``contaminated``: `qc contamination`
    found the sample contaminated, which caps every call at moderate.

    Raises OSError, EOFError or ValueError for an unreadable or damaged VCF."""
    with _lines(path) as stream:
        header_lines = []
        for line in stream:
            header_lines.append(line)
            if line.startswith("#CHROM"):
                break
        header = variant_table.parse_header(header_lines)
        has_sample = bool(header.samples)
        result = Scan(header, sites_only=not has_sample)
        caller = variant_confidence.detect_caller(header.lines)
        mq_kind = variant_confidence.mapping_quality_kind(header.lines)
        one_sample = len(header.samples) == 1
        for line in stream:
            classes = _classes(line)
            if not classes:
                continue
            gt = None
            if has_sample:
                # Records run to kilobytes of CSQ: split off only the sample.
                fmt, sample = (
                    line.rstrip("\r\n").rsplit("\t", 2)[-2:]
                    if one_sample
                    else line.split("\t")[8:10]
                )
                gt = _format(fmt, sample).get("GT")
            for alt, cls in enumerate(classes, start=1):
                if cls in acmg.CLASSES and (
                    not has_sample
                    or variant_table.zygosity(gt, alt) in variant_table.CARRIED
                ):
                    result.carried[cls] += 1
            if any(cls in acmg.PATHOGENIC for cls in classes):
                calls, rare = _calls(
                    line, header, thresholds, caller, mq_kind, contaminated
                )
                result.calls += calls
                result.rare_imputed += rare
    return result


def _classes(line: str) -> list[str]:
    """The ACMG_CLASS values of a record, one per ALT ([] when it has none).
    ``str.find`` rather than a regular expression: it is several times faster
    over the long INFO of a VEP-annotated record."""
    start = line.find(_KEY)
    while start > 0 and line[start - 1] not in ";\t":
        start = line.find(_KEY, start + 1)
    if start <= 0 or line.startswith("#"):
        return []
    start += len(_KEY)
    ends = [i for i in (line.find(";", start), line.find("\t", start)) if i >= 0]
    return line[start : min(ends, default=len(line))].rstrip("\r\n").split(",")


def _format(keys: str, values: str) -> dict[str, str]:
    return dict(zip(keys.split(":"), values.split(":"), strict=False))


def _info(text: str) -> dict[str, str]:
    if text in ("", "."):
        return {}
    return {key: value for key, _, value in (i.partition("=") for i in text.split(";"))}


def _calls(
    line: str,
    header: variant_table.VcfHeader,
    thresholds: variant_confidence.Thresholds,
    caller: str,
    mq_kind: str,
    contaminated: bool,
) -> tuple[list[Call], int]:
    """A record's carried P/LP calls, and how many rare imputed ones it left
    out."""
    has_sample = bool(header.samples)
    fields = line.rstrip("\r\n").split("\t")
    alts = fields[4].split(",")
    info = _info(fields[7])
    fmt = _format(fields[8], fields[9]) if has_sample and len(fields) > 9 else {}
    alleles = re.split(r"[/|]", fmt.get("GT", "")) if has_sample else []
    calls = []
    rare = 0
    for values in variant_table.variant_rows(
        [line], header, sample_index=0 if has_sample else None
    ):
        row = dict(zip(variant_table.COLUMN_NAMES, values, strict=True))
        if row["acmg_class"] not in acmg.PATHOGENIC:
            continue
        alt = alts.index(str(row["alt"])) + 1
        if variant_confidence.rare_imputed(info, alt):
            rare += 1
            continue
        label = None
        if has_sample:
            label = variant_confidence.classify(
                alt=alt,
                alleles=alleles,
                ref=fields[3],
                alt_allele=str(row["alt"]),
                filters=fields[6],
                info=info,
                fmt=fmt,
                n_alts=len(alts),
                thresholds=thresholds,
                mq_kind=mq_kind,
                caller=caller,
                samples=len(header.samples),
                contaminated=contaminated,
            )
        calls.append(
            Call(
                row,
                label.label if label else "not_assessed",
                list(label.reasons) if label else [],
                label.alt_fraction if label else None,
                fmt.get("PS") if fmt.get("PS") not in (None, "", ".") else None,
            )
        )
    return calls, rare
