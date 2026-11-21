# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from genid/Yleaf v4.1.6; see provenance.json.
# Copyright Diego Montiel Gonzalez and Bram van Wersch, Erasmus MC.
# Modified 2026-11-20 by WGS Extract contributors: shared interval evidence
# and streamed Y VCF calls.
"""Stream Y calls through the shared reference-evidence interval classifier."""

import heapq
import math
import subprocess
from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from wgsextract_cli.core.reference_evidence import (
    ReferenceEvidence,
    State,
    record_state,
)
from wgsextract_cli.core.utils import WGSExtractError, popen, run_command
from wgsextract_cli.core.variant_confidence import Thresholds
from wgsextract_cli.core.variant_files import vcf_index_chromosomes

from .markers import Call, ad_call, gt_call

Y_NAMES = ("Y", "chrY")


def _call(fields: list[str], block: bool, quality: int) -> Call:
    ref, alt = fields[3:5]
    values = dict(zip(fields[8].split(":"), fields[9].split(":"), strict=False))
    info = dict(part.partition("=")[::2] for part in fields[7].split(";"))
    gt = values.get("GT", ".")
    genotype = gt_call(ref, alt, gt)
    gq = float(values["GQ"]) if values.get("GQ", ".") != "." else None
    metrics = [
        int(value)
        for value in (
            values.get("MIN_DP", "."),
            values.get("DP", "."),
            info.get("MIN_DP", ".") if block else ".",
        )
        if value != "."
    ]
    description = ""
    if fields[6] != "PASS" or values.get("FT", ".") not in (".", "PASS"):
        description = "Filtered or unassessed variant"
    elif "." in gt.replace("|", "/").split("/") or not gt:
        description = "Missing genotype"
    elif genotype is None or genotype.base not in ("A", "C", "G", "T"):
        description = "Ambiguous or unsupported Y genotype"
    elif gq is not None and (not math.isfinite(gq) or gq < quality):
        description = "Below genotype quality"
    call = Call(
        genotype.base if genotype else "NA",
        min(metrics) if metrics else 0,
        100.0,
        description,
        gt,
        fields[6],
        gq,
    )
    if not description and values.get("AD", ".") != ".":
        evidence = ad_call(ref, alt, values["AD"])
        call = replace(
            call,
            reads=min(call.reads, evidence.reads) if metrics else evidence.reads,
            percentage=evidence.percentage,
        )
        if evidence.base != call.base:
            call = replace(call, description="Genotype and allele depths disagree")
    elif not description and not metrics:
        call = replace(call, description="Missing depth evidence")
    return call


def _point_call(calls: list[Call]) -> Call:
    if len({call.base for call in calls}) != 1:
        return replace(calls[0], base="NA", description="Conflicting Y genotypes")
    return min(calls, key=lambda c: (not bool(c.description), c.reads, c.percentage))


def query_vcf(
    path: Path,
    *,
    positions: set[int],
    work: Path,
    reference_sequence: str | None = None,
    min_quality: int = 20,
    min_reads: int = 10,
    majority: int = 90,
) -> tuple[dict[int, Call], bool]:
    if not positions:
        raise WGSExtractError("The Y marker panel contains no positions.")
    samples = run_command(
        ["bcftools", "query", "-l", str(path)], capture_output=True
    ).stdout.splitlines()
    if len(samples) != 1:
        raise WGSExtractError("Y haplogroup analysis requires a single-sample VCF.")
    header = run_command(
        ["bcftools", "view", "-h", str(path)], capture_output=True
    ).stdout
    source = ReferenceEvidence(
        str(path),
        {name: max(len(reference_sequence or ""), max(positions)) for name in Y_NAMES},
        header_text=header,
    )
    indexed = any(Path(str(path) + suffix).is_file() for suffix in (".tbi", ".csi"))
    regions = []
    if indexed:
        contigs = vcf_index_chromosomes(path)
        if contigs:
            regions = [name for name in contigs if name in Y_NAMES]
            if not regions:
                raise WGSExtractError("Input VCF has no records on chromosome Y.")
    region_args = ["-r", ",".join(regions)] if regions else []
    base = ["bcftools", "view", *region_args, "-i", 'CHROM="Y" || CHROM="chrY"']
    processes: list[subprocess.Popen[str]] = []
    # --min-reads already describes haploid Y depth; do not halve it again.
    thresholds = Thresholds(min_reads, min_reads, min_quality, min_quality)
    marker_positions = sorted(positions)
    points: dict[tuple[str, int], list[tuple[State, Call]]] = defaultdict(list)
    pending: dict[str, deque[tuple[int, int, Call]]] = defaultdict(deque)
    active: dict[str, list[tuple[int, int, int, Call]]] = defaultdict(list)
    pending_spans: dict[str, deque[tuple[int, int]]] = defaultdict(deque)
    observed_spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
    serial = 0
    calls: dict[int, Call] = {}
    reference_positions: set[int] = set()
    with (
        (work / "vcf-errors.txt").open("w+", encoding="utf-8") as errors,
        ExitStack() as stack,
    ):
        first = stack.enter_context(
            popen(
                [*base, "-H" if indexed else "-Ou", str(path)],
                stdout=subprocess.PIPE,
                stderr=errors,
                text=True,
            )
        )
        processes.append(first)
        if not indexed:
            assert first.stdout is not None
            sorted_vcf = stack.enter_context(
                popen(
                    ["bcftools", "sort", "-T", str(work / "vcf-sort"), "-Ou"],
                    stdin=first.stdout,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    text=True,
                )
            )
            first.stdout.close()
            processes.append(sorted_vcf)
            assert sorted_vcf.stdout is not None
            reader = stack.enter_context(
                popen(
                    ["bcftools", "view", "-H"],
                    stdin=sorted_vcf.stdout,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    text=True,
                )
            )
            sorted_vcf.stdout.close()
            processes.append(reader)
        else:
            reader = first
        assert reader.stdout is not None
        output = reader.stdout

        def records() -> Iterator[list[str]]:
            for line in output:
                fields = line.rstrip("\n").split("\t")
                info = dict(part.partition("=")[::2] for part in fields[7].split(";"))
                start = int(fields[1])
                end = int(info.get("END", start + len(fields[3]) - 1))
                block = (
                    "END" in info
                    and len(fields[3]) == 1
                    and all(
                        alt in (".", "<NON_REF>", "<*>") for alt in fields[4].split(",")
                    )
                )
                call = _call(fields, block, min_quality)
                # Point AD is observed depth, but never a floor for a block.
                values = dict(
                    zip(fields[8].split(":"), fields[9].split(":"), strict=False)
                )
                if not block and values.get("DP", ".") == "." and not call.description:
                    values["DP"] = str(call.reads)
                    fields[8], fields[9] = ":".join(values), ":".join(values.values())
                state = record_state(fields, source.quality_model, thresholds, ploidy=2)
                if state is State.CALLABLE_REFERENCE and (
                    call.description
                    or call.reads < min_reads
                    or call.percentage < majority
                ):
                    fields[6] = "YleafRejected"
                    state = State.FILTERED
                if not block and start in positions:
                    points[(fields[0], start)].append((state, call))
                if block and bisect_left(marker_positions, start) < bisect_right(
                    marker_positions, end
                ):
                    pending[fields[0]].append((start, end, call))
                if bisect_left(marker_positions, start) < bisect_right(
                    marker_positions, end
                ):
                    pending_spans[fields[0]].append((start, end))
                yield fields

        stream = records()
        try:
            first_fields = next(stream, None)
            if first_fields is None:
                raise WGSExtractError("Input VCF has no records on chromosome Y.")
            contig = first_fields[0]
            source.lengths.setdefault(
                contig, max(len(reference_sequence or ""), max(positions))
            )
            from itertools import chain

            spans = source.spans(
                thresholds,
                [
                    (name, pos, pos)
                    for name in source.lengths
                    if name in Y_NAMES
                    for pos in marker_positions
                ],
                records=chain([first_fields], stream),
                ploidy=2,
            )
            for span in spans:
                first_marker = bisect_left(marker_positions, span.start)
                last_marker = bisect_right(marker_positions, span.end)
                pending_blocks, active_blocks = (
                    pending[span.contig],
                    active[span.contig],
                )
                for pos in marker_positions[first_marker:last_marker]:
                    observed = observed_spans[span.contig]
                    waiting = pending_spans[span.contig]
                    while waiting and waiting[0][0] <= pos:
                        start, end = waiting.popleft()
                        heapq.heappush(observed, (end, start))
                    while observed and observed[0][0] < pos:
                        heapq.heappop(observed)
                    while pending_blocks and pending_blocks[0][0] <= pos:
                        start, end, call = pending_blocks.popleft()
                        heapq.heappush(active_blocks, (end, start, serial, call))
                        serial += 1
                    while active_blocks and active_blocks[0][0] < pos:
                        heapq.heappop(active_blocks)
                    explicit = [
                        call
                        for state, call in points.pop((span.contig, pos), [])
                        if state is span.state
                    ]
                    if span.state is State.NOT_ASSESSED:
                        if not observed:
                            continue
                        calls[pos] = Call("NA", 0, 0, "Unassessed observed position")
                        continue
                    if (
                        span.state in (State.VARIANT, State.CALLABLE_REFERENCE)
                        and explicit
                    ):
                        call = _point_call(explicit)
                    elif span.state is State.CALLABLE_REFERENCE and active_blocks:
                        call = min(
                            (item[3] for item in active_blocks), key=lambda c: c.reads
                        )
                        if reference_sequence and pos <= len(reference_sequence):
                            call = replace(call, base=reference_sequence[pos - 1])
                        elif not any(start == pos for _, start, _, _ in active_blocks):
                            call = replace(
                                call, base="NA", description="Unassessed position"
                            )
                    else:
                        evidence = (
                            _point_call(explicit)
                            if explicit
                            else min(
                                (item[3] for item in active_blocks),
                                key=lambda c: c.reads,
                            )
                            if active_blocks
                            else Call("NA", 0, 0)
                        )
                        call = replace(
                            evidence,
                            base="NA",
                            description=evidence.description
                            or f"Reference evidence: {span.state.value}",
                        )
                    calls[pos] = (
                        _point_call([calls[pos], call]) if pos in calls else call
                    )
                    if span.state is State.CALLABLE_REFERENCE and not call.description:
                        reference_positions.add(pos)
            for _ in stream:
                pass
        finally:
            reader.stdout.close()
            codes = [process.wait() for process in reversed(processes)]
        if any(codes):
            errors.seek(0)
            raise WGSExtractError(f"Reading Y VCF failed: {errors.read()[-500:]}")
    return calls, any(
        pos in reference_positions
        and not call.description
        and call.base in ("A", "C", "G", "T")
        for pos, call in calls.items()
    )
