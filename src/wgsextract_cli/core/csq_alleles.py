"""Which of a VCF record's annotation entries describe which ALT allele.

VEP's CSQ, SnpEff's ANN, SpliceAI's and fastVEP's pipe-delimited INFO fields
list entries for every ALT of a record, each naming its allele. VEP spells
indel alleles without their padding base; a record split from a multi-allelic
one (``bcftools norm -m-``) may keep every original allele's entries. These
functions pick one ALT's entries for `vcf table` and `vcf acmg`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence

from wgsextract_cli.core.vcf_alleles import allele_key

# Ensembl VEP's test for a structural-variant ALT (<DEL>, breakends), which it
# names differently; <*> doesn't match it (Parser/VCF.pm, release 113).
_SV_ALT = re.compile(r"[<\[\]][^*]+[>\]\[]|^\.\w+|\w+\.$")
_BASES = re.compile(r"[ACGTN]+")
_ANN_COMPOUND = re.compile(r"([ACGTN]+)-[^:\s]+:[0-9]+_[ACGTN]+>[ACGTN]+", re.I)


def vep_allele(ref: str, alts: Sequence[str], alt: str) -> str:
    """How VEP names ``alt`` in CSQ, following its VCF parser: an indel record
    loses its shared padding base ('-' when nothing is left). A multi-allelic
    record is trimmed when every allele without a '*' (spanning deletions,
    ``<*>``) starts with the same base, and those '*' alleles keep theirs."""
    if not _BASES.fullmatch((ref + "".join(alts)).upper()) and _SV_ALT.search(
        ",".join(alts)
    ):
        return alt
    if all(len(a) == len(ref) for a in alts):
        return alt
    if len(alts) > 1:
        first_bases = {a[:1].upper() for a in (ref, *alts) if "*" not in a}
        if len(first_bases) != 1 or "*" in alt:
            return alt
    elif ref[:1].upper() != alt[:1].upper():
        return alt
    return alt[1:] or "-"


def csq_entries(raw: str | None, names: Sequence[str]) -> list[dict[str, str]]:
    if not raw:
        return []
    return [
        dict(zip(names, entry.split("|"), strict=False)) for entry in raw.split(",")
    ]


def _ann_allele_key(allele: str) -> str:
    if "-" in allele and (compound := _ANN_COMPOUND.fullmatch(allele)):
        allele = compound[1]
    return allele_key(allele)


def ann_for_allele(
    entries: list[dict[str, str]], candidates: Sequence[str]
) -> list[dict[str, str]]:
    """Match the ALT that owns a simple or compound SnpEff annotation."""
    return _by_spelling(entries, "Allele", candidates, entry_key=_ann_allele_key)


def _by_spelling(
    entries: list[dict[str, str]],
    field: str,
    candidates: Sequence[str],
    *,
    entry_key: Callable[[str], str] = allele_key,
) -> list[dict[str, str]]:
    if not entries or not candidates:
        return []
    keys = tuple(dict.fromkeys(map(allele_key, candidates)))
    if len(keys) == 1:
        return [e for e in entries if field in e and entry_key(e[field]) == keys[0]]
    by_key: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        if field in entry:
            by_key.setdefault(entry_key(entry[field]), []).append(entry)
    for spelling in keys:
        if matches := by_key.get(spelling):
            return matches
    return []


def _minimal_allele(ref: str, alt: str) -> str | None:
    """Some producers minimize both shared ends before writing CSQ."""
    if max(len(ref), len(alt)) == 1 or not _BASES.fullmatch((ref + alt).upper()):
        return None
    ref, alt = ref.upper(), alt.upper()
    end = 0
    while end < min(len(ref), len(alt)) and ref[-end - 1] == alt[-end - 1]:
        end += 1
    if end:
        ref, alt = ref[:-end], alt[:-end]
    start = 0
    while start < min(len(ref), len(alt)) and ref[start] == alt[start]:
        start += 1
    return alt[start:] or "-"


def _sibling_spellings(
    ref: str, alts: Sequence[str], index: int
) -> tuple[set[str], set[str]]:
    spellings, minimized = set(), set()
    for i, sibling in enumerate(alts, start=1):
        if i == index:
            continue
        spellings.add(allele_key(sibling))
        spelling = allele_key(vep_allele(ref, alts, sibling))
        spellings.add(spelling)
        if (compact := _minimal_allele(ref, sibling)) is not None:
            spellings.add(compact)
            if compact != spelling:
                minimized.add(compact)
    return spellings, minimized


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
    if not entries:
        return []
    n_alts = len(alts)
    source_ref, source_alts = ref, alts
    index = alt_index
    split = origin is not None and n_alts == 1
    if origin is not None and split:
        source_ref, source_alts, index = origin
    if len(source_alts) > 1 and any(
        entry.get("ALLELE_NUM") not in (None, "", ".") for entry in entries
    ):
        # bcftools preserves CSQ verbatim, so even a lone ALLELE_NUM=1
        # belongs to the first original ALT, not to each split record.
        number = str(index)
        return [entry for entry in entries if entry.get("ALLELE_NUM") == number]

    alt = alts[alt_index - 1]
    preferred = vep_allele(ref, alts, alt)
    original_alt = source_alts[index - 1]
    spellings: tuple[str, ...] = (preferred, alt)
    siblings: set[str] = set()
    if split or len(source_alts) > 1:
        original = (
            vep_allele(source_ref, source_alts, original_alt) if split else preferred
        )
        siblings, minimized = _sibling_spellings(source_ref, source_alts, index)
        # Even the standard spelling can name a different ALT if the annotator
        # instead removed both common ends. With no allele number, don't guess.
        primary = (original,) if allele_key(original) not in minimized else ()
        fallback = (original_alt, preferred, alt) if split else (alt,)
        spellings = (
            *primary,
            *(
                spelling
                for spelling in fallback
                if allele_key(spelling) not in siblings
            ),
        )
    if matches := _by_spelling(entries, "Allele", spellings):
        return matches
    candidates = (
        (_minimal_allele(source_ref, original_alt), _minimal_allele(ref, alt))
        if split
        else (_minimal_allele(ref, alt),)
    )
    return _by_spelling(
        entries, "Allele", tuple(c for c in candidates if c and c not in siblings)
    )


def allele_entries(
    info: Mapping[str, str],
    formats: Mapping[str, Sequence[str]],
    spellings: Sequence[str],
    tag: str,
    *,
    refreshed: tuple[str, str] | None = None,
) -> list[dict[str, str]]:
    """The entries for one ALT of a pipe-delimited INFO field that leads with
    the allele as the VCF spells it (SpliceAI's, fastVEP's FV_*). Each entry
    must name the ALT: a record split elsewhere may keep its other ALTs'."""
    raw = info.get(tag)
    names = formats.get(tag)
    if not raw or not names:
        return []
    entries = csq_entries(raw, names)
    if refreshed and tag == refreshed[0]:
        spellings = (refreshed[1], *spellings)
    return _by_spelling(entries, "ALLELE", spellings)


def split_origin(raw: str | None) -> tuple[str, list[str], int] | None:
    """Original REF, ALTs and 1-based index from bcftools ``--old-rec-tag``.

    The tag is CHR|POS|REF|ALT|IDX when split, but normalization alone can
    omit IDX. Infer index 1 only when there was a single original ALT.
    """
    parts = (raw or "").split("|")
    if len(parts) not in (4, 5):
        return None
    alts = parts[3].split(",")
    if len(parts) == 4:
        return (parts[2], alts, 1) if len(alts) == 1 else None
    if not parts[4].isdigit():
        return None
    index = int(parts[4])
    return (parts[2], alts, index) if 1 <= index <= len(alts) else None
