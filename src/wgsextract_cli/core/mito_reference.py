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


def rotated_mito(sequence: str) -> str | None:
    """Which non-rCRS origin ``sequence`` has, or None when it has the rCRS's
    (or isn't recognisably the rCRS at all, like a fake genome's)."""
    if sequence_md5(sequence) == CHM13_CHRM_MD5:
        return CHM13_CHRM
    start = "".join(sequence.split()).upper().find(RCRS_ORIGIN)
    return f"an rCRS copy whose position 1 is at {start + 1}" if start > 0 else None


@cache
def pinned_sequences() -> tuple[str, str]:
    """The rCRS and CHM13v2.0's chrM, each from its own origin, out of the
    pinned alignment the microarray CHM13 path bundles."""
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
                    records[-1].append(line.strip().replace("-", ""))
        rcrs, chm13 = ("".join(rows).upper() for rows in records)
        # The alignment starts CHM13's chrM at the rCRS origin; undo that.
        cut = len(chm13) - int(bridge["target_rotation_offset0"])
        chm13 = chm13[cut:] + chm13[:cut]
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise WGSExtractError(f"Bundled mitochondrial sequences: {error}") from error
    if (sequence_md5(rcrs), sequence_md5(chm13)) != (RCRS_MD5, CHM13_CHRM_MD5):
        raise WGSExtractError("Bundled mitochondrial sequences fail their MD5 check.")
    return rcrs, chm13


def refs_from_chm13(records: Iterable[tuple[int, str]]) -> bool:
    """Whether VCF REF alleles, as (1-based position, REF), were read from
    CHM13's chrM rather than the rCRS: all agree with it, some not with rCRS."""
    rcrs, chm13 = pinned_sequences()
    rows = [(pos - 1, ref.upper()) for pos, ref in records if pos >= 1 and ref]

    def agree(sequence: str) -> bool:
        return all(sequence[i : i + len(ref)] == ref for i, ref in rows)

    return bool(rows) and agree(chm13) and not agree(rcrs)
