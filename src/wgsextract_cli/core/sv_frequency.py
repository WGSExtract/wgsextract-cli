"""Population frequencies of structural variants, by matching each SV call to
the gnomAD-SV and gnomAD-LR sites tables (`ref gnomad-sv`).

SV calls rarely share exact breakpoints with a population catalogue, so a call
matches a catalogue SV by the rule truvari and SVAFotate use:

- Deletions, duplications, inversions and CNVs: compatible types and a
  reciprocal overlap of at least ``min_overlap`` (default 50%, the usual
  CNV/SV standard): the shared span divided by the longer of the two.
- Insertions: start positions at most ``max_distance`` bp apart (default 500,
  truvari's ``--refdist``) and sizes at least ``min_size_similarity`` alike
  (shorter / longer, default 0.5) when both sizes are known. A tandem
  duplication is an insertion of a copy, so insertions and duplications
  are compared this way too (long-read callers report many tandem
  duplications as insertions).

Breakends and translocations (BND, TRA, CTX) and complex SVs are not
annotated. Of all the catalogue SVs that match, the one with the highest
allele frequency is reported (as SVAFotate does), with its match quality, so
an SV that is common in any matching record or catalogue is flagged common.
"""

from __future__ import annotations

import bisect
import math
import re
import subprocess
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from wgsextract_cli.core.table_writer import Column
from wgsextract_cli.core.utils import WGSExtractError, popen, run_command

TABLE_COLUMNS = ("#CHROM", "POS", "END", "SVTYPE", "SVLEN", "AF", "POPMAX_AF", "ID")
COMMON_AF = 0.01
RARE_AF = 0.001
# SV_FREQ classes, by the best match's allele frequency.
COMMON, LOW, RARE, NOVEL = "common", "low", "rare", "novel"
CLASSES = (COMMON, LOW, RARE, NOVEL)
MIN_SV_LENGTH = 50

_OVERLAP_TYPES = {
    "DEL": ("DEL", "CNV"),
    "DUP": ("DUP", "CNV"),
    "INV": ("INV",),
    "CNV": ("DEL", "DUP", "CNV"),
}
_POINT_TYPES = {"INS": ("INS", "DUP"), "DUP": ("INS",)}
_SYMBOLIC = re.compile(r"^<([A-Za-z0-9]+)")


@dataclass(frozen=True)
class SvCall:
    svtype: str
    start: int
    end: int
    length: int | None


@dataclass(frozen=True)
class Match:
    af: float | None
    popmax_af: float | None
    id: str
    score: float
    source: str


@dataclass(frozen=True)
class MatchRule:
    min_overlap: float = 0.5
    max_distance: int = 500
    min_size_similarity: float = 0.5

    def __post_init__(self) -> None:
        if not 0 < self.min_overlap <= 1:
            raise ValueError("min_overlap must be in (0, 1]")
        if not 0 <= self.min_size_similarity <= 1:
            raise ValueError("min_size_similarity must be in [0, 1]")
        if self.max_distance < 0:
            raise ValueError("max_distance must be at least 0")


def sv_type(raw: str) -> str:
    """DEL, DUP, INS, INV or CNV from a caller's SVTYPE or symbolic ALT
    (DUP:TANDEM, INS:ME:ALU, Delly's <CN0>...); other types upper-cased."""
    base = raw.upper().split(":")[0]
    if base in ("MCNV", "CN") or re.fullmatch(r"CN\d+", base):
        return "CNV"
    return base


def _int(text: str | None) -> int | None:
    try:
        return int(float(text)) if text not in (None, "", ".") else None
    except ValueError:
        return None


def _float(text: str | None) -> float | None:
    try:
        value = float(text) if text not in (None, "", ".") else None
    except ValueError:
        return None
    return value if value is not None and math.isfinite(value) else None


def parse_info(info: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if info in ("", "."):
        return out
    for item in info.split(";"):
        key, _, value = item.partition("=")
        out[key] = value
    return out


def call_from_record(
    pos: int, ref: str, alt: str, info: dict[str, str]
) -> SvCall | None:
    """The SV a VCF record describes, or None when it is not an SV this
    module annotates (a small variant, breakend, translocation or complex SV)."""
    first_alt = alt.split(",")[0]
    symbolic = _SYMBOLIC.match(first_alt)
    raw = info.get("SVTYPE") or (symbolic.group(1) if symbolic else "")
    svlen = _int((info.get("SVLEN") or "").split(",")[0])
    end = _int(info.get("END"))
    sequence = not symbolic and re.fullmatch(r"[ACGTNacgtn]+", first_alt) is not None
    if not raw and sequence:
        diff = len(first_alt) - len(ref)
        raw = "INS" if diff > 0 else "DEL" if diff < 0 else ""
        svlen = svlen if svlen is not None else diff
    svtype = sv_type(raw) if raw else ""
    if svtype not in ("DEL", "DUP", "INS", "INV", "CNV"):
        return None
    length = abs(svlen) if svlen not in (None, 0) else None
    if svtype == "INS":
        if length is None and sequence:
            length = len(first_alt) - len(ref)
        if length is not None and length < MIN_SV_LENGTH:
            return None
        return SvCall("INS", pos, pos, length)
    if end is None or end <= pos:
        if length is None and sequence:
            length = len(ref) - 1
        if not length:
            return None
        end = pos + length
    length = end - pos
    if length < MIN_SV_LENGTH:
        return None
    return SvCall(svtype, pos, end, length)


@dataclass
class _Contig:
    starts: list[int] = field(default_factory=list)
    rows: list[tuple[int, int, str, int | None, float | None, float | None, str]] = (
        field(default_factory=list)
    )


class SitesTable:
    """One catalogue's sites table (bgzip + tabix), loaded a contig at a time."""

    def __init__(self, path: str, source: str | None = None) -> None:
        """source: the catalogue's name, by default its table's ##source."""
        self.path = path
        header = run_command(["tabix", "-H", path], capture_output=True).stdout
        named = re.search(r"^##source=(\S+)", header, re.MULTILINE)
        self.source = source or (named.group(1) if named else Path(path).name)
        listed = run_command(["tabix", "-l", path], capture_output=True).stdout
        self.contigs = set(listed.split())
        self._name = ""
        self._data = _Contig()

    def contig_name(self, chrom: str) -> str | None:
        """The table's name for chrom (chr1 or 1; chrM or MT)."""
        bare = chrom[3:] if chrom.lower().startswith("chr") else chrom
        bare = {"M": "MT", "m": "MT"}.get(bare, bare)
        for name in (chrom, bare, f"chr{bare}", "chrM" if bare == "MT" else ""):
            if name and name in self.contigs:
                return name
        return None

    def covers(self, chrom: str) -> bool:
        return self.contig_name(chrom) is not None

    def _load(self, name: str) -> _Contig:
        if name == self._name:
            return self._data
        rows = []
        query = popen(["tabix", self.path, name], stdout=subprocess.PIPE, text=True)
        assert query.stdout is not None
        for line in query.stdout:
            f = line.rstrip("\n").split("\t")
            # A site no one carries (AF 0, e.g. a gnomAD-LR site seen only in
            # the other cohort) says nothing about frequency: not a match.
            if len(f) < 8 or not _float(f[5]):
                continue
            rows.append(
                (
                    int(f[1]),
                    int(f[2]),
                    f[3],
                    _int(f[4]),
                    _float(f[5]),
                    _float(f[6]),
                    f[7],
                )
            )
        if query.wait() != 0:
            raise WGSExtractError(f"Reading {name} from {self.path} failed.")
        rows.sort()
        self._name, self._data = name, _Contig([r[0] for r in rows], rows)
        return self._data

    def best(self, chrom: str, call: SvCall, rule: MatchRule) -> Match | None:
        name = self.contig_name(chrom)
        if name is None:
            return None
        data = self._load(name)
        best: Match | None = None
        for start, end, svtype, svlen, af, popmax, ident in _candidates(
            data, call, rule
        ):
            score = _score(call, start, end, svtype, svlen, rule)
            if score is None:
                continue
            match = Match(af, popmax, ident, score, self.source)
            if best is None or _rank(match) > _rank(best):
                best = match
        return best


def _rank(match: Match) -> tuple[float, float]:
    """The highest allele frequency among the matches, as SVAFotate reports
    (gnomAD lists some SVs twice at the same breakpoints, once rare and once
    common); the closer match on a tie."""
    return (match.af if match.af is not None else -1.0, match.score)


def _candidates(
    data: _Contig, call: SvCall, rule: MatchRule
) -> Iterator[tuple[int, int, str, int | None, float | None, float | None, str]]:
    # Reciprocal overlap >= f bounds a match's start to within the longer
    # length, and its length to at most the call's / f.
    reach = rule.max_distance
    if call.svtype in _OVERLAP_TYPES and call.length:
        reach = max(reach, math.ceil(call.length / rule.min_overlap))
    lo = bisect.bisect_left(data.starts, call.start - reach)
    hi = bisect.bisect_right(data.starts, call.start + reach)
    return iter(data.rows[lo:hi])


def _score(
    call: SvCall,
    start: int,
    end: int,
    svtype: str,
    svlen: int | None,
    rule: MatchRule,
) -> float | None:
    if svtype in _OVERLAP_TYPES.get(call.svtype, ()):
        db_length = end - start
        if db_length <= 0 or not call.length:
            return None
        shared = min(end, call.end) - max(start, call.start)
        overlap = shared / max(db_length, call.length)
        return overlap if overlap >= rule.min_overlap else None
    if svtype in _POINT_TYPES.get(call.svtype, ()):
        distance = abs(start - call.start)
        if distance > rule.max_distance:
            return None
        db_size = abs(svlen) if svlen else (end - start if svtype == "DUP" else None)
        closeness = 1 - distance / (rule.max_distance + 1)
        if db_size and call.length:
            similarity = min(db_size, call.length) / max(db_size, call.length)
            if similarity < rule.min_size_similarity:
                return None
            return (closeness + similarity) / 2
        return closeness / 2
    return None


def frequency_class(
    af: float | None, matched: bool, common: float = COMMON_AF, rare: float = RARE_AF
) -> str:
    if not matched:
        return NOVEL
    if af is None:
        return LOW  # in gnomAD, frequency not given
    if af >= common:
        return COMMON
    return RARE if af < rare else LOW


INFO_HEADERS = (
    '##INFO=<ID=SV_AF,Number=1,Type=Float,Description="Population allele '
    "frequency of the matching gnomAD SV (the highest across the catalogues "
    'searched; for gnomAD multi-allelic CNVs, the non-reference carrier frequency)">',
    '##INFO=<ID=SV_POPMAX_AF,Number=1,Type=Float,Description="Highest '
    'genetic-ancestry-group allele frequency of the matching gnomAD SV">',
    '##INFO=<ID=SV_AF_ID,Number=1,Type=String,Description="ID of the matching '
    'gnomAD SV">',
    '##INFO=<ID=SV_AF_SOURCE,Number=1,Type=String,Description="Catalogue of the '
    'matching SV (e.g. gnomAD-SV_v4.1, gnomAD-LR_0.1)">',
    '##INFO=<ID=SV_AF_MATCH,Number=1,Type=Float,Description="Match quality, 0-1: '
    "the reciprocal overlap (DEL/DUP/INV/CNV), else the mean of breakpoint "
    'closeness and size similarity (INS)">',
    '##INFO=<ID=SV_FREQ,Number=1,Type=String,Description="Population frequency '
    "class: common (SV_AF >= {common}), low, rare (SV_AF < {rare}) or novel (no "
    'matching gnomAD SV)">',
    '##INFO=<ID=SV_COMMON,Number=0,Type=Flag,Description="Common SV: SV_AF >= '
    '{common}">',
)
SV_TAGS = (
    "SV_AF",
    "SV_POPMAX_AF",
    "SV_AF_ID",
    "SV_AF_SOURCE",
    "SV_AF_MATCH",
    "SV_FREQ",
    "SV_COMMON",
)


def _fmt(value: float | None) -> str:
    return "." if value is None else f"{value:.6g}"


@dataclass
class Summary:
    records: int = 0
    annotatable: int = 0
    classes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(CLASSES, 0))
    by_source: dict[str, int] = field(default_factory=dict)
    rows: list[tuple[str | int | float | None, ...]] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return self.annotatable - self.classes[NOVEL]


TEXT, INTEGER, REAL = "TEXT", "INTEGER", "REAL"
# sv_freq.tsv: one row per annotatable SV.
TABLE: tuple[Column, ...] = (
    Column("chrom", TEXT, "Chromosome, as named in the VCF."),
    Column("pos", INTEGER, "1-based POS of the call."),
    Column("end", INTEGER, "End of the call (POS for an insertion)."),
    Column("svtype", TEXT, "DEL, DUP, INS, INV or CNV."),
    Column("svlen", INTEGER, "SV length in bp, when known."),
    Column("id", TEXT, "The call's VCF ID."),
    Column("filter", TEXT, "The call's FILTER."),
    Column("genotype", TEXT, "The first sample's GT."),
    Column("sv_af", REAL, "Allele frequency of the matching gnomAD SV (SV_AF)."),
    Column("sv_popmax_af", REAL, "Its highest genetic-ancestry-group frequency."),
    Column("sv_freq", TEXT, "common, low, rare or novel (SV_FREQ)."),
    Column("sv_af_id", TEXT, "ID of the matching gnomAD SV."),
    Column("sv_af_source", TEXT, "Its catalogue, e.g. gnomAD-SV_v4.1."),
    Column("sv_af_match", REAL, "Match quality, 0-1 (SV_AF_MATCH)."),
)


def annotate_lines(
    lines: Iterable[str],
    tables: Sequence[SitesTable],
    rule: MatchRule,
    common: float = COMMON_AF,
    rare: float = RARE_AF,
    summary: Summary | None = None,
) -> Iterator[str]:
    """Annotate VCF text (header included) with the SV_* INFO fields; calls
    that are not annotatable SVs, or on a contig no catalogue covers, pass
    through unchanged."""
    summary = summary if summary is not None else Summary()
    params = (
        f'##wgsextract_sv_freq=<Sources="{",".join(t.source for t in tables)}",'
        f"MinOverlap={rule.min_overlap},MaxDistance={rule.max_distance},"
        f"MinSizeSimilarity={rule.min_size_similarity},CommonAF={common},"
        f"RareAF={rare}>"
    )
    for line in lines:
        if line.startswith("##"):
            if line.startswith("##INFO=<ID=") and line[11:].split(",")[0] in SV_TAGS:
                continue  # replaced by this run's definitions
            yield line
            continue
        if line.startswith("#"):
            for header in INFO_HEADERS:
                yield header.format(common=common, rare=rare) + "\n"
            yield params + "\n"
            yield line
            continue
        summary.records += 1
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 8:
            yield line
            continue
        info = parse_info(fields[7])
        for tag in SV_TAGS:
            info.pop(tag, None)
        call = call_from_record(int(fields[1]), fields[3], fields[4], info)
        chrom = fields[0]
        if call is None or not any(t.covers(chrom) for t in tables):
            fields[7] = _join_info(info)
            yield "\t".join(fields) + "\n"
            continue
        summary.annotatable += 1
        matches = [m for t in tables if (m := t.best(chrom, call, rule))]
        best = max(matches, key=_rank) if matches else None
        label = frequency_class(
            best.af if best else None, best is not None, common, rare
        )
        summary.classes[label] += 1
        if best is not None:
            summary.by_source[best.source] = summary.by_source.get(best.source, 0) + 1
            if best.af is not None:
                info["SV_AF"] = _fmt(best.af)
            if best.popmax_af is not None:
                info["SV_POPMAX_AF"] = _fmt(best.popmax_af)
            info["SV_AF_ID"] = best.id
            info["SV_AF_SOURCE"] = best.source
            info["SV_AF_MATCH"] = f"{best.score:.3f}"
        info["SV_FREQ"] = label
        if label == COMMON:
            info["SV_COMMON"] = ""
        fields[7] = _join_info(info)
        gt = None
        if len(fields) > 9:
            keys = fields[8].split(":")
            values = fields[9].split(":")
            if "GT" in keys and keys.index("GT") < len(values):
                gt = values[keys.index("GT")]
        summary.rows.append(
            (
                chrom,
                int(fields[1]),
                call.end,
                call.svtype,
                call.length,
                None if fields[2] == "." else fields[2],
                fields[6],
                gt,
                best.af if best else None,
                best.popmax_af if best else None,
                label,
                best.id if best else None,
                best.source if best else None,
                round(best.score, 3) if best else None,
            )
        )
        yield "\t".join(fields) + "\n"


def _join_info(info: dict[str, str]) -> str:
    if not info:
        return "."
    return ";".join(k if v == "" else f"{k}={v}" for k, v in info.items())
