"""Which of a VCF record's annotation entries describe which ALT allele.

VEP's CSQ, SnpEff's ANN, SpliceAI's and fastVEP's pipe-delimited INFO fields
list entries for every ALT of a record, each naming its allele. VEP spells
indel alleles without their padding base; a record split from a multi-allelic
one (``bcftools norm -m-``) may keep every original allele's entries. These
functions pick one ALT's entries for `vcf table` and `vcf acmg`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

# Ensembl VEP's test for a structural-variant ALT (<DEL>, breakends), which it
# names differently; <*> doesn't match it (Parser/VCF.pm, release 113).
_SV_ALT = re.compile(r"[<\[\]][^*]+[>\]\[]|^\.\w+|\w+\.$")
_BASES = re.compile(r"[ACGT]+")


def vep_allele(ref: str, alts: Sequence[str], alt: str) -> str:
    """How VEP names ``alt`` in CSQ, following its VCF parser: an indel record
    loses its shared padding base ('-' when nothing is left). A multi-allelic
    record is trimmed when every allele without a '*' (spanning deletions,
    ``<*>``) starts with the same base, and those '*' alleles keep theirs."""
    if not _BASES.fullmatch(ref + "".join(alts)) and _SV_ALT.search(",".join(alts)):
        return alt
    if all(len(a) == len(ref) for a in alts):
        return alt
    if len(alts) > 1:
        first_bases = {a[:1] for a in (ref, *alts) if "*" not in a}
        if len(first_bases) != 1 or "*" in alt:
            return alt
    elif ref[:1] != alt[:1]:
        return alt
    return alt[1:] or "-"


def csq_entries(raw: str | None, names: Sequence[str]) -> list[dict[str, str]]:
    if not raw:
        return []
    return [
        dict(zip(names, entry.split("|"), strict=False)) for entry in raw.split(",")
    ]


def for_allele(
    entries: list[dict[str, str]],
    allele_key: str,
    candidates: Sequence[str],
    alt_index: int,
    n_alts: int,
    *,
    split: bool = False,
) -> list[dict[str, str]]:
    """The entries describing one ALT. ``candidates`` are its possible
    spellings, most specific first: the first that matches anything wins, so
    VEP's trimmed spelling of one insertion can't also pick up another's.

    A record split from a multi-allelic one (``split``: ``vcf clinvar``/
    ``gnomad`` split them) may keep every original allele's entries, so it is
    matched by its original ALLELE_NUM (``alt_index``) or spelling, never
    given entries just because they all name one allele."""
    if not entries:
        return []
    if "ALLELE_NUM" in entries[0] and (
        n_alts > 1 or (split and {e.get("ALLELE_NUM") for e in entries} != {"1"})
    ):
        return [e for e in entries if e.get("ALLELE_NUM") == str(alt_index)]
    if n_alts == 1 and not split and len({e.get(allele_key) for e in entries}) == 1:
        return entries
    for spelling in dict.fromkeys(candidates):
        matches = [e for e in entries if e.get(allele_key) == spelling]
        if matches:
            return matches
    return []


def csq_for_allele(
    entries: list[dict[str, str]],
    ref: str,
    alts: Sequence[str],
    alt_index: int,
    origin: tuple[str, list[str], int] | None = None,
) -> list[dict[str, str]]:
    """The CSQ entries of ALT number ``alt_index`` (1-based) of a record;
    ``origin`` is :func:`split_origin` of its INFO, for a record split from a
    multi-allelic one. ``pick_csq`` of them is the variant table's choice."""
    alt = alts[alt_index - 1]
    spellings: tuple[str, ...] = (vep_allele(ref, alts, alt), alt)
    index, n_alts = alt_index, len(alts)
    split = origin is not None and n_alts == 1
    if origin is not None and split:
        o_ref, o_alts, index = origin
        o_alt = o_alts[index - 1]
        spellings = (vep_allele(o_ref, o_alts, o_alt), *spellings, o_alt)
    return for_allele(entries, "Allele", spellings, index, n_alts, split=split)


def allele_entries(
    info: Mapping[str, str],
    formats: Mapping[str, Sequence[str]],
    spellings: Sequence[str],
    tag: str,
) -> list[dict[str, str]]:
    """The entries for one ALT of a pipe-delimited INFO field that leads with
    the allele as the VCF spells it (SpliceAI's, fastVEP's FV_*). Each entry
    must name the ALT: a record split elsewhere may keep its other ALTs'."""
    raw = info.get(tag)
    names = formats.get(tag)
    if not raw or not names:
        return []
    entries = csq_entries(raw, names)
    for spelling in dict.fromkeys(spellings):
        matches = [e for e in entries if e.get("ALLELE") == spelling]
        if matches:
            return matches
    return []


def split_origin(raw: str | None) -> tuple[str, list[str], int] | None:
    """REF, ALTs and this ALT's 1-based index of the record a biallelic one
    was split from (bcftools norm ``--old-rec-tag``: CHR|POS|REF|ALT|IDX)."""
    parts = (raw or "").split("|")
    if len(parts) != 5 or not parts[4].isdigit():
        return None
    alts = parts[3].split(",")
    index = int(parts[4])
    return (parts[2], alts, index) if 1 <= index <= len(alts) else None
