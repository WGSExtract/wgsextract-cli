import gzip
from pathlib import Path

from scripts.build_poz_t2t import chain_blocks, lift

HG38, T2T = 57_227_415, 62_460_029


def test_lift_cuts_regions_to_chrY_blocks_and_mirrors_reverse_chains(
    tmp_path: Path,
) -> None:
    """#1170: pieces on reverse-strand chains land at mirrored T2T positions."""
    chain = tmp_path / "toy.over.chain.gz"
    chain.write_bytes(
        gzip.compress(
            (
                # 100 bp aligned, 50 bp deleted in T2T, 30 bp inserted, 100 bp.
                f"chain 1 chrY {HG38} + 1000 1250 chrY {T2T} + 5000 5230 1\n"
                "100\t50\t30\n100\n\n"
                f"chain 1 chrY {HG38} + 2000 2100 chrY {T2T} - 10 110 2\n100\n\n"
                f"chain 1 chrY {HG38} + 3000 3100 chrX 156040895 + 0 100 3\n100\n"
            ).encode()
        )
    )
    blocks = list(chain_blocks(chain))

    assert lift([(1050, 1200), (2000, 2020), (3000, 3100)], blocks, 10) == [
        (5050, 5100),
        (5130, 5180),
        (T2T - 30, T2T - 10),
    ]
    assert lift([(1050, 1200)], blocks, 30) == [(5050, 5180)]
