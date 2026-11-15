"""Tell an rCRS mitochondrial sequence from one whose positions aren't rCRS's.

CHM13v2.0's chrM is as long as the rCRS (16,569 bp) but starts 576 bp further
round the circle and differs from it at three sites, so its length can't tell
them apart: its MD5 or its sequence can.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable
from functools import cache
from pathlib import Path

from wgsextract_cli.core.utils import WGSExtractError

RCRS_MD5 = "c68f52674c9fb33aef52dcf399755519"
CHM13_CHRM_MD5 = "ec493a132ac4823aa696e37109f64972"
CHM13_CHRM = "CHM13v2.0's chrM"
# The rCRS's first 40 bases: a rotated copy has them further along.
RCRS_ORIGIN = "GATCACAGGTCTATCACCCTATTAACCACTCACGGGAGCT"
_ASSETS = Path(__file__).resolve().parents[1] / "assets/microarray/chm13/v1"


def sequence_md5(sequence: str) -> str:
    """The MD5 a sequence dictionary's M5 tag holds: of the uppercase bases."""
    return hashlib.md5("".join(sequence.split()).upper().encode()).hexdigest()


def mito_origin(sequence: str) -> str | None:
    """ "rCRS" when ``sequence`` starts at the rCRS origin, a description of
    the other origin when it is a rotated rCRS, or None when it isn't
    recognisably the rCRS at all (like a fake genome's)."""
    if sequence_md5(sequence) == CHM13_CHRM_MD5:
        return CHM13_CHRM
    bases = "".join(sequence.split()).upper()
    # The circle may split the origin across the sequence's end and start.
    start = (bases + bases[: len(RCRS_ORIGIN) - 1]).find(RCRS_ORIGIN)
    if start < 0:
        return None
    return f"an rCRS copy whose position 1 is at {start + 1}" if start else "rCRS"


@cache
def _alignment() -> tuple[str, str, int]:
    """The pinned rCRS and CHM13 alignment rows (gaps as ``-``, both from the
    rCRS origin) and how far round CHM13's own origin is (0-based)."""
    try:
        bridge = json.loads((_ASSETS / "manifest.json").read_text("utf-8"))[
            "provenance"
        ]["mitochondria"]
        records: list[list[str]] = []
        with gzip.open(_ASSETS / bridge["alignment_file"], "rt") as fasta:
            for line in fasta:
                if line.startswith(">"):
                    records.append([])
                elif records:
                    records[-1].append(line.strip())
        rcrs, chm13 = ("".join(rows).upper() for rows in records)
        offset = int(bridge["target_rotation_offset0"])
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise WGSExtractError(f"Bundled mitochondrial sequences: {error}") from error
    ungapped = chm13.replace("-", "")
    cut = len(ungapped) - offset  # the alignment starts CHM13 at the rCRS origin
    md5s = (
        sequence_md5(rcrs.replace("-", "")),
        sequence_md5(ungapped[cut:] + ungapped[:cut]),
    )
    if len(rcrs) != len(chm13) or md5s != (RCRS_MD5, CHM13_CHRM_MD5):
        raise WGSExtractError("Bundled mitochondrial sequences fail their MD5 check.")
    return rcrs, chm13, offset


@cache
def pinned_sequences() -> tuple[str, str]:
    """The rCRS and CHM13v2.0's chrM, each from its own origin, out of the
    pinned alignment the microarray CHM13 path bundles."""
    rcrs, chm13, offset = _alignment()
    chm13 = chm13.replace("-", "")
    cut = len(chm13) - offset
    return rcrs.replace("-", ""), chm13[cut:] + chm13[:cut]


@cache
def _columns() -> tuple[tuple[int | None, str, int | None, str], ...]:
    """Each alignment column as (rCRS position, rCRS base, CHM13 position,
    CHM13 base), 1-based in each sequence's own coordinates; a gap is
    (None, "")."""
    rcrs, chm13, offset = _alignment()
    length = len(chm13.replace("-", ""))
    columns = []
    p = q = 0
    for r, c in zip(rcrs, chm13, strict=True):
        p += r != "-"
        q += c != "-"
        columns.append(
            (
                p if r != "-" else None,
                r.strip("-"),
                (q - 1 + offset) % length + 1 if c != "-" else None,
                c.strip("-"),
            )
        )
    return tuple(columns)


def chm13_to_rcrs() -> dict[int, tuple[int, str, str]]:
    """CHM13 chrM position -> (rCRS position, rCRS base, CHM13 base), for the
    positions both sequences have."""
    return {q: (p, r, c) for p, r, q, c in _columns() if p and q}


def rcrs_variants(sample: dict[int, str]) -> list[tuple[int, str, str]]:
    """The rCRS variants of a sample known at CHM13 chrM positions.

    ``sample`` maps each called CHM13 position to the bases the sample has
    there ("" where it lacks it, more than one base where it has an insertion
    after it); positions without a call are unknown and, as in a variants-only
    VCF, read as the rCRS. Where CHM13 differs from the rCRS (the C after 310,
    10208 C, no base at 3107) only the sample's bases count, so a sample
    matching CHM13 at 10208 carries 10208C. Returns (position, REF, ALT) on the
    rCRS: SNPs, and indels anchored on the base before them, which still need
    left-aligning against the rCRS (``bcftools norm``), as bcftools calls them
    on an rCRS chrM.
    """
    rcrs = pinned_sequences()[0]
    alleles: dict[int, str] = {}
    last = 0
    for p, _r, q, _c in _columns():
        bases = sample.get(q) if q else None
        if bases and bases.strip("ACGT"):  # an N or ambiguity code: unknown
            bases = None
        if p:
            last = p
            if bases is not None and q:
                alleles[p] = bases
        elif bases and last in alleles:  # CHM13's extra C, kept by the sample
            alleles[last] += bases
    variants: list[tuple[int, str, str]] = []
    for p, bases in sorted(alleles.items()):
        ref = rcrs[p - 1]
        if bases == ref or ref == "N":
            continue
        if bases:  # a SNP, or an insertion after p
            variants.append((p, ref, bases))
        elif variants and variants[-1][2] == "" and _ends_at(variants[-1], p - 1):
            start, run, _ = variants[-1]
            variants[-1] = (start, run + ref, "")
        else:
            variants.append((p, ref, ""))
    # Anchor each deletion on the rCRS base before it (after it at 1), as VCF
    # needs.
    return [
        (p, ref, alt)
        if alt
        else (p - 1, rcrs[p - 2] + ref, rcrs[p - 2])
        if p > 1
        else (p, ref + rcrs[len(ref)], rcrs[len(ref)])
        for p, ref, alt in variants
    ]


def _ends_at(variant: tuple[int, str, str], position: int) -> bool:
    return variant[0] + len(variant[1]) - 1 == position


def ref_agreement(records: Iterable[tuple[int, str]]) -> tuple[bool, bool]:
    """Whether VCF REF alleles, as (1-based position, REF), all agree with the
    rCRS, and whether they all agree with CHM13v2.0's chrM."""
    rcrs, chm13 = pinned_sequences()
    rows = [(pos - 1, ref.upper()) for pos, ref in records if pos >= 1 and ref]

    def agree(sequence: str) -> bool:
        return all(sequence[i : i + len(ref)] == ref for i, ref in rows)

    return agree(rcrs), agree(chm13)
