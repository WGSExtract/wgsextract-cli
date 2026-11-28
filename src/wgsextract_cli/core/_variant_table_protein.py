"""Protein fields already reported by a VCF's chosen annotation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from wgsextract_cli.core.table_writer import Column

CSQ_POSITION = "Protein_position"
ANN_POSITION = "AA.pos / AA.length"
CSQ_DOMAINS = "DOMAINS"

COLUMNS = (
    Column(
        "protein_position", "INTEGER", "1-based residue start on the chosen transcript."
    ),
    Column(
        "protein_position_end",
        "INTEGER",
        "Inclusive residue end on the chosen transcript.",
    ),
    Column(
        "domains", "TEXT", "Reported VEP protein domains (source:id, '&'-separated)."
    ),
)

_ALTERING = frozenset(
    {
        "missense_variant",
        "inframe_insertion",
        "inframe_deletion",
        "conservative_inframe_insertion",
        "conservative_inframe_deletion",
        "disruptive_inframe_insertion",
        "disruptive_inframe_deletion",
        "protein_altering_variant",
        "frameshift_variant",
        "start_lost",
        "stop_gained",
        "stop_lost",
        "initiator_codon_variant",
    }
)
_POSITION = re.compile(
    r"([1-9][0-9]{0,18})(?:-([1-9][0-9]{0,18}))?(?:/[1-9][0-9]{0,18})?"
)
_DOMAIN = re.compile(r"[^:\s]+:[^\s:][^\s]*")


def annotation_fields(csq: Sequence[str], ann: Sequence[str]) -> str:
    """Which protein fields the header declares, regardless of coverage."""
    return ",".join(
        f"{source}/{name}"
        for source, name, fields in (
            ("CSQ", CSQ_POSITION, csq),
            ("ANN", ANN_POSITION, ann),
            ("CSQ", CSQ_DOMAINS, csq),
        )
        if name in fields
    )


def protein_fields(
    csq: Mapping[str, str],
    ann: Mapping[str, str],
    csq_consequence: str | None,
    ann_consequence: str | None,
) -> tuple[int | None, int | None, str | None]:
    """Keep the position and any domains on the annotation that drove the row."""
    domains = None
    if csq_consequence or not ann:
        value = csq.get(CSQ_POSITION)
        if raw := csq.get(CSQ_DOMAINS):
            domains = (
                "&".join(
                    dict.fromkeys(
                        token for token in raw.split("&") if _DOMAIN.fullmatch(token)
                    )
                )
                or None
            )
    else:
        value = ann.get(ANN_POSITION)
    if value and any(
        term in _ALTERING
        for term in (csq_consequence or ann_consequence or "").split("&")
    ):
        if match := _POSITION.fullmatch(value):
            start = int(match[1])
            end = int(match[2]) if match[2] else start
            # VEP can report insertion boundaries in reverse order. Do not
            # swap the bounds or constrain stop coordinates to protein length.
            if start <= end <= 2**63 - 1:
                return start, end, domains
    return None, None, domains
