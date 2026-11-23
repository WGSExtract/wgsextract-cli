"""Preserve the original AD denominator when splitting a multi-ALT call."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator

AD_TOTAL = "WGSE_AD_TOTAL"
_AD_TOTAL_HEADER = (
    f"##FORMAT=<ID={AD_TOTAL},Number=1,Type=String,"
    'Description="Sum of the original allele depths before splitting">\n'
)


def count(raw: str | None) -> int | None:
    try:
        value = int(raw) if raw is not None else -1
    except ValueError:
        return None
    return value if value >= 0 else None


def allele_depths(raw: str | None) -> list[int]:
    """The complete vector of nonnegative AD counts, or an empty list."""
    if not raw or raw == ".":
        return []
    try:
        values = [int(value) for value in raw.split(",")]
    except ValueError:
        return []
    return values if all(value >= 0 for value in values) else []


def ad_total_header(formats: Collection[str]) -> str:
    if "AD" in formats and AD_TOTAL not in formats:
        return _AD_TOTAL_HEADER
    return ""


def preserve_ad_total(fields: list[str]) -> None:
    """Keep only the denominator; leave AD itself for bcftools to remap."""
    if len(fields) < 10 or "," not in fields[4]:
        return
    keys = fields[8].split(":")
    if "AD" not in keys or AD_TOTAL in keys:
        return
    ad_index = keys.index("AD")
    allele_count = fields[4].count(",") + 2
    fields[8] += ":" + AD_TOTAL
    for index in range(9, len(fields)):
        values = fields[index].split(":")
        depths = allele_depths(values[ad_index]) if ad_index < len(values) else []
        total = str(sum(depths)) if len(depths) == allele_count else "."
        values.extend(["."] * (len(keys) - len(values)))
        fields[index] = ":".join((*values, total))


def with_ad_totals(
    header: list[str], records: Iterable[str], extra_header: str
) -> Iterator[str]:
    """Yield VCF text with original depth totals on multi-allelic records."""
    yield from header[:-1]
    if extra_header:
        yield extra_header
    yield header[-1]
    for line in records:
        prefix = line.split("\t", 5)
        if len(prefix) < 6 or "," not in prefix[4]:
            yield line
            continue
        fields = line.rstrip("\r\n").split("\t")
        preserve_ad_total(fields)
        yield "\t".join(fields) + "\n"
