"""Reference library helpers.

``catalog`` and ``downloads`` manage available genomes and transfers.
``ncount`` writes FASTA N-run sidecars; ``needs`` finds missing command resources.
``lookup`` scopes htslib reference discovery to local-only locations.
``models`` identifies assemblies from headers; ``assets`` discovers local
FASTA/annotation resources; ``processing`` installs and indexes reference data;
``resolver`` combines library roots, signatures and build-specific resources.
Retained ``core.reference_processing`` and ``core.reference_resolver``
interfaces re-export the same objects.
"""

from typing import TYPE_CHECKING

__all__ = ["download_file", "get_available_genomes"]
_SUBMODULES = {"catalog", "downloads"}

if TYPE_CHECKING:
    from . import catalog as catalog
    from . import downloads as downloads
    from .catalog import get_available_genomes
    from .downloads import download_file
else:

    def __getattr__(name: str) -> object:
        if name == "get_available_genomes":
            from .catalog import get_available_genomes

            return get_available_genomes
        if name == "download_file":
            from .downloads import download_file

            return download_file
        if name == "catalog":
            import wgsextract_cli.core.ref_library.catalog as catalog

            return catalog
        if name == "downloads":
            import wgsextract_cli.core.ref_library.downloads as downloads

            return downloads
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __dir__() -> list[str]:
        return sorted(globals().keys() | set(__all__) | _SUBMODULES)
