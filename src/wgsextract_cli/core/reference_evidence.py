"""Reference evidence: what a gVCF says about every base, not only variants.

A variant-only VCF cannot tell a confident reference base from one nobody
looked at. With ``--reference-evidence gvcf`` a caller also publishes a gVCF
(role ``reference_evidence``, its index ``reference_evidence_index``) next to
its unchanged variant VCF. ``spans`` reads one and classifies every requested
base into one ``State``; PGS, PharmCAT and ``vcf confidence`` are to share it.
docs/reference-evidence.md explains each state and the rules.

The gVCF's ``##wgseReferenceEvidence`` header (``provenance_header``) records
how it was made: the engine and its version, its quality model (``gq``: blocks
carry GQ; ``depth_only``: bcftools blocks carry only DP/MIN_DP), its bands, the
assessed region and the bases its ploidy policy excludes (ploidy 0, such as a
woman's chrY). A gVCF without the header (another tool's) is read with the
model its FORMAT/GQ declaration implies and no exclusions.
"""

from __future__ import annotations

import enum
import hashlib
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from wgsextract_cli.core.reference_resolver import header_contig_lengths
from wgsextract_cli.core.region_selection import selected_intervals
from wgsextract_cli.core.utils import WGSExtractError, popen, run_command
from wgsextract_cli.core.variant_confidence import SYMBOLIC_REFERENCE_ALTS, Thresholds

SCHEMA = "wgsextract.reference_evidence/1"
ROLE, INDEX_ROLE = "reference_evidence", "reference_evidence_index"
HEADER_KEY = "wgseReferenceEvidence"
GQ, DEPTH_ONLY = "gq", "depth_only"
# Blocks below the first band would be written one record per base.
BCFTOOLS_BANDS = "0,5,10,20"
# Reads under this MAPQ are not evidence for a reference claim; GATK
# HaplotypeCaller drops them too (MappingQualityReadFilter, default 20).
BCFTOOLS_MIN_MAPPING_QUALITY = 20
# GATK's default bands (one per GQ below 60) make a genome's gVCF ~6.5 GB;
# these make it ~1.3 GB with the same GQ >= 20 states (#863).
GATK_BANDS = "10,20,30,60"
GATK_MIN_MAPPING_QUALITY = 20
# Clair3's --min_mq default (shared/param_p.py, param_f.py).
CLAIR3_MIN_MAPPING_QUALITY = 5

Interval = tuple[str, int, int]  # contig, 1-based start, inclusive end


class State(enum.Enum):
    """A base's evidence state, from the strongest claim against reference
    to the weakest. Where records overlap, the earlier state wins."""

    EXCLUDED_BY_POLICY = "excluded_by_policy"
    VARIANT = "variant"
    FILTERED = "filtered"
    NO_CALL = "no_call"
    LOW_QUALITY_REFERENCE = "low_quality_reference"
    UNCOVERED = "uncovered"
    CALLABLE_REFERENCE = "callable_reference"
    NOT_ASSESSED = "not_assessed"


_RANK = {state: rank for rank, state in enumerate(State)}


@dataclass(frozen=True)
class Provenance:
    """What a ``##wgseReferenceEvidence`` header records."""

    engine: str
    version: str
    quality_model: str
    bands: str = ""
    min_mapping_quality: int | None = None
    # The assessed bases (None: every contig), as comma-separated
    # "contig:start-end" items (canonical_region); earlier headers may hold
    # the --region as given, which selected_intervals reads too.
    region: str | None = None
    ploidy: str = ""
    sex: str = ""
    # Ploidy-0 bases, as "contig:start-end" (1-based, inclusive) items.
    ploidy_zero: tuple[str, ...] = ()

    @property
    def ploidy_policy(self) -> str:
        """A digest of the ploidy rules and sex the caller applied."""
        text = "\n".join((self.ploidy, self.sex, *self.ploidy_zero))
        return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]

    def header(self) -> str:
        values = {
            "Schema": SCHEMA,
            "Engine": self.engine,
            "Version": self.version,
            "QualityModel": self.quality_model,
            "Bands": self.bands,
            "MinMQ": "" if self.min_mapping_quality is None else str(self.min_mapping_quality),
            "Region": self.region or "",
            "Ploidy": self.ploidy,
            "Sex": self.sex,
            "PloidyPolicy": self.ploidy_policy,
            "PloidyZero": ",".join(self.ploidy_zero),
        }  # fmt: skip
        body = ",".join(f'{key}="{_quote(value)}"' for key, value in values.items())
        return f"##{HEADER_KEY}=<{body}>"

    def metrics(self) -> dict[str, str | int | None]:
        """The provenance as result-manifest metrics of the gVCF output."""
        return {
            "schema": SCHEMA,
            "engine": self.engine,
            "engine_version": self.version,
            "quality_model": self.quality_model,
            "bands": self.bands or None,
            "min_mapping_quality": self.min_mapping_quality,
            "region": self.region,
            "ploidy": self.ploidy or None,
            "sex": self.sex or None,
            "ploidy_policy": self.ploidy_policy,
            "ploidy_zero": ",".join(self.ploidy_zero) or None,
        }


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def parse_provenance(line: str) -> Provenance | None:
    """The Provenance of a ``##wgseReferenceEvidence`` header line."""
    prefix = f"##{HEADER_KEY}=<"
    if not line.startswith(prefix):
        return None
    values = {
        key: re.sub(r"\\(.)", r"\1", value)
        for key, value in re.findall(r'(\w+)="((?:\\.|[^"\\])*)"', line[len(prefix) :])
    }
    if values.get("Schema") != SCHEMA:
        return None
    mq = values.get("MinMQ", "")
    return Provenance(
        engine=values.get("Engine", ""),
        version=values.get("Version", ""),
        quality_model=values.get("QualityModel", GQ),
        bands=values.get("Bands", ""),
        min_mapping_quality=int(mq) if mq.isdigit() else None,
        region=values.get("Region") or None,
        ploidy=values.get("Ploidy", ""),
        sex=values.get("Sex", ""),
        ploidy_zero=tuple(item for item in values.get("PloidyZero", "").split(",") if item),
    )  # fmt: skip


def canonical_region(intervals: Iterable[tuple[str, int, int]]) -> str:
    """Zero-based, half-open ``intervals`` as the merged, comma-separated
    "contig:start-end" (1-based, inclusive) items a Region records, contigs
    in the order they first appear: no file names, no thousands separators."""
    spans: dict[str, list[tuple[int, int]]] = {}
    for contig, start, end in intervals:
        spans.setdefault(contig, []).append((start, end))
    items = []
    for contig, ranges in spans.items():
        merged: list[list[int]] = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        items += [f"{contig}:{start + 1}-{end}" for start, end in merged]
    return ",".join(items)


def ploidy_definitions(ploidy_args: Sequence[str]) -> list[list[str]]:
    """bcftools call's ploidy rules for ``ploidy_args`` (``--ploidy NAME`` or
    ``--ploidy-file FILE``) as [contig, start, end, sex, ploidy] rows."""
    if "--ploidy-file" in ploidy_args:
        path = ploidy_args[list(ploidy_args).index("--ploidy-file") + 1]
        text = Path(path).read_text(encoding="utf-8")
    elif "--ploidy" in ploidy_args:
        name = ploidy_args[list(ploidy_args).index("--ploidy") + 1]
        if name.isdigit():
            return [["*", "*", "*", "*", name]]
        # bcftools prints a predefined table's rules for "NAME?".
        shown = run_command(
            ["bcftools", "call", "--ploidy", f"{name}?"],
            capture_output=True,
            check=False,
        )
        text = shown.stdout + shown.stderr
    else:
        return []
    return [
        row
        for line in text.splitlines()
        if len(row := line.split()) == 5 and not line.lstrip().startswith("#")
    ]


def ploidy_zero(
    rows: Iterable[Sequence[str]], sex: str, lengths: Mapping[str, int]
) -> tuple[str, ...]:
    """The reference's ploidy-0 intervals for a sample of ``sex`` (M or F)."""
    items = []
    for contig, start, end, rule_sex, ploidy in rows:
        if ploidy != "0" or rule_sex not in {sex, "*"} or contig not in lengths:
            continue
        first = int(start) if start.isdigit() else 1
        last = min(int(end), lengths[contig]) if end.isdigit() else lengths[contig]
        if first <= last:
            items.append(f"{contig}:{first}-{last}")
    return tuple(items)


@dataclass(frozen=True)
class Span:
    """Consecutive bases of one contig in one state (1-based, inclusive)."""

    contig: str
    start: int
    end: int
    state: State

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def _info(text: str) -> dict[str, str]:
    return dict(item.partition("=")[::2] for item in text.split(";") if item != ".")


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "", ".") else None
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "", ".") else None
    except ValueError:
        return None


def record_state(fields: Sequence[str], model: str, thresholds: Thresholds) -> State:
    """The state of the bases one gVCF record covers (its first sample)."""
    if fields[6] not in {"PASS", "."}:
        return State.FILTERED
    keys = fields[8].split(":") if len(fields) > 9 else []
    values = dict(zip(keys, fields[9].split(":"), strict=False)) if keys else {}
    alleles = re.split(r"[/|]", values.get("GT", "."))
    alts = fields[4].split(",")
    carried = [a for a in alleles if a.isdigit() and a != "0" and int(a) <= len(alts)]
    if any(alts[int(a) - 1] not in SYMBOLIC_REFERENCE_ALTS for a in carried):
        return State.VARIANT
    if carried or any(a != "0" for a in alleles):
        return State.NO_CALL
    info = _info(fields[7])
    depths = [d for d in (_int(info.get("MIN_DP")), _int(values.get("MIN_DP")), _int(values.get("DP"))) if d is not None]  # fmt: skip
    depth = min(depths) if depths else None
    if depth == 0:
        return State.UNCOVERED
    scale = 0.5 if len(alleles) == 1 else 1.0
    if depth is None or depth < thresholds.min_depth * scale:
        return State.LOW_QUALITY_REFERENCE
    if model == GQ:
        gq = _float(values.get("GQ"))
        if gq is None or gq < thresholds.min_gq:
            return State.LOW_QUALITY_REFERENCE
    return State.CALLABLE_REFERENCE


def _record_end(fields: Sequence[str]) -> int:
    end = _int(_info(fields[7]).get("END"))
    return end if end is not None else int(fields[1]) + len(fields[3]) - 1


def _merge(intervals: Iterable[Interval], order: Mapping[str, int]) -> list[Interval]:
    merged: list[Interval] = []
    for contig, start, end in sorted(
        intervals, key=lambda item: (order.get(item[0], len(order)), item[1])
    ):
        if merged and merged[-1][0] == contig and start <= merged[-1][2] + 1:
            merged[-1] = (contig, merged[-1][1], max(merged[-1][2], end))
        else:
            merged.append((contig, start, end))
    return merged


def _overlay(
    spans: Iterator[Span], intervals: Sequence[Interval], state: State, inside: bool
) -> Iterator[Span]:
    """Set ``state`` on the bases of ``spans`` inside (or, with ``inside``
    false, outside) ``intervals`` (merged and sorted per contig)."""
    by_contig: dict[str, list[tuple[int, int]]] = {}
    for contig, start, end in intervals:
        by_contig.setdefault(contig, []).append((start, end))
    for span in spans:
        position = span.start
        for start, end in by_contig.get(span.contig, []):
            if end < position or start > span.end:
                continue
            if start > position:
                yield Span(
                    span.contig, position, start - 1, span.state if inside else state
                )
            last = min(end, span.end)
            yield Span(
                span.contig, max(start, position), last, state if inside else span.state
            )
            position = last + 1
        if position <= span.end:
            yield Span(span.contig, position, span.end, span.state if inside else state)


def _joined(spans: Iterable[Span]) -> Iterator[Span]:
    pending: Span | None = None
    for span in spans:
        if (
            pending
            and pending.contig == span.contig
            and pending.state is span.state
            and pending.end + 1 == span.start
        ):
            pending = Span(span.contig, pending.start, span.end, span.state)
            continue
        if pending:
            yield pending
        pending = span
    if pending:
        yield pending


@dataclass
class ReferenceEvidence:
    """An indexed gVCF, read as reference evidence."""

    path: str
    lengths: dict[str, int] = field(init=False)
    provenance: Provenance | None = field(init=False)
    quality_model: str = field(init=False)

    def __post_init__(self) -> None:
        header = run_command(
            ["bcftools", "view", "-h", self.path], capture_output=True
        ).stdout
        self.lengths = header_contig_lengths(header)
        self.provenance = next(
            (p for line in header.splitlines() if (p := parse_provenance(line))), None
        )
        has_gq = "##FORMAT=<ID=GQ," in header
        self.quality_model = (
            self.provenance.quality_model
            if self.provenance
            else (GQ if has_gq else DEPTH_ONLY)
        )

    def assessed(self) -> list[Interval] | None:
        """The intervals the caller assessed, or None when unknown."""
        region = self.provenance.region if self.provenance else None
        if not region:
            return [(name, 1, length) for name, length in self.lengths.items()]
        try:
            selected = selected_intervals(region, self.lengths, commas=True)
        except ValueError:
            return None
        return [
            (contig, start + 1, end)
            for contig, ranges in selected.items()
            for start, end in ranges
        ]

    def excluded(self) -> list[Interval]:
        items = self.provenance.ploidy_zero if self.provenance else ()
        intervals = []
        for item in items:
            contig, _, span = item.rpartition(":")
            start, _, end = span.partition("-")
            intervals.append((contig, int(start), int(end)))
        return intervals

    def spans(
        self, thresholds: Thresholds, regions: Iterable[Interval] | None = None
    ) -> Iterator[Span]:
        """Every base of ``regions`` (default: every contig) in its state, as
        maximal spans in contig order. Bases no record covers are uncovered
        where the caller assessed them and not assessed elsewhere."""
        order = {name: index for index, name in enumerate(self.lengths)}
        requested = (
            regions
            if regions is not None
            else ((name, 1, length) for name, length in self.lengths.items())
        )
        wanted = _merge(
            (
                (contig, max(start, 1), min(end, self.lengths[contig]))
                for contig, start, end in requested
                if contig in self.lengths and start <= min(end, self.lengths[contig])
            ),
            order,
        )
        if not wanted:
            return
        assessed = self.assessed()
        observed = self._observed(wanted, thresholds, order)
        spans: Iterator[Span] = observed
        if assessed is not None:
            spans = _overlay(
                spans, _merge(assessed, order), State.NOT_ASSESSED, inside=False
            )
        else:
            spans = (
                Span(s.contig, s.start, s.end, State.NOT_ASSESSED)
                if s.state is State.UNCOVERED
                else s
                for s in spans
            )
        excluded = _merge(self.excluded(), order)
        if excluded:
            spans = _overlay(spans, excluded, State.EXCLUDED_BY_POLICY, inside=True)
        yield from _joined(spans)

    def _observed(
        self,
        wanted: list[Interval],
        thresholds: Thresholds,
        order: Mapping[str, int],
    ) -> Iterator[Span]:
        """Spans of ``wanted`` from the records alone (absent: uncovered)."""
        with tempfile.TemporaryDirectory(prefix="wgse_evidence_") as work:
            regions = Path(work, "regions.tsv")
            regions.write_text(
                "".join(f"{c}\t{s}\t{e}\n" for c, s, e in wanted),
                encoding="utf-8",
                newline="\n",
            )
            with open(
                Path(work, "stderr.txt"), "w+", encoding="utf-8", newline="\n"
            ) as errors:
                process = popen(
                    ["bcftools", "view", "-H", "-R", str(regions), self.path],
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    text=True,
                )
                assert process.stdout is not None
                try:
                    yield from _sweep(
                        wanted,
                        (line.rstrip("\n").split("\t") for line in process.stdout),
                        self.quality_model,
                        thresholds,
                        order,
                    )
                finally:
                    process.stdout.close()
                    code = process.wait()
                if code:
                    errors.seek(0)
                    raise WGSExtractError(
                        f"Reading reference evidence {self.path} failed: "
                        f"{errors.read().strip()[-500:]}"
                    )


def _sweep(
    wanted: list[Interval],
    records: Iterator[list[str]],
    model: str,
    thresholds: Thresholds,
    order: Mapping[str, int],
) -> Iterator[Span]:
    """Resolve overlapping records base by base, highest-ranked state first.
    Records arrive sorted, so only the few overlapping the cursor are held."""
    pending = next(records, None)
    active: list[tuple[str, int, State]] = []
    for contig, first, last in wanted:
        rank = order[contig]
        # Records of contigs already passed (none, from bcftools -R) are skipped.
        while pending is not None and order.get(pending[0], -1) < rank:
            pending = next(records, None)
        cursor = first
        while cursor <= last:
            while (
                pending is not None
                and pending[0] == contig
                and int(pending[1]) <= cursor
            ):
                end = _record_end(pending)
                if end >= cursor:
                    state = record_state(pending, model, thresholds)
                    active.append((contig, end, state))
                pending = next(records, None)
            active = [a for a in active if a[0] == contig and a[1] >= cursor]
            upcoming = last + 1
            if pending is not None and pending[0] == contig:
                upcoming = min(upcoming, int(pending[1]))
            if active:
                state = min((a[2] for a in active), key=_RANK.__getitem__)
                stop = min(min(a[1] for a in active), upcoming - 1)
            else:
                state, stop = State.UNCOVERED, upcoming - 1
            yield Span(contig, cursor, stop, state)
            cursor = stop + 1


def count_bases(spans: Iterable[Span]) -> Counter[State]:
    """Bases per state."""
    counts: Counter[State] = Counter()
    for span in spans:
        counts[span.state] += span.length
    return counts
