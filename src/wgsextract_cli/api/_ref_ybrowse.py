"""``ref ybrowse`` parameters, kept out of ``api.ref`` for its size budget."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._params import Params, param
from ._ref_library import _InLibrary
from ._shared import ref_param


@dataclass(frozen=True, kw_only=True)
class RefYbrowseParams(_InLibrary, Params):
    """Install a hash-pinned, sites-only GRCh38 yBrowse snapshot into the
    library, or delete the installed snapshots; ``grch37`` lifts it to GRCh37."""

    COMMAND = "ref ybrowse"

    delete: bool = param(
        False,
        help="Delete the installed yBrowse snapshots",
        action="store_true",
        deletes=True,
    )
    snapshot: Path | None = param(
        None,
        help="Use a local GRCh38 VCF(.gz), verifying its header instead of the "
        "upstream pin.",
        path="file",
        cli_type=Path,
    )
    grch37: bool = param(
        False,
        help="Lift the installed GRCh38 sites to GRCh37 with Ensembl's chain, "
        "checked against the GRCh37 FASTA --ref names or the library's.",
        action="store_true",
    )
    chain: Path | None = param(
        None, help="With --grch37, lift with this chain.", path="file", cli_type=Path
    )
    download: bool = param(
        False,
        help="With --grch37, download Ensembl's pinned chain (730 KB) if missing.",
        action="store_true",
        writes=True,
    )
    ref: Path | None = ref_param()
