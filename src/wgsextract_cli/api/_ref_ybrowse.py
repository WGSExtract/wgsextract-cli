"""``ref ybrowse`` parameters, kept out of ``api.ref`` for its size budget."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ._params import Params, param, with_config_defaults
from ._ref_library import _InLibrary
from ._ref_resources import _delete_param
from ._shared import ref_param


@dataclass(frozen=True, kw_only=True)
class RefYbrowseParams(_InLibrary, Params):
    """Install a verified, sites-only GRCh38 yBrowse snapshot into the
    library, or delete the installed snapshots; ``grch37`` lifts it to GRCh37."""

    COMMAND = "ref ybrowse"

    delete: bool = _delete_param("yBrowse snapshots")
    snapshot: Path | None = param(
        None,
        help="Use a local GRCh38 VCF(.gz) instead of downloading from ybrowse.org.",
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

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        from wgsextract_cli.core import ybrowse_grch37
        from wgsextract_cli.core.ref_library.removal import ybrowse_paths
        from wgsextract_cli.core.ybrowse import guarded_snapshot_paths

        filled = with_config_defaults(self, only={"ref"})
        ref = filled.ref.expanduser() if filled.ref is not None else None
        if ref is not None:
            yield "ref", ref
        library = filled.library()
        yield "ref", library
        root = library / "ybrowse"
        yield "ref", root
        if self.delete:
            for path in ybrowse_paths(str(library)):
                yield "ref", path
            return
        for path in guarded_snapshot_paths(root / "hg38"):
            yield "ref", path
        if not self.grch37:
            return
        for path in guarded_snapshot_paths(root / "hg19"):
            yield "ref", path
        if self.chain is None:
            chain = ybrowse_grch37.library_chain(library)
            yield "chain", chain
            if self.download and not chain.is_file():
                for path in ybrowse_grch37.chain_staging_paths(library):
                    yield "chain", path
        for path in ybrowse_grch37.guarded_fasta_paths(
            library, ref if ref is not None and ref.is_file() else None
        ):
            yield "ref", path
