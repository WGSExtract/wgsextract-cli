"""Public imports for the ref_library.assets implementation."""

import json as json
import os as os
import re as re
from collections.abc import Iterable as Iterable
from fnmatch import fnmatch as fnmatch
from os import fspath as fspath
from pathlib import Path as Path
from typing import Any as Any

from wgsextract_cli.core.builds import BUILD_PATH_TOKENS as BUILD_PATH_TOKENS
from wgsextract_cli.core.builds import build_from_chr1_length as build_from_chr1_length
from wgsextract_cli.core.builds import build_from_path as build_from_path
from wgsextract_cli.core.builds import canonical_build as canonical_build
from wgsextract_cli.core.paths import PathInput as PathInput
from wgsextract_cli.core.paths import absolute_path as absolute_path
from wgsextract_cli.core.paths import append_suffix as append_suffix
from wgsextract_cli.core.paths import expand_path as expand_path

from .ref_library.assets import (
    FASTA_EXTENSIONS as FASTA_EXTENSIONS,
)
from .ref_library.assets import (
    FASTA_SUFFIX_RE as FASTA_SUFFIX_RE,
)
from .ref_library.assets import (
    GENOME_SIDECARS as GENOME_SIDECARS,
)
from .ref_library.assets import (
    TARGET_PATTERNS as TARGET_PATTERNS,
)
from .ref_library.assets import (
    _fasta_description as _fasta_description,
)
from .ref_library.assets import (
    _path_key as _path_key,
)
from .ref_library.assets import (
    annotation_vcf_names as annotation_vcf_names,
)
from .ref_library.assets import (
    build_hint as build_hint,
)
from .ref_library.assets import (
    default_reference_library as default_reference_library,
)
from .ref_library.assets import (
    fasta_build as fasta_build,
)
from .ref_library.assets import (
    fasta_prefix as fasta_prefix,
)
from .ref_library.assets import (
    fasta_signature as fasta_signature,
)
from .ref_library.assets import (
    find_annotation_resource as find_annotation_resource,
)
from .ref_library.assets import (
    find_annotation_vcf as find_annotation_vcf,
)
from .ref_library.assets import (
    find_fasta_by_signature as find_fasta_by_signature,
)
from .ref_library.assets import (
    find_reference_fasta as find_reference_fasta,
)
from .ref_library.assets import (
    find_reference_target_tab as find_reference_target_tab,
)
from .ref_library.assets import (
    first_existing_file as first_existing_file,
)
from .ref_library.assets import (
    first_existing_named_file as first_existing_named_file,
)
from .ref_library.assets import (
    first_matching_file as first_matching_file,
)
from .ref_library.assets import (
    header_sequences as header_sequences,
)
from .ref_library.assets import (
    input_stem as input_stem,
)
from .ref_library.assets import (
    is_fasta_path as is_fasta_path,
)
from .ref_library.assets import (
    library_of_genomes_dir as library_of_genomes_dir,
)
from .ref_library.assets import (
    n_count_paths as n_count_paths,
)
from .ref_library.assets import (
    read_fai_lengths as read_fai_lengths,
)
from .ref_library.assets import (
    reference_dict_path as reference_dict_path,
)
from .ref_library.assets import (
    reference_fasta_candidates as reference_fasta_candidates,
)
from .ref_library.assets import (
    reference_genomes_dir as reference_genomes_dir,
)
from .ref_library.assets import (
    reference_library_of as reference_library_of,
)
from .ref_library.assets import (
    resolve_input_reference_fasta as resolve_input_reference_fasta,
)
from .ref_library.assets import (
    resolve_input_target_tab as resolve_input_target_tab,
)
from .ref_library.assets import (
    resolve_reference_library as resolve_reference_library,
)
from .ref_library.assets import (
    select_reference_fasta as select_reference_fasta,
)
from .ref_library.assets import (
    sequence_dictionary as sequence_dictionary,
)
from .ref_library.assets import (
    sequence_signature as sequence_signature,
)
from .ref_library.assets import (
    verify_sequence_signature as verify_sequence_signature,
)
