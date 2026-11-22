"""A sample's genotype and evidence state at a site or across a region.

Calls come from the input VCF (and, for a site its call set doesn't list,
the gVCF's own records); a site no call covers takes its state from the
reference evidence (docs/reference-evidence.md). A variants-only VCF without
a gVCF can't show that a base it doesn't list was read as reference, so such
a site is not assessed, never reference.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core import variant_confidence as vc
from wgsextract_cli.core.reference_evidence import (
    Provenance,
    ReferenceEvidence,
    Span,
    State,
    count_bases,
    header_declares_gvcf,
    parse_provenance,
    site_states,
)
from wgsextract_cli.core.reference_evidence_records import bcftools_gvcf_header
from wgsextract_cli.core.reference_resolver import header_contig_lengths
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command

from .identifiers import Locus, local_contig

# The coarse answer to "what is this sample's genotype here?".
CONFIDENT_CALL = "confident_call"
CONFIDENT_REFERENCE = "confident_reference"
WEAK_EVIDENCE = "weak_evidence"
NO_CALL = "no_call"
NOT_ASSESSED = "not_assessed"
STATUSES = (CONFIDENT_CALL, CONFIDENT_REFERENCE, WEAK_EVIDENCE, NO_CALL, NOT_ASSESSED)

_STATE_STATUS = {
    # A variant the gVCF records but whose call couldn't be read.
    State.VARIANT: WEAK_EVIDENCE,
    State.CALLABLE_REFERENCE: CONFIDENT_REFERENCE,
    State.LOW_QUALITY_REFERENCE: WEAK_EVIDENCE,
    State.FILTERED: WEAK_EVIDENCE,
    State.NO_CALL: NO_CALL,
    State.UNCOVERED: NOT_ASSESSED,
    State.NOT_ASSESSED: NOT_ASSESSED,
    State.EXCLUDED_BY_POLICY: NOT_ASSESSED,
}
REASONS = {
    "variant": "A non-reference genotype was called here.",
    "reference_call": "The call set records a reference genotype here.",
    "filtered": "The caller's FILTER (or FORMAT/FT) failed here.",
    "no_call": "The caller recorded a missing genotype (./.) here.",
    "partial_call": (
        "The genotype is partly missing (such as 1/.): an allele was seen, but "
        "not a complete genotype."
    ),
    State.CALLABLE_REFERENCE.value: (
        "The gVCF reads every base here as reference with enough depth "
        "(and genotype quality)."
    ),
    State.LOW_QUALITY_REFERENCE.value: (
        "The gVCF reads this as reference, below the depth or genotype-quality "
        "thresholds."
    ),
    State.UNCOVERED.value: "The caller assessed this site but no reads covered it.",
    State.NOT_ASSESSED.value: "Outside the region the caller assessed.",
    State.EXCLUDED_BY_POLICY.value: (
        "Ploidy 0 for this sample here (such as chrY in a female sample)."
    ),
    "variants_only": (
        "The VCF lists no call here and no reference evidence (gVCF) was found: "
        "a variants-only VCF can't show a base it doesn't list was read as "
        "reference, so this site was not assessed."
    ),
}
_SYMBOLIC_REFERENCE = vc.SYMBOLIC_REFERENCE_ALTS | {"."}
_CONTIG_ID = re.compile(r"^##contig=<(?:.*?,)?ID=([^,>]+)", re.MULTILINE)
_CONFIDENT = (vc.HIGH, vc.MODERATE)


@dataclass
class VcfSource:
    """A VCF/BCF and the one sample a lookup reads from it."""

    path: str
    sample: str
    samples: int
    first_sample: bool
    lengths: dict[str, int]
    header: str
    indexed: bool
    is_gvcf: bool
    # Every contig the header declares, with or without a length.
    contigs: list[str]
    provenance: Provenance | None

    def ploidy(self, chrom: str, start: int, end: int) -> int | None:
        """The ploidy the caller's recorded policy gives a record, if any."""
        return (
            self.provenance.record_ploidy(chrom, start, end)
            if self.provenance
            else None
        )


def _declares_gvcf(header: str) -> bool:
    return (
        header_declares_gvcf(header)
        or bcftools_gvcf_header(header)
        or "##ALT=<ID=NON_REF" in header
        or ("##INFO=<ID=END," in header and "##ALT=<ID=*," in header)
    )


def open_vcf(path: str, sample: str | None, *, option: str = "--input") -> VcfSource:
    """Read ``path``'s header and choose its sample: the only one, or
    ``sample``. A multi-sample VCF needs ``sample``; the first isn't assumed."""
    header = run_command(["bcftools", "view", "-h", path], capture_output=True).stdout
    names = [line for line in header.splitlines()[-1].split("\t")[9:] if line]
    if not names:
        raise UsageError(f"{option} {path} has no samples, so no genotypes.")
    if sample is None:
        if len(names) > 1:
            raise UsageError(
                f"{option} {path} has {len(names)} samples ({', '.join(names[:10])}"
                f"{', ...' if len(names) > 10 else ''}); choose one with --sample."
            )
        sample = names[0]
    elif sample not in names:
        raise UsageError(
            f"{option} {path} has no sample {sample!r}; it has {', '.join(names[:10])}."
        )
    indexed = any(Path(path + suffix).is_file() for suffix in (".tbi", ".csi"))
    lines = header.splitlines()
    return VcfSource(
        path,
        sample,
        len(names),
        sample == names[0],
        header_contig_lengths(header),
        header,
        indexed,
        _declares_gvcf(header),
        list(dict.fromkeys(_CONTIG_ID.findall(header))),
        next((p for line in lines if (p := parse_provenance(line))), None),
    )


def records(source: VcfSource, chrom: str, start: int, end: int) -> list[list[str]]:
    """The selected sample's records overlapping chrom:start-end."""
    if source.contigs and chrom not in source.contigs:
        return []
    where = (
        ["--regions-overlap", "1", "-r"]
        if source.indexed
        else ["--targets-overlap", "1", "-t"]
    )
    command = ["bcftools", "view", "--no-version", "-H"]
    if source.samples > 1:
        command += ["-s", source.sample]
    command += [*where, f"{chrom}:{start}-{end}", source.path]
    try:
        completed = run_command(command, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise WGSExtractError(
            f"Reading {source.path} at {chrom}:{start}-{end} failed: "
            f"{(e.stderr or '').strip()[-300:]}"
        ) from None
    return [line.split("\t") for line in completed.stdout.splitlines() if line]


# Reference blocks, and Clair3's deletion anchors, can start this far before
# the bases asked about.
EVIDENCE_PAD = 1000


def evidence_file(source: VcfSource, locus: Locus, work: Path) -> str:
    """The sample's gVCF, as the classifier reads it: the file itself when it
    is an indexed, bgzipped single-sample VCF, else the sample's records
    around ``locus`` copied into ``work`` (never the whole genome)."""
    if source.samples == 1 and source.indexed and source.path.endswith((".gz", ".bgz")):
        return source.path
    out = work / "evidence.g.vcf.gz"
    chrom = local_contig(locus.chrom, source.contigs)
    command = ["bcftools", "view", "--no-version", "-s", source.sample, "-Oz"]
    if chrom is None:
        command.append("-h")
    else:
        span = (
            f"{chrom}:{max(1, locus.start - EVIDENCE_PAD)}-{locus.end + EVIDENCE_PAD}"
        )
        command += (
            ["--regions-overlap", "1", "-r", span]
            if source.indexed
            else ["--targets-overlap", "1", "-t", span]
        )
    try:
        run_command([*command, "-o", str(out), source.path])
        run_command(["bcftools", "index", "-t", str(out)])
    except subprocess.CalledProcessError as e:
        raise WGSExtractError(
            f"Reading reference evidence {source.path} failed: "
            f"{(e.stderr or '').strip()[-300:]}"
        ) from None
    return str(out)


# Calls -----------------------------------------------------------------------


def minimal(pos: int, ref: str, alt: str) -> tuple[int, str, str]:
    """An allele's minimal representation: shared trailing bases, then
    shared leading ones, trimmed, keeping one anchor base for an indel."""
    while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt, pos = ref[1:], alt[1:], pos + 1
    return pos, ref, alt


def variant_type(ref: str, alt: str) -> str:
    if alt.startswith("<"):
        return "symbolic"
    if alt == "*":
        return "spanning_deletion"
    if len(ref) == len(alt):
        return "snv" if len(ref) == 1 else "mnv"
    if len(ref) == 1 and alt[0] == ref:
        return "insertion"
    if len(alt) == 1 and ref[0] == alt:
        return "deletion"
    return "complex"


def _allele_key(pos: int, ref: str, alt: str) -> tuple[int, str, str]:
    if alt.startswith("<") or alt == "*":
        return pos, ref.upper(), alt.upper()
    return minimal(pos, ref.upper(), alt.upper())


def _whole(value: float | None) -> float | int | None:
    return int(value) if value is not None and float(value).is_integer() else value


def _number(value: str | None) -> float | int | None:
    if value in (None, "", "."):
        return None
    try:
        number = float(str(value))
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


@dataclass
class Call:
    """One record of the selected sample."""

    fields: list[str]
    vcf: VcfSource
    source: str  # input or reference_evidence

    @property
    def chrom(self) -> str:
        return self.fields[0]

    @property
    def pos(self) -> int:
        return int(self.fields[1])

    @property
    def ref(self) -> str:
        return self.fields[3]

    @property
    def alleles(self) -> list[str]:
        return [self.ref, *self.fields[4].split(",")]

    @property
    def info(self) -> dict[str, str]:
        return dict(
            item.partition("=")[::2]
            for item in self.fields[7].split(";")
            if item != "."
        )

    @property
    def fmt(self) -> dict[str, str]:
        if len(self.fields) < 10:
            return {}
        return dict(
            zip(self.fields[8].split(":"), self.fields[9].split(":"), strict=False)
        )

    @property
    def end(self) -> int:
        end = _number(self.info.get("END"))
        return int(end) if isinstance(end, int) else self.pos + len(self.ref) - 1

    @property
    def gt(self) -> str:
        return self.fmt.get("GT", ".")

    @property
    def indices(self) -> list[str]:
        return re.split(r"[/|]", self.gt)

    @property
    def phased(self) -> bool:
        return "|" in self.gt and "." not in self.indices

    @property
    def block(self) -> bool:
        """A gVCF reference block, not a call."""
        return "END" in self.info and all(
            a in _SYMBOLIC_REFERENCE for a in self.alleles[1:]
        )

    @property
    def filter_failed(self) -> bool:
        ft = self.fmt.get("FT", ".")
        return self.fields[6] not in ("PASS", ".") or ft not in ("PASS", ".", "")

    @property
    def state(self) -> str:
        if all(i == "." for i in self.indices):
            return "no_call"
        if "." in self.indices:
            return "partial_call"
        if self.filter_failed:
            return "filtered"
        if any(i not in ("0", ".") for i in self.indices):
            return "variant"
        return "reference_call"

    def carried(self) -> list[int]:
        """The ALT numbers the genotype carries."""
        return sorted({int(i) for i in self.indices if i not in ("0", ".")})

    def allele_at(self, index: str) -> str | None:
        if index == ".":
            return None
        alleles = self.alleles
        number = int(index)
        return alleles[number] if number < len(alleles) else None

    def confidence(
        self, thresholds: vc.Thresholds, caller: str, mq_kind: str
    ) -> vc.CallConfidence | None:
        """Its label: the weakest of its carried ALTs', or its reference call's."""
        if self.state == "no_call":
            return None
        alts = self.alleles[1:] or ["."]

        def classify(number: int, reference: bool) -> vc.CallConfidence | None:
            return vc.classify(
                alt=number,
                alleles=self.indices,
                ref=self.ref,
                alt_allele=alts[number - 1],
                filters=self.fields[6],
                info=self.info,
                fmt=self.fmt,
                n_alts=len(alts),
                thresholds=thresholds,
                mq_kind=mq_kind,
                caller=caller,
                samples=self.vcf.samples,
                reference=reference,
                ploidy=self.vcf.ploidy(self.chrom, self.pos, self.end),
            )

        carried = [n for n in self.carried() if n <= len(alts)]
        if not carried:
            return classify(1, True)
        results = [r for n in carried if (r := classify(n, False)) is not None]
        if not results:
            return None
        worst = max(results, key=lambda r: vc.LABELS.index(r.label))
        reasons = list(dict.fromkeys(x for r in results for x in r.reasons))
        return vc.CallConfidence(
            worst.label,
            reasons,
            worst.zygosity,
            worst.depth,
            worst.genotype_quality,
            worst.alt_fraction,
        )

    def variants(self) -> list[dict[str, object]]:
        out = []
        for number in self.carried():
            allele = self.allele_at(str(number))
            if allele is None:
                continue
            pos, ref, alt = _allele_key(self.pos, self.ref, allele)
            out.append(
                {
                    "allele": allele,
                    "pos": pos,
                    "ref": ref,
                    "alt": alt,
                    "type": variant_type(ref, alt),
                    "copies": self.indices.count(str(number)),
                }
            )
        return out

    def copies_of(self, locus: Locus) -> int:
        """Copies of the queried REF>ALT the genotype carries."""
        assert locus.ref and locus.alt
        wanted = _allele_key(locus.start, locus.ref, locus.alt)
        return sum(
            1
            for index in self.indices
            if index not in ("0", ".")
            and (allele := self.allele_at(index)) is not None
            and _allele_key(self.pos, self.ref, allele) == wanted
        )

    def document(
        self, thresholds: vc.Thresholds, caller: str, mq_kind: str
    ) -> dict[str, object]:
        fmt = self.fmt
        label = self.confidence(thresholds, caller, mq_kind)
        ids = [i for i in self.fields[2].split(";") if i and i != "."]
        out: dict[str, object] = {
            "chrom": self.chrom,
            "pos": self.pos,
            "end": self.end,
            "ids": ids,
            "ref": self.ref,
            "alts": [a for a in self.alleles[1:] if a not in _SYMBOLIC_REFERENCE],
            "filter": self.fields[6],
            "state": self.state,
            "genotype": self.gt,
            "alleles": [self.allele_at(i) for i in self.indices],
            "zygosity": label.zygosity if label else None,
            "phased": self.phased,
            "phase_set": _phase_set(self),
            "depth": _whole(label.depth) if label else _number(fmt.get("DP")),
            "genotype_quality": _whole(label.genotype_quality) if label else _number(fmt.get("GQ")),
            "allele_depths": [_number(v) for v in fmt["AD"].split(",")] if fmt.get("AD") not in (None, ".") else None,
            "alt_fraction": label.alt_fraction if label else None,
            "confidence": label.label if label else None,
            "confidence_reasons": label.reasons if label else [],
            "variants": self.variants(),
            "source": self.source,
        }  # fmt: skip
        if self.phased:
            out["haplotypes"] = [self.allele_at(i) for i in self.indices]
        return out


def calls(rows: Iterable[list[str]], vcf: VcfSource, source: str) -> list[Call]:
    """The rows that are calls (not reference blocks)."""
    found = [Call(row, vcf, source) for row in rows]
    return [c for c in found if not c.block]


# Sites -----------------------------------------------------------------------


@dataclass
class Evidence:
    """Reference evidence for the selected sample, and where it came from."""

    reader: ReferenceEvidence
    path: str
    origin: str  # input, explicit or manifest


def _primary(found: Sequence[Call], locus: Locus) -> Call:
    def rank(call: Call) -> tuple[int, int, int]:
        matches = bool(locus.ref and locus.alt and call.copies_of(locus))
        return (not matches, call.pos != locus.start, call.pos)

    return min(found, key=rank)


def _evidence_state(
    evidence: Evidence | None, locus: Locus, thresholds: vc.Thresholds
) -> State | None:
    if evidence is None:
        return None
    site = (locus.chrom, locus.start, locus.end)
    return next(iter(site_states(evidence.reader, [site], thresholds)))[1]


def _block_genotype(gvcf: VcfSource | None, locus: Locus) -> str | None:
    """The reference genotype, as the gVCF writes it (0/0, or 0 where the
    caller called a haploid), of the records covering ``locus``; None when
    they disagree or there are none."""
    if gvcf is None:
        return None
    chrom = local_contig(locus.chrom, gvcf.contigs) or locus.chrom
    found = set()
    for row in records(gvcf, chrom, locus.start, locus.end):
        call = Call(row, gvcf, "reference_evidence")
        if all(i == "0" for i in call.indices):
            found.add(call.gt)
    return found.pop() if len(found) == 1 else None


def site_result(
    source: VcfSource,
    evidence: Evidence | None,
    locus: Locus,
    thresholds: vc.Thresholds,
    gvcf_source: VcfSource | None,
) -> dict[str, object]:
    """The sample's genotype and evidence state at ``locus``."""
    caller = vc.detect_caller(source.header.splitlines())
    mq_kind = vc.mapping_quality_kind(source.header.splitlines())
    chrom = local_contig(locus.chrom, source.contigs) or locus.chrom
    found = calls(records(source, chrom, locus.start, locus.end), source, "input")
    state = None if found else _evidence_state(evidence, locus, thresholds)
    if state in (State.VARIANT, State.FILTERED, State.NO_CALL) and gvcf_source:
        # A call the gVCF records but the call set doesn't list.
        local = local_contig(locus.chrom, gvcf_source.contigs) or locus.chrom
        found = calls(
            records(gvcf_source, local, locus.start, locus.end),
            gvcf_source,
            "reference_evidence",
        )
    documents = [c.document(thresholds, caller, mq_kind) for c in found]
    result: dict[str, object] = {"locus": locus.document(), "calls": documents}
    if found:
        primary = _primary(found, locus)
        document = documents[found.index(primary)]
        kind = primary.state
        label = document["confidence"]
        if kind in ("no_call", "partial_call"):
            status = NO_CALL
        elif kind == "reference_call":
            status = CONFIDENT_REFERENCE if label in _CONFIDENT else WEAK_EVIDENCE
        else:
            status = (
                CONFIDENT_CALL
                if label in _CONFIDENT and kind == "variant"
                else WEAK_EVIDENCE
            )
        result.update(
            status=status,
            evidence_state=kind,
            reason=REASONS[kind],
            genotype=document["genotype"],
            alleles=document["alleles"],
            confidence=label,
        )
        if locus.ref and locus.alt:
            result["query_allele_copies"] = (
                None if status == NO_CALL else primary.copies_of(locus)
            )
    else:
        key = state.value if state is not None else "variants_only"
        status = _STATE_STATUS.get(state, NOT_ASSESSED) if state else NOT_ASSESSED
        result.update(
            status=status,
            evidence_state=state.value if state else State.NOT_ASSESSED.value,
            reason=REASONS[key],
            genotype=None,
            alleles=None,
            confidence=None,
        )
        reference = state in (State.CALLABLE_REFERENCE, State.LOW_QUALITY_REFERENCE)
        genotype = _block_genotype(gvcf_source, locus) if reference else None
        if genotype is not None:
            copies = len(re.split(r"[/|]", genotype))
            result["genotype"] = genotype
            result["alleles"] = [locus.ref] * copies if locus.ref else None
        if locus.ref and locus.alt:
            result["query_allele_copies"] = 0 if status == CONFIDENT_REFERENCE else None
    result["reference_evidence_state"] = state.value if state else None
    return result


# Regions ---------------------------------------------------------------------


def _phase_set(call: Call) -> str | None:
    value = call.fmt.get("PS")
    return value if call.phased and value not in (None, ".") else None


def _phase_sets(found: Sequence[Call]) -> list[dict[str, object]]:
    """Phased calls grouped by phase set (one implicit set per contig for
    phased calls without PS), with the alleles on each copy in order."""
    groups: dict[tuple[str, str | None], list[Call]] = {}
    for call in found:
        if call.phased:
            groups.setdefault((call.chrom, _phase_set(call)), []).append(call)
    out: list[dict[str, object]] = []
    for (chrom, phase_set), members in groups.items():
        copies = max(len(m.indices) for m in members)
        out.append(
            {
                "chrom": chrom,
                "phase_set": phase_set,
                "positions": [m.pos for m in members],
                "haplotypes": [
                    [
                        m.allele_at(m.indices[i]) if i < len(m.indices) else None
                        for m in members
                    ]
                    for i in range(copies)
                ],
            }
        )
    return out


def _span(span: Span) -> dict[str, object]:
    return {
        "chrom": span.contig,
        "start": span.start,
        "end": span.end,
        "state": span.state.value,
    }


def region_result(
    source: VcfSource,
    evidence: Evidence | None,
    locus: Locus,
    thresholds: vc.Thresholds,
    *,
    limit: int,
    offset: int,
) -> dict[str, object]:
    """The calls in ``locus`` (one page of them) and its bases per state."""
    caller = vc.detect_caller(source.header.splitlines())
    mq_kind = vc.mapping_quality_kind(source.header.splitlines())
    chrom = local_contig(locus.chrom, source.contigs) or locus.chrom
    found = calls(records(source, chrom, locus.start, locus.end), source, "input")
    documents = [c.document(thresholds, caller, mq_kind) for c in found]
    page = documents[offset : offset + limit]
    more = offset + limit < len(documents)
    length = locus.end - locus.start + 1
    if evidence is not None:
        local = local_contig(locus.chrom, evidence.reader.lengths)
        spans = (
            list(evidence.reader.spans(thresholds, [(local, locus.start, locus.end)]))
            if local
            else []
        )
        counts = {s.value: n for s, n in count_bases(spans).items()}
        missing = length - sum(counts.values())
        if missing > 0:
            counts[State.NOT_ASSESSED.value] = (
                counts.get(State.NOT_ASSESSED.value, 0) + missing
            )
        weak = [s for s in spans if s.state is not State.CALLABLE_REFERENCE]
        bases: dict[str, object] = {
            "basis": "reference_evidence",
            "bases": dict(sorted(counts.items())),
            "spans": [_span(s) for s in weak[:limit]],
            "spans_truncated": len(weak) > limit,
        }
    else:
        bases = {
            "basis": "variants_only",
            "bases": {State.NOT_ASSESSED.value: length},
            "spans": [],
            "spans_truncated": False,
            "reason": REASONS["variants_only"],
        }
    statuses: dict[str, int] = {}
    for doc in documents:
        kind = str(doc["state"])
        statuses[kind] = statuses.get(kind, 0) + 1
    return {
        "locus": locus.document(),
        "evidence": bases,
        "call_count": len(documents),
        "call_states": statuses,
        "calls": page,
        "next_cursor": str(offset + limit) if more else None,
        "phase_sets": _phase_sets(found),
    }
