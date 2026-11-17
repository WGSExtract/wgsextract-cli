"""Reference library helpers.

``catalog`` and ``downloads`` manage available genomes and transfers.
``ncount`` writes FASTA N-run sidecars; ``needs`` finds missing command resources.
``lookup`` scopes htslib reference discovery to local-only locations.
"""

from .catalog import get_available_genomes
from .downloads import download_file

__all__ = ["download_file", "get_available_genomes"]
