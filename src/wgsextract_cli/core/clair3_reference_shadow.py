"""Clair3 missed-deletion shadows and complete-anchor boundary queries."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from wgsextract_cli.core.reference_evidence_records import (
    _RANK,
    Classified,
    Interval,
    State,
    _info,
    _merge,
    _record_end,
    _sample,
    _sweep,
    record_state,
    reference_depth,
)
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.core.variant_confidence import Thresholds

# Clair3's gVCF counts, at a reference base, only the reads that show a base
# there (preprocess/utils.py, _cal_reference_likelihood): a read deleting it
# counts for neither its DP nor its GQ. A deletion it saw but didn't call is a
# no-call or filtered record at its anchor, then confident 0/0 blocks with the
# deleting reads missing from their depth. Bases within this span after one,
# at under this share of its depth, are low quality (HG002 ONT chr20:10-15 Mb:
# 41 truth variants on callable reference became 3, for 0.4% of its callable
# bases; #1028).
CLAIR3_DELETION_SPAN = 10
CLAIR3_DELETION_DEPTH = 0.7


def query_intervals(
    path: str,
    model: str,
    wanted: list[Interval],
    thresholds: Thresholds,
    order: Mapping[str, int],
    regions: Path,
) -> list[Interval]:
    """Include complete anchor chains; batch boundary probes for point lists."""
    margins = [2 * CLAIR3_DELETION_SPAN] * len(wanted)
    while True:
        queried = _merge(
            (
                (c, max(1, s - m), e)
                for (c, s, e), m in zip(wanted, margins, strict=True)
            ),
            order,
        )
        probes = [(c, s, s) for c, s, _ in queried if s > 1]
        if not probes:
            return queried
        regions.write_text(
            "".join(f"{c}\t{s}\t{e}\n" for c, s, e in probes),
            encoding="utf-8",
            newline="\n",
        )
        boundary = run_command(
            ["bcftools", "view", "-H", "-R", str(regions), path],
            capture_output=True,
        ).stdout
        anchors = _merge(
            (
                (fields[0], int(fields[1]), _record_end(fields))
                for line in boundary.splitlines()
                if record_state(fields := line.split("\t"), model, thresholds)
                in (State.NO_CALL, State.FILTERED)
            ),
            order,
        )
        crossing = {
            (span.contig, span.start)
            for span in _sweep(
                probes, iter((c, s, e, State.NO_CALL) for c, s, e in anchors), order
            )
            if span.state is State.NO_CALL
        }
        if not crossing:
            return queried
        for index, (contig, start, _) in enumerate(wanted):
            if (contig, max(1, start - margins[index])) in crossing:
                margins[index] *= 2


def classified_records(
    records: Iterator[list[str]],
    model: str,
    thresholds: Thresholds,
    deletion_shadow: bool,
    record_ploidy: Callable[[str, int, int], int | None] | None = None,
) -> Iterator[Classified]:
    """Each record's bases (contig, start, end) and state. With
    ``deletion_shadow`` (Clair3), reference bases directly after a no-call or
    filtered record, within CLAIR3_DELETION_SPAN of it, are low quality while
    their depth stays under CLAIR3_DELETION_DEPTH of its depth: the missing
    reads delete them, which Clair3's reference GQ doesn't see (#1028)."""
    if not deletion_shadow:
        for fields in records:
            yield (
                fields[0],
                int(fields[1]),
                _record_end(fields),
                record_state(
                    fields,
                    model,
                    thresholds,
                    record_ploidy(fields[0], int(fields[1]), _record_end(fields))
                    if record_ploidy
                    else None,
                ),
            )
        return
    references = (State.CALLABLE_REFERENCE, State.LOW_QUALITY_REFERENCE)
    pending = next(records, None)
    # Keep only overlapping records. Evaluate their tails after later anchors
    # arrive, so block splitting and overlapping calls cannot change evidence.
    active: list[tuple[int, State, int]] = []
    contig, cursor = "", 0
    anchor_end, anchor_depth, shadow = 0, 0, False
    while pending is not None or active:
        if not active and (pending is not None and pending[0] != contig):
            contig, cursor = pending[0], int(pending[1])
            anchor_end, anchor_depth, shadow = 0, 0, False
        while pending is not None and (pending[0], int(pending[1])) == (contig, cursor):
            end = _record_end(pending)
            state = record_state(
                pending,
                model,
                thresholds,
                record_ploidy(contig, cursor, end) if record_ploidy else None,
            )
            info = _info(pending[7])
            depth = (
                reference_depth(
                    info, _sample(pending), block="END" in info and state in references
                )
                or 0
            )
            active.append((end, state, depth))
            if state in (State.NO_CALL, State.FILTERED):
                if cursor <= anchor_end + 1:
                    anchor_end = max(anchor_end, end)
                    anchor_depth = max(anchor_depth, depth)
                else:
                    anchor_end, anchor_depth = end, depth
                shadow = True
            pending = next(records, None)
        stop = min((end + 1 for end, _, _ in active), default=cursor + 1)
        if pending is not None and pending[0] == contig:
            stop = min(stop, int(pending[1])) if active else int(pending[1])
        if shadow:
            boundary = (
                anchor_end
                if cursor <= anchor_end
                else anchor_end + CLAIR3_DELETION_SPAN
            )
            stop = min(stop, boundary + 1) if cursor <= boundary else stop
        if active:
            state = min((state for _, state, _ in active), key=_RANK.__getitem__)
            if shadow and cursor > anchor_end:
                depths = [d for _, s, d in active if s in references]
                if cursor <= anchor_end + CLAIR3_DELETION_SPAN and any(
                    d < CLAIR3_DELETION_DEPTH * anchor_depth for d in depths
                ):
                    state = min(
                        state, State.LOW_QUALITY_REFERENCE, key=_RANK.__getitem__
                    )
                else:
                    shadow = False
            yield contig, cursor, stop - 1, state
        elif cursor > anchor_end:
            shadow = False
        cursor = stop
        active = [record for record in active if record[0] >= cursor]
