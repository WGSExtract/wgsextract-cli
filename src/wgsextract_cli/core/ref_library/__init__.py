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

from .catalog import get_available_genomes
from .downloads import download_file

__all__ = ["download_file", "get_available_genomes"]
