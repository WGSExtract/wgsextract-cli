# SPDX-License-Identifier: GPL-3.0-or-later
"""Interpret upstream marker tables using independently verified SNP polarity."""

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from wgsextract_cli.core.utils import WGSExtractError

from .data import file_sha256
from .markers import Marker


def correct_polarity(
    markers: list[Marker],
    table: Path,
    audit: Path,
    tree_id: str,
    build: str,
    *,
    ancient: bool,
) -> tuple[list[Marker], dict[str, Any]]:
    """Apply only audited reversals to the exact upstream regular marker table.

    No strand guessing or reference-as-ancestral assumption is made. Recurrent
    positions are deliberately left alone, as they are excluded by prediction.
    Ancient panels are filtered after correction, excluding whole positions
    so a surviving row cannot turn a recurrent site into unique evidence.
    """
    metadata = json.loads(audit.read_text(encoding="utf-8"))
    spec = metadata["tables"][f"{tree_id}/{build}"]
    if file_sha256(table) != spec["sha256"]:
        raise WGSExtractError(
            "Y marker polarity audit does not match its source table."
        )
    alleles = {(name, pos): (anc, der) for name, pos, anc, der in spec["alleles"]}
    recurrent = Counter(marker.pos for marker in markers)
    corrected = []
    count = 0
    for marker in markers:
        pair = alleles.get((marker.name, marker.pos))
        if pair is not None:
            anc, der = pair
            if recurrent[marker.pos] != 1 or (marker.ancestral, marker.derived) != (
                der,
                anc,
            ):
                raise WGSExtractError("Invalid Y marker polarity correction.")
            marker = replace(
                marker, ancestral=anc, derived=der, mutation=f"{anc}->{der}"
            )
            count += 1
        corrected.append(marker)
    if count != len(alleles):
        raise WGSExtractError("Incomplete Y marker polarity correction.")
    if ancient:
        damaged = {
            marker.pos
            for marker in corrected
            if (marker.ancestral, marker.derived) in {("C", "T"), ("G", "A")}
        }
        corrected = [marker for marker in corrected if marker.pos not in damaged]
    return corrected, {
        "source": metadata["source"],
        "sha256": file_sha256(audit),
        "corrected": count,
        "audit": spec["audit"],
    }
