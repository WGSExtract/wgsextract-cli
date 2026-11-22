"""Place GRCh38 chrY sites on another build with a liftOver chain.

Shared by ``ref ybrowse --grch37`` and scripts/build_yfull_tree.py: a site is
placed only when the chain maps it exactly once, onto Y; a reverse-strand
placement complements its alleles. Whether the placed alleles agree with the
target reference is the caller's check."""

from pathlib import Path
from typing import NamedTuple

from pyliftover import LiftOver

from wgsextract_cli.core.utils import WGSExtractError

COMPLEMENT = str.maketrans("ACGTacgt", "TGCAtgca")
_Y_NAMES = frozenset({"chrY", "Y"})


class YPlacement(NamedTuple):
    position: int  # 1-based
    reverse: bool


class YLiftOver:
    """A chain's placements of 1-based chrY positions."""

    def __init__(self, chain: str | Path) -> None:
        try:
            self._lifter = LiftOver(str(chain))
        except (OSError, ValueError, EOFError) as exc:
            raise WGSExtractError(f"Could not read the chain {chain}: {exc}") from exc
        # pyliftover answers chrY for a chain naming it Y, and the reverse.
        if self._lifter.convert_coordinate("chrY", 0) is None:
            raise WGSExtractError(f"The chain {chain} has no chrY/Y source blocks.")

    def place(self, position: int) -> YPlacement | str:
        """Where ``position`` lands, else why it doesn't: ``"unlifted"`` (no
        block covers it) or ``"multi_hit_or_off_Y"``."""
        hits = {
            tuple(hit[:3])
            for hit in self._lifter.convert_coordinate("chrY", position - 1) or ()
        }
        if not hits:
            return "unlifted"
        if len(hits) != 1:
            return "multi_hit_or_off_Y"
        contig, target, strand = hits.pop()
        if contig not in _Y_NAMES:
            return "multi_hit_or_off_Y"
        return YPlacement(int(target) + 1, strand == "-")


def complement(alleles: str) -> str:
    return alleles.translate(COMPLEMENT)
