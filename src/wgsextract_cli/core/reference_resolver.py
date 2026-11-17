"""Public imports for the ref_library.resolver implementation."""

import functools as functools
import logging as logging
import os as os
import subprocess as subprocess
from collections.abc import Iterator as Iterator
from pathlib import Path as Path
from typing import NamedTuple as NamedTuple

from wgsextract_cli.core import sample_workspace as sample_workspace
from wgsextract_cli.core.alignment.metadata import get_bam_header as get_bam_header
from wgsextract_cli.core.alignment.metadata import get_vcf_build as get_vcf_build
from wgsextract_cli.core.builds import HG37_BUILD_ALIASES as HG37_BUILD_ALIASES
from wgsextract_cli.core.builds import HG38_BUILD_ALIASES as HG38_BUILD_ALIASES
from wgsextract_cli.core.builds import build_from_chr1_length as build_from_chr1_length
from wgsextract_cli.core.builds import build_from_path as build_from_path
from wgsextract_cli.core.builds import canonical_build as canonical_build
from wgsextract_cli.core.builds import is_hg37_build as is_hg37_build
from wgsextract_cli.core.builds import is_hg38_build as is_hg38_build
from wgsextract_cli.core.constants import REF_GENOME_FILENAMES as REF_GENOME_FILENAMES
from wgsextract_cli.core.paths import PathInput as PathInput
from wgsextract_cli.core.paths import expand_path as expand_path
from wgsextract_cli.core.reference_assets import build_hint as build_hint
from wgsextract_cli.core.reference_assets import fasta_build as fasta_build
from wgsextract_cli.core.reference_assets import (
    find_fasta_by_signature as find_fasta_by_signature,
)
from wgsextract_cli.core.reference_assets import (
    find_reference_fasta as find_reference_fasta,
)
from wgsextract_cli.core.reference_assets import (
    find_reference_target_tab as find_reference_target_tab,
)
from wgsextract_cli.core.reference_assets import is_fasta_path as is_fasta_path
from wgsextract_cli.core.reference_assets import (
    library_of_genomes_dir as library_of_genomes_dir,
)
from wgsextract_cli.core.reference_assets import read_fai_lengths as read_fai_lengths
from wgsextract_cli.core.reference_assets import (
    reference_dict_path as reference_dict_path,
)
from wgsextract_cli.core.reference_assets import (
    reference_genomes_dir as reference_genomes_dir,
)
from wgsextract_cli.core.reference_assets import (
    resolve_input_reference_fasta as resolve_input_reference_fasta,
)
from wgsextract_cli.core.reference_assets import (
    resolve_input_target_tab as resolve_input_target_tab,
)
from wgsextract_cli.core.reference_models import (
    identify_reference as identify_reference,
)
from wgsextract_cli.core.reference_models import sequence_lengths as sequence_lengths

from .ref_library.resolver import (
    _UNKNOWN_SIGNATURE as _UNKNOWN_SIGNATURE,
)
from .ref_library.resolver import (
    LibraryRoots as LibraryRoots,
)
from .ref_library.resolver import (
    ReferenceLibrary as ReferenceLibrary,
)
from .ref_library.resolver import (
    _warn_missing_configured_library as _warn_missing_configured_library,
)
from .ref_library.resolver import (
    header_contig_lengths as header_contig_lengths,
)
from .ref_library.resolver import (
    library_roots as library_roots,
)
