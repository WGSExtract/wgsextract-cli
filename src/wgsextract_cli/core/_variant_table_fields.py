"""Scalar and allele-specific VCF fields used by the variant table."""

from __future__ import annotations

from collections.abc import Mapping

POPMAX_INFO = ("GNOMAD_POPMAX_AF", "AF_grpmax_joint", "AF_grpmax", "AF_popmax")
NHOMALT_INFO = ("GNOMAD_NHOMALT", "nhomalt_joint", "nhomalt")


def value(raw: str | None) -> str | None:
    return None if raw in (None, "", ".") else raw


def to_int(raw: str | None) -> int | None:
    raw = value(raw)
    if raw is None:
        return None
    try:
        return round(float(raw))
    except ValueError:
        return None


def to_float(raw: str | None) -> float | None:
    raw = value(raw)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def csq_frequency(csq: Mapping[str, str] | None, *keys: str) -> float | None:
    """The first of ``keys`` with a value in a CSQ entry, as a frequency. VEP
    joins the values of co-located known variants with '&'; the highest is
    taken, so an allele common under any of them never reads as rare."""
    if not csq:
        return None
    for key in keys:
        raw = csq.get(key)
        if not raw:
            continue
        values = [f for v in raw.split("&") if (f := to_float(v)) is not None]
        if values:
            return max(values)
    return None


def per_allele(raw: str | None, number: str | None, alt_index: int) -> str | None:
    """This ALT's value of a VCF field (``alt_index`` is 1-based)."""
    if raw is None:
        return None
    if number in ("A", "R"):
        values = raw.split(",")
        i = alt_index - 1 if number == "A" else alt_index
        return value(values[i]) if i < len(values) else None
    return value(raw)


def fraction(raw: str | None) -> float | None:
    parsed = to_float(raw)
    return parsed if parsed is not None and 0 <= parsed <= 1 else None
