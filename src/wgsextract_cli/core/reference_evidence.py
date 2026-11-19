"""Reference evidence: what a gVCF says about every base, not only variants.

A variant-only VCF cannot tell a confident reference base from one nobody
looked at. With ``--reference-evidence gvcf`` a caller also publishes a gVCF
(role ``reference_evidence``, its index ``reference_evidence_index``) next to
its unchanged variant VCF. ``spans`` reads one and classifies every requested
base into one ``State`` (``site_states``: whole sites); ``score pgs``, ``vcf
pharmcat``, the confidence labels and the ancestry projection find a VCF's
gVCF with ``companion``.
docs/reference-evidence.md explains each state and the rules.

The gVCF's ``##wgseReferenceEvidence`` header (``provenance_header``) records
how it was made: the engine and its version, its quality model (``gq``: blocks
carry GQ; ``depth_only``: bcftools blocks carry only DP/MIN_DP), its bands, the
assessed region and the bases its ploidy policy excludes (ploidy 0, such as a
woman's chrY). A gVCF without the header (another tool's) is read with the
model its FORMAT/GQ declaration implies and no exclusions.

Public imports stay here. ``reference_evidence_records`` shares the record
states and interval sweep with ``clair3_reference_shadow``, which owns the
missed-deletion rule and its complete-anchor boundary queries.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
import subprocess
import tempfile
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path

from wgsextract_cli.core.clair3_reference_shadow import (
    CLAIR3_DELETION_DEPTH as CLAIR3_DELETION_DEPTH,
)
from wgsextract_cli.core.clair3_reference_shadow import (
    CLAIR3_DELETION_SPAN as CLAIR3_DELETION_SPAN,
)
from wgsextract_cli.core.clair3_reference_shadow import (
    classified_records as _classified,
)
from wgsextract_cli.core.clair3_reference_shadow import query_intervals
from wgsextract_cli.core.paths import PathInput
from wgsextract_cli.core.reference_evidence_records import _RANK, _merge, _sweep
from wgsextract_cli.core.reference_evidence_records import (
    DEPTH_ONLY as DEPTH_ONLY,
)
from wgsextract_cli.core.reference_evidence_records import (
    GQ as GQ,
)
from wgsextract_cli.core.reference_evidence_records import (
    Classified as Classified,
)
from wgsextract_cli.core.reference_evidence_records import (
    Interval as Interval,
)
from wgsextract_cli.core.reference_evidence_records import (
    Span as Span,
)
from wgsextract_cli.core.reference_evidence_records import (
    State as State,
)
from wgsextract_cli.core.reference_evidence_records import record_state as record_state
from wgsextract_cli.core.reference_evidence_records import (
    reference_depth as reference_depth,
)
from wgsextract_cli.core.reference_resolver import header_contig_lengths
from wgsextract_cli.core.region_selection import selected_intervals
from wgsextract_cli.core.utils import WGSExtractError, popen, run_command
from wgsextract_cli.core.variant_confidence import Thresholds

SCHEMA = "wgsextract.reference_evidence/1"
ROLE, INDEX_ROLE = "reference_evidence", "reference_evidence_index"
HEADER_KEY = "wgseReferenceEvidence"
POLICY_HEADER_KEY = "wgsePloidyPolicy"
# Blocks below the first band would be written one record per base.
BCFTOOLS_BANDS = "0,5,10,20"
# Reads under this MAPQ are not evidence for a reference claim; GATK
# HaplotypeCaller drops them too (MappingQualityReadFilter, default 20).
BCFTOOLS_MIN_MAPPING_QUALITY = 20
# GATK's default bands (one per GQ below 60) make a genome's gVCF ~6.5 GB;
# these make it ~1.4 GB with the same GQ >= 20 states (#863).
GATK_BANDS = "10,20,30,60"
GATK_MIN_MAPPING_QUALITY = 20
# Clair3's --min_mq default (shared/param_p.py, param_f.py).
CLAIR3_MIN_MAPPING_QUALITY = 5


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

    @cached_property
    def _ploidy_intervals(self) -> dict[str, list[tuple[int, int, int]]]:
        intervals: dict[str, list[tuple[int, int, int]]] = {}
        for item in self.ploidy.split(","):
            match = re.fullmatch(r"(.+):(\d+)-(\d+)=([012])", item)
            if match:
                chrom, start, end, ploidy = match.groups()
                intervals.setdefault(chrom, []).append(
                    (int(start), int(end), int(ploidy))
                )
        return {chrom: sorted(spans) for chrom, spans in intervals.items()}

    def record_ploidy(self, chrom: str, start: int, end: int) -> int | None:
        """Effective ploidy of an inclusive record span, not its GT encoding.

        A span touching diploid PAR stays diploid. Headers without interval
        rules (including bcftools' named policies) leave GT-based inference intact.
        """
        if not self._ploidy_intervals:
            return None
        spans = self._ploidy_intervals.get(chrom, [])
        index = bisect_right(spans, start, key=lambda span: span[0]) - 1
        if index >= 0:
            left, right, ploidy = spans[index]
            if left <= start <= end <= right:
                return ploidy
        return 2

    @property
    def ploidy_policy(self) -> str:
        """A digest of the ploidy rules and sex the caller applied."""
        text = "\n".join((self.ploidy, self.sex, *self.ploidy_zero))
        return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]

    def header(self, *, key: str = HEADER_KEY) -> str:
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
        return f"##{key}=<{body}>"

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
    prefix = next(
        (
            f"##{key}=<"
            for key in (HEADER_KEY, POLICY_HEADER_KEY)
            if line.startswith(f"##{key}=<")
        ),
        None,
    )
    if prefix is None:
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


_CONTIG_ID = re.compile(r"^##contig=<(?:.*?,)?ID=([^,>]+)", re.MULTILINE)


@dataclass
class ReferenceEvidence:
    """An indexed gVCF, read as reference evidence. ``known_lengths`` (say,
    from the reference FASTA's index) supplies the length of a contig the
    header declares without one; a contig of unknown length is not assessed."""

    path: str
    known_lengths: Mapping[str, int] = field(default_factory=dict, repr=False)
    lengths: dict[str, int] = field(init=False)
    provenance: Provenance | None = field(init=False)
    quality_model: str = field(init=False)
    deletion_shadow: bool = field(init=False)

    def __post_init__(self) -> None:
        header = run_command(
            ["bcftools", "view", "-h", self.path], capture_output=True
        ).stdout
        declared = header_contig_lengths(header)
        self.lengths = {}
        for name in _CONTIG_ID.findall(header):
            length = declared.get(name) or self.known_lengths.get(name)
            if length:
                self.lengths.setdefault(name, length)
        self.provenance = next(
            (p for line in header.splitlines() if (p := parse_provenance(line))), None
        )
        engine = self.provenance.engine if self.provenance else None
        # Another tool's gVCF from Clair3 says so in its ##source line.
        self.deletion_shadow = engine == "clair3" or (
            engine is None and "\n##source=Clair3" in f"\n{header}"
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

    def normalized(self, reference: str, work: Path) -> ReferenceEvidence:
        """Left-align the evidence against the same FASTA as the checked alleles.

        Blocks retain their coordinates; moved indels take precedence over
        overlapping reference blocks. Provenance intervals keep their meaning
        when chromosome aliases are renamed to the FASTA's names.
        """
        from wgsextract_cli.core.reference_resolver import read_fai_lengths
        from wgsextract_cli.core.variant_files import (
            ensure_vcf_indexed,
            match_chromosome_name,
            normalize_vcf_chromosomes,
        )

        lengths = read_fai_lengths(reference)
        header = run_command(
            ["bcftools", "view", "-h", self.path], capture_output=True
        ).stdout
        original = ReferenceEvidence(
            self.path,
            {
                contig: lengths[alias]
                for contig in _CONTIG_ID.findall(header)
                if (alias := match_chromosome_name(contig, lengths))
            },
        )
        renamed = normalize_vcf_chromosomes(self.path, list(lengths), str(work))
        split = work / "evidence.split.vcf.gz"
        run_command(
            [
                "bcftools",
                "norm",
                "-m",
                "-any",
                "--multi-overlaps",
                ".",
                "-Oz",
                "-o",
                str(split),
                renamed,
            ],
            capture_output=True,
        )
        aligned, output = work / "evidence.aligned.vcf.gz", work / "evidence.vcf.gz"
        run_command(
            [
                "bcftools",
                "norm",
                "-f",
                reference,
                "-c",
                "e",
                "-Oz",
                "-o",
                str(aligned),
                str(split),
            ],
            capture_output=True,
        )
        run_command(
            [
                "bcftools",
                "sort",
                "-T",
                str(work / "evidence-sort"),
                "-Oz",
                "-o",
                str(output),
                str(aligned),
            ],
            capture_output=True,
        )
        ensure_vcf_indexed(str(output))
        result = ReferenceEvidence(str(output), lengths)
        if self.provenance:

            def mapped(intervals: Iterable[Interval]) -> list[Interval]:
                return [
                    (alias, start, end)
                    for contig, start, end in intervals
                    if (alias := match_chromosome_name(contig, lengths))
                ]

            assessed = original.assessed()
            result.provenance = replace(
                self.provenance,
                region=(
                    canonical_region((c, s - 1, e) for c, s, e in mapped(assessed))
                    if assessed is not None
                    else self.provenance.region
                ),
                ploidy_zero=tuple(
                    f"{c}:{s}-{e}" for c, s, e in mapped(self.excluded())
                ),
                ploidy=(
                    ",".join(
                        f"{alias}:{start}-{end}={ploidy}"
                        for contig, spans in self.provenance._ploidy_intervals.items()
                        if (alias := match_chromosome_name(contig, lengths))
                        for start, end, ploidy in spans
                    )
                    if self.provenance._ploidy_intervals
                    else self.provenance.ploidy
                ),
            )
        return result

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
            queried = self._query_intervals(wanted, thresholds, order, regions)
            regions.write_text(
                "".join(f"{c}\t{s}\t{e}\n" for c, s, e in queried),
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
                    records = (line.rstrip("\n").split("\t") for line in process.stdout)
                    yield from _sweep(
                        wanted,
                        _classified(
                            records,
                            self.quality_model,
                            thresholds,
                            self.deletion_shadow,
                            self.provenance.record_ploidy if self.provenance else None,
                        ),
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

    def _query_intervals(
        self,
        wanted: list[Interval],
        thresholds: Thresholds,
        order: Mapping[str, int],
        regions: Path,
    ) -> list[Interval]:
        """Include complete anchor chains; batch boundary probes for point lists."""
        if not self.deletion_shadow:
            return wanted
        return query_intervals(
            self.path, self.quality_model, wanted, thresholds, order, regions
        )


def count_bases(spans: Iterable[Span]) -> Counter[State]:
    """Bases per state."""
    counts: Counter[State] = Counter()
    for span in spans:
        counts[span.state] += span.length
    return counts


def _recorded_file(
    record: Mapping[str, object], manifest: Path, document: Mapping[str, object]
) -> Path | None:
    """A file a result manifest recorded, where it is now (relative to the
    manifest when its folder has moved), if its recorded fingerprint shows
    it unchanged since; without one the file can't be trusted to be the
    run's."""
    from wgsextract_cli.core.results import fingerprint

    recorded = Path(str(record.get("path") or ""))
    place = recorded
    original = document.get("manifest")
    if not recorded.is_file() and isinstance(original, str):
        try:
            place = manifest.parent / recorded.relative_to(Path(original).parent)
        except ValueError:
            return None
    expected = record.get("fingerprint")
    if not expected or not place.is_file() or fingerprint(place) != expected:
        return None
    return place


def companion(vcf: PathInput) -> str | None:
    """The gVCF the run that wrote ``vcf`` published beside it, if any: the
    ``reference_evidence`` output of a successful result manifest that also
    lists ``vcf`` among its outputs. The manifests read are those in the
    VCF's folder and those its sample workspace recorded; both files must
    still be the ones the manifest describes (same fingerprint), and the gVCF
    indexed. Of several such runs the newest wins."""
    from wgsextract_cli.core import sample_workspace
    from wgsextract_cli.core.results import MANIFEST_SUFFIX

    given = Path(vcf).expanduser().absolute()
    path = given.resolve()
    if not path.is_file():
        return None
    candidates = {
        manifest
        for folder in {given.parent, path.parent}
        for manifest in folder.glob(f"*{MANIFEST_SUFFIX}")
    }
    workspace = sample_workspace.workspace_for(given)
    if workspace is not None:
        candidates.update(workspace.recorded_manifests())
    found: list[tuple[str, str]] = []
    for manifest in candidates:
        try:
            document = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(document, dict) or document.get("status") != "ok":
            continue
        outputs = [o for o in document.get("outputs") or [] if isinstance(o, dict)]
        if not any(
            o.get("role") != ROLE
            and (p := _recorded_file(o, manifest, document)) is not None
            and p.resolve() == path
            for o in outputs
        ):
            continue
        provenance = document.get("provenance")
        finished = str(
            (provenance.get("finished") if isinstance(provenance, dict) else "") or ""
        )
        for record in outputs:
            if record.get("role") != ROLE:
                continue
            gvcf = _recorded_file(record, manifest, document)
            if gvcf is not None and any(
                Path(f"{gvcf}{suffix}").is_file() for suffix in (".tbi", ".csi")
            ):
                found.append((finished, str(gvcf)))
    return max(found)[1] if found else None


def site_state(states: Iterable[State]) -> State:
    """A site's state from its bases' states: callable reference only when
    every base is; otherwise the strongest claim against reference among the
    rest (not assessed included)."""
    seen = list(states)
    failing = [s for s in seen if s is not State.CALLABLE_REFERENCE]
    if failing or not seen:
        return min(failing or [State.NOT_ASSESSED], key=_RANK.__getitem__)
    return State.CALLABLE_REFERENCE


def _contig_key(name: str) -> str:
    key = name.removeprefix("chr")
    return "MT" if key == "M" else key


def site_states(
    source: ReferenceEvidence,
    sites: Iterable[Interval],
    thresholds: Thresholds,
) -> Iterator[tuple[Interval, State]]:
    """Each site (contig, 1-based start, inclusive end) with its state
    (``site_state`` of its bases). Contig names are matched with or without
    "chr" (and M/MT); a site on a contig the gVCF lacks is not assessed."""
    names = {_contig_key(name): name for name in source.lengths}
    order = {name: index for index, name in enumerate(source.lengths)}
    given = sorted(
        set(sites),
        key=lambda s: (order.get(names.get(_contig_key(s[0]), ""), len(order)), s[1]),
    )
    local = [(names.get(_contig_key(c)), s, e) for c, s, e in given]
    ends: dict[str, list[int]] = {}
    found: dict[str, list[Span]] = {}
    for span in source.spans(thresholds, ((c, s, e) for c, s, e in local if c)):
        ends.setdefault(span.contig, []).append(span.end)
        found.setdefault(span.contig, []).append(span)
    for site, (contig, start, end) in zip(given, local, strict=True):
        if contig is None:
            yield site, State.NOT_ASSESSED
            continue
        spans = found.get(contig, [])
        index = bisect.bisect_left(ends.get(contig, []), start)
        states: list[State] = []
        covered = 0
        while index < len(spans) and spans[index].start <= end:
            span = spans[index]
            states.append(span.state)
            covered += min(span.end, end) - max(span.start, start) + 1
            index += 1
        if covered < end - start + 1:
            states.append(State.NOT_ASSESSED)
        yield site, site_state(states)


def readable(gvcf: str, sample: str | None, work: Path) -> str | None:
    """``gvcf`` as the classifier reads it (an indexed bgzipped gVCF whose
    first sample is ``sample``): itself, or a copy in ``work``. None when it
    doesn't hold ``sample``."""
    samples = run_command(
        ["bcftools", "query", "-l", gvcf], capture_output=True
    ).stdout.splitlines()
    if not samples or (sample is not None and sample not in samples):
        return None
    indexed = gvcf.endswith((".gz", ".bgz")) and any(
        Path(gvcf + suffix).is_file() for suffix in (".tbi", ".csi")
    )
    if (sample is None or samples[0] == sample) and len(samples) == 1 and indexed:
        return gvcf
    copy = str(work / "evidence.g.vcf.gz")
    run_command(["bcftools", "view", "--no-version", "-s", sample or samples[0],
                 "-Oz", "-o", copy, gvcf])  # fmt: skip
    run_command(["bcftools", "index", "-t", copy])
    return copy
