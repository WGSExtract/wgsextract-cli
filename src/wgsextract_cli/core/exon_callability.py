"""Exon callability: which coding exons of a gene list a gVCF could read.

A missing finding means one of three things: nothing was found where the
assay read every base (not reported), the assay could not read the bases (not
readable), or no analysis looked (not run). Gene-body coverage can't tell
them apart: a gene with reads over 90% of its body can still hide a whole
unread exon. So each coding exon (its CDS plus ``flank`` intronic bases each
side) of each gene's MANE Select (GRCh38) or RefSeq Select (GRCh37)
transcript is classified from the sample's reference evidence
(core/reference_evidence.py), base by base, and a gene is callable only when
every one of its coding exons is.

Bases where short reads are known to be unreliable (GIAB low-mappability and
segmental-duplication regions, in the bundled model's ``limitations``) never
count as readable: their exon needs a specialist (paralog-aware) method,
whatever the gVCF says. The bundled exon models (assets/report/
exons_<build>.tsv.gz) are made by scripts/build_report_exons.py; a model for
another gene set or build can be given in the same format. docs/
exon-callability.md describes the states and their limits.
"""

from __future__ import annotations

import gzip
import hashlib
import io
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from wgsextract_cli.core.gene_lists import chrom_key
from wgsextract_cli.core.reference_evidence import (
    Interval,
    ReferenceEvidence,
    Span,
    State,
)
from wgsextract_cli.core.table_writer import Column, Value
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.core.variant_confidence import Thresholds

SCHEMA = "wgsextract.exon_callability/1"
TEXT, INTEGER = "TEXT", "INTEGER"
GUIDE = "docs/exon-callability.md"
BUILDS = ("hg38", "hg19")
DEFAULT_FLANK = 2
# The bundled models' limitation intervals reach this far beyond each exon.
MAX_FLANK = 50
MODEL_COLUMNS = ("gene", "transcript", "tag", "strand", "exon", "exons", "chrom",
                 "start", "end", "cds_start", "cds_end", "limitations")  # fmt: skip

# An exon's status, from its in-scope bases.
CALLABLE = "callable"
PARTIAL = "partial"
NOT_READABLE = "not_readable"
SPECIALIST = "specialist_required"
NOT_RUN = "not_run"
NOT_APPLICABLE = "not_applicable"
EXON_STATUSES = {
    CALLABLE: "every base was read: callable reference or a call",
    PARTIAL: "some bases were read, others were not",
    NOT_READABLE: "no base was read (no reads, too few, or failed calls)",
    SPECIALIST: "overlaps a region short reads can't place uniquely (a paralog "
    "or low-mappability region); needs a specialist method",
    NOT_RUN: "the caller did not assess these bases",
    NOT_APPLICABLE: "not expected in this sample (ploidy 0, such as chrY in a woman)",
}
# A gene's technical state, from its coding exons.
GENE_STATES = {
    CALLABLE: "every coding exon callable",
    PARTIAL: "some coding exons not callable",
    NOT_READABLE: "no coding exon callable",
    NOT_RUN: "not assessed (no exon model, or no evidence for it)",
}
LIMITATIONS = {
    "low_mappability": "100 bp reads (up to 2 mismatches, 1 indel) or 250 bp "
    "reads also match elsewhere in the genome (GIAB low-mappability)",
    "segmental_duplication": "inside a copy of over 1 kb at 90% identity or "
    "more elsewhere (GIAB segmental duplications)",
}
# What a callable exon still doesn't establish.
UNASSESSED_CLASSES = {
    "copy_number_and_structural": "Exon or gene deletions and duplications and "
    "other structural variants: reference evidence is for small variants; see "
    "vcf sv and vcf cnv.",
    "repeat_expansions": "Repeat expansions longer than a read: see typing str.",
    "outside_coding_exons": "Promoters, untranslated regions and deep intronic "
    "variants, beyond the coding exons and their flanks.",
    "other_transcripts": "Exons only in transcripts other than the one listed.",
    "mosaic": "Mosaic and low-fraction variants: the germline callers expect "
    "about half or all of the reads to carry a variant.",
    "paralog_regions": "Exons in specialist_required regions: short reads "
    "can't tell the gene from its paralog there.",
}
NOTE = (
    "Callability is a technical property of this sequencing run, not a "
    "clinical result. A callable exon with nothing reported does not rule out "
    "disease, and no gene here is 'negative' while an exon, a paralog region "
    "or a variant class remains unassessed."
)
# States of bases that were read.
_READ = frozenset({State.CALLABLE_REFERENCE.value, State.VARIANT.value})

EXON_COLUMNS: tuple[Column, ...] = (
    Column("gene", TEXT, "Gene symbol."),
    Column("transcript", TEXT, "RefSeq transcript (accession.version)."),
    Column("exon", INTEGER, "Exon number in transcript order."),
    Column("exons", INTEGER, "The transcript's exon count."),
    Column("chrom", TEXT, "Contig, as the gVCF names it."),
    Column("start", INTEGER, "First assessed base (CDS start - flank), 1-based."),
    Column("end", INTEGER, "Last assessed base (CDS end + flank), inclusive."),
    Column(
        "status",
        TEXT,
        "callable, partial, not_readable, specialist_required, "
        "not_run or not_applicable.",
    ),  # fmt: skip
    Column(
        "reasons", TEXT, "States and limitations keeping bases unread, '|'-separated."
    ),
    Column("bases", INTEGER, "Assessed bases."),
    Column(
        "readable_bases",
        INTEGER,
        "Bases read (callable reference or a call) "
        "outside known short-read limitations.",
    ),  # fmt: skip
    Column("limited_bases", INTEGER, "Bases in a known short-read limitation."),
    *(
        Column(f"{state.value}_bases", INTEGER, f"Bases in the {state.value} state.")
        for state in State
    ),
    Column(
        "limitations", TEXT, "Limitation intervals (kind:start-end), '|'-separated."
    ),
)


@dataclass(frozen=True)
class Exon:
    gene: str
    transcript: str
    tag: str
    number: int
    count: int
    chrom: str
    start: int
    end: int
    cds_start: int | None
    cds_end: int | None
    limitations: tuple[tuple[str, int, int], ...] = ()

    def region(self, flank: int) -> tuple[int, int] | None:
        if self.cds_start is None or self.cds_end is None:
            return None
        return max(1, self.cds_start - flank), self.cds_end + flank


@dataclass(frozen=True)
class ExonModel:
    name: str
    sha256: str
    notes: tuple[str, ...]
    contigs: Mapping[str, int]
    genes: Mapping[str, tuple[Exon, ...]]
    build: str | None = None

    def describe(self) -> dict[str, Any]:
        tags = sorted({e.tag for exons in self.genes.values() for e in exons})
        return {
            "name": self.name,
            "build": self.build,
            "sha256": self.sha256,
            "transcripts": ", ".join(tags),
            "genes": len(self.genes),
            "source": list(self.notes),
        }


def _parse_model(name: str, data: bytes, build: str | None) -> ExonModel:
    text = (gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data).decode("utf-8")
    notes: list[str] = []
    contigs: dict[str, int] = {}
    genes: dict[str, list[Exon]] = {}
    columns: list[str] | None = None
    for number, line in enumerate(io.StringIO(text), start=1):
        line = line.rstrip("\r\n")
        if not line:
            continue
        if line.startswith("#contig\t"):
            _, contig, length = line.split("\t")
            contigs[contig] = int(length)
        elif line.startswith("#"):
            notes.append(line.lstrip("# "))
        elif columns is None:
            columns = line.split("\t")
            missing = [c for c in MODEL_COLUMNS if c not in columns]
            if missing:
                raise UsageError(f"Exon model {name} lacks columns {missing}.")
        else:
            row = dict(zip(columns, line.split("\t"), strict=False))
            try:
                limits = tuple(
                    (kind, int(span.split("-")[0]), int(span.split("-")[1]))
                    for kind, _, span in (
                        item.partition(":")
                        for item in row["limitations"].split(";")
                        if item
                    )
                )
                exon = Exon(
                    row["gene"], row["transcript"], row["tag"], int(row["exon"]),
                    int(row["exons"]), row["chrom"], int(row["start"]),
                    int(row["end"]), int(row["cds_start"]) if row["cds_start"] else None,
                    int(row["cds_end"]) if row["cds_end"] else None, limits,
                )  # fmt: skip
            except (KeyError, ValueError, IndexError) as error:
                raise UsageError(
                    f"Exon model {name}, line {number}: bad row ({error})."
                ) from error
            genes.setdefault(exon.gene.upper(), []).append(exon)
    if not genes:
        raise UsageError(f"Exon model {name} lists no exons.")
    unknown = sorted({e.chrom for v in genes.values() for e in v} - set(contigs))
    if unknown:
        raise UsageError(
            f"Exon model {name} has no '#contig<TAB>name<TAB>length' line for "
            f"{', '.join(unknown)}: its build can't be checked against the gVCF."
        )
    return ExonModel(
        name,
        hashlib.sha256(data).hexdigest(),
        tuple(notes),
        contigs,
        {g: tuple(sorted(v, key=lambda e: e.number)) for g, v in genes.items()},
        build,
    )


def bundled_model(build: str) -> ExonModel:
    name = f"exons_{build}.tsv.gz"
    data = resources.files("wgsextract_cli").joinpath(f"assets/report/{name}")
    return _parse_model(name, data.read_bytes(), build)


def load_model(path: Path) -> ExonModel:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise UsageError(f"Cannot read exon model {path}: {error}") from error
    return _parse_model(path.name, data, None)


def contig_names(model: ExonModel, lengths: Mapping[str, int]) -> dict[str, str]:
    """The model's contigs that the gVCF declares, as it names them; refuses a
    model whose contig lengths differ from the gVCF's (another build)."""
    by_key = {chrom_key(name): name for name in lengths}
    names: dict[str, str] = {}
    differ: list[str] = []
    for contig, length in model.contigs.items():
        theirs = by_key.get(chrom_key(contig))
        if theirs is None:
            continue
        if lengths[theirs] != length:
            differ.append(f"{contig} {length:,} vs {lengths[theirs]:,}")
        names[contig] = theirs
    if differ or not names:
        detail = "; ".join(differ) if differ else "it declares none of its contigs"
        raise UsageError(
            f"The gVCF's reference does not match exon model {model.name} "
            f"({detail}): another build. Exon callability needs a gVCF on the "
            "model's build (bundled: hg19, hg38), or --exons with a model for "
            "the gVCF's build."
        )
    return names


def choose_model(lengths: Mapping[str, int], given: Path | None) -> tuple[ExonModel, dict[str, str]]:  # fmt: skip
    """The given model, else the bundled one matching the gVCF's contigs."""
    if given is not None:
        model = load_model(given)
        return model, contig_names(model, lengths)
    for build in BUILDS:
        model = bundled_model(build)
        try:
            return model, contig_names(model, lengths)
        except UsageError:
            continue
    chr1 = next((v for k, v in lengths.items() if chrom_key(k) == "1"), None)
    raise UsageError(
        "The gVCF's contigs match neither the GRCh38 nor the GRCh37 exon model "
        f"(chr1 length {f'{chr1:,}' if chr1 else 'not declared'}): exon "
        "callability is bundled for hg19 and hg38 only; give --exons with a "
        "model for this build."
    )


# --- classification ----------------------------------------------------------


def _overlap(a: Sequence[tuple[int, int]], b: Sequence[tuple[int, int]]) -> int:
    """Bases two sorted, disjoint interval lists share."""
    total = i = j = 0
    while i < len(a) and j < len(b):
        low, high = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        total += max(0, high - low + 1)
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def _merged(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if out and start <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


@dataclass
class ExonResult:
    exon: Exon
    chrom: str
    start: int
    end: int
    states: Counter[str] = field(default_factory=Counter)
    readable: int = 0
    limited: int = 0
    limits: tuple[tuple[str, int, int], ...] = ()

    @property
    def bases(self) -> int:
        return self.end - self.start + 1

    @property
    def status(self) -> str:
        excluded = self.states[State.EXCLUDED_BY_POLICY.value]
        if excluded == self.bases:
            return NOT_APPLICABLE
        if self.states[State.NOT_ASSESSED.value] == self.bases:
            return NOT_RUN
        if self.limited:
            return SPECIALIST
        if self.readable == self.bases - excluded:
            return CALLABLE
        return NOT_READABLE if self.readable == 0 else PARTIAL

    @property
    def reasons(self) -> list[str]:
        unread = [
            state.value
            for state in State
            if self.states[state.value]
            and state.value not in _READ
            and state is not State.EXCLUDED_BY_POLICY
        ]
        return unread + sorted({kind for kind, _, _ in self.limits})

    def row(self) -> list[Value]:
        e = self.exon
        return [
            e.gene, e.transcript, e.number, e.count, self.chrom, self.start,
            self.end, self.status, "|".join(self.reasons), self.bases,
            self.readable, self.limited,
            *(self.states[state.value] for state in State),
            "|".join(f"{k}:{s}-{t}" for k, s, t in self.limits),
        ]  # fmt: skip


def assess(
    evidence: ReferenceEvidence,
    exons: Sequence[Exon],
    names: Mapping[str, str],
    thresholds: Thresholds,
    flank: int,
) -> list[ExonResult]:
    """Each coding exon's bases by evidence state; an exon on a contig the
    gVCF lacks is not assessed."""
    results: list[ExonResult] = []
    for exon in exons:
        region = exon.region(flank)
        if region is None:
            continue
        start, end = region
        limits = tuple(
            (kind, max(s, start), min(t, end))
            for kind, s, t in exon.limitations
            if s <= end and t >= start
        )
        results.append(
            ExonResult(
                exon, names.get(exon.chrom, exon.chrom), start, end, limits=limits
            )
        )
    wanted: list[Interval] = [
        (r.chrom, r.start, r.end) for r in results if r.exon.chrom in names
    ]
    by_contig: dict[str, list[Span]] = {}
    for span in evidence.spans(thresholds, wanted):
        by_contig.setdefault(span.contig, []).append(span)
    for result in results:
        spans = by_contig.get(result.chrom, []) if result.exon.chrom in names else []
        read: list[tuple[int, int]] = []
        covered = 0
        for span in spans:
            low, high = max(span.start, result.start), min(span.end, result.end)
            if low > high:
                continue
            covered += high - low + 1
            result.states[span.state.value] += high - low + 1
            if span.state.value in _READ:
                read.append((low, high))
        result.states[State.NOT_ASSESSED.value] += result.bases - covered
        limited = _merged((s, t) for _, s, t in result.limits)
        read = _merged(read)
        result.limited = sum(t - s + 1 for s, t in limited)
        result.readable = sum(t - s + 1 for s, t in read) - _overlap(read, limited)
    return results


@dataclass
class GeneResult:
    gene: str
    transcript: str | None
    exons: list[ExonResult]

    @property
    def scoped(self) -> list[ExonResult]:
        return [e for e in self.exons if e.status != NOT_APPLICABLE]

    @property
    def state(self) -> str:
        statuses = {e.status for e in self.scoped}
        if not statuses or statuses == {NOT_RUN}:
            return NOT_RUN
        if statuses == {CALLABLE}:
            return CALLABLE
        if not statuses & {CALLABLE, PARTIAL}:
            return NOT_READABLE
        return PARTIAL

    def document(self) -> dict[str, Any]:
        statuses = Counter(e.status for e in self.exons)
        not_callable: dict[str, list[int]] = {}
        for e in self.scoped:
            if e.status != CALLABLE:
                not_callable.setdefault(e.status, []).append(e.exon.number)
        bases = sum(e.bases for e in self.scoped)
        readable = sum(e.readable for e in self.scoped)
        return {
            "gene": self.gene,
            "transcript": self.transcript,
            "chrom": self.exons[0].chrom if self.exons else None,
            "state": self.state,
            "reasons": sorted({r for e in self.scoped for r in e.reasons})
            if self.exons
            else ["no_exon_model"],
            "coding_exons": len(self.scoped),
            "exons": {status: statuses[status] for status in EXON_STATUSES},
            "not_callable": not_callable,
            "bases": bases,
            "readable_bases": readable,
            "readable_fraction": round(readable / bases, 4) if bases else None,
            "limited_bases": sum(e.limited for e in self.scoped),
            "states": dict(
                sum((e.states for e in self.scoped), Counter[str]()).most_common()
            ),
        }


def genes(
    symbols: Sequence[str], model: ExonModel, results: Sequence[ExonResult]
) -> list[GeneResult]:
    by_gene: dict[str, list[ExonResult]] = {}
    for result in results:
        by_gene.setdefault(result.exon.gene.upper(), []).append(result)
    out = []
    for symbol in symbols:
        exons = model.genes.get(symbol.upper(), ())
        out.append(
            GeneResult(
                exons[0].gene if exons else symbol,
                exons[0].transcript if exons else None,
                by_gene.get(symbol.upper(), []),
            )
        )
    return out


def ranges(numbers: Sequence[int]) -> str:
    """'3, 11-15' for [3, 11, 12, 13, 14, 15]."""
    runs: list[list[int]] = []
    for number in sorted(numbers):
        if runs and runs[-1][1] == number - 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    return ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in runs)
