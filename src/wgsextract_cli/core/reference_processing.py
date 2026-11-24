"""Public imports for the ref_library.processing implementation."""

from __future__ import annotations

import filecmp as filecmp
import logging as logging
import os as os
import shutil as shutil
import subprocess as subprocess
import tarfile as tarfile
import tempfile as tempfile
from collections.abc import Callable as Callable
from collections.abc import Mapping as Mapping
from pathlib import Path as Path
from pathlib import PurePosixPath as PurePosixPath

from wgsextract_cli.core.bgzf import ensure_bgzf as ensure_bgzf
from wgsextract_cli.core.dependency_checks import (
    verify_dependencies as verify_dependencies,
)
from wgsextract_cli.core.ref_library.assets import GENOME_SIDECARS as GENOME_SIDECARS
from wgsextract_cli.core.ref_library.assets import n_count_paths as n_count_paths
from wgsextract_cli.core.ref_library.assets import read_fai_lengths as read_fai_lengths
from wgsextract_cli.core.ref_library.assets import (
    reference_dict_path as reference_dict_path,
)
from wgsextract_cli.core.ref_library.assets import (
    reference_genomes_dir as reference_genomes_dir,
)
from wgsextract_cli.core.ref_library.assets import (
    verify_sequence_signature as verify_sequence_signature,
)
from wgsextract_cli.core.utils import WGSExtractError as WGSExtractError
from wgsextract_cli.core.utils import popen as popen
from wgsextract_cli.core.utils import run_command as run_command
from wgsextract_cli.core.utils import wait_with_cancel as wait_with_cancel

from .annotation_resources import GNOMAD_URLS as GNOMAD_URLS
from .annotation_resources import CancelEvent as CancelEvent
from .annotation_resources import ProgressCallback as ProgressCallback
from .ref_library.catalog import get_genome_status as get_genome_status
from .ref_library.downloads import download_file as download_file
from .ref_library.downloads import verify_download_sha256 as verify_download_sha256
from .ref_library.processing import (
    _LEGACY_BOOTSTRAP_SENTINELS as _LEGACY_BOOTSTRAP_SENTINELS,
)
from .ref_library.processing import (
    _OLD_PLOIDY_TEMPLATES as _OLD_PLOIDY_TEMPLATES,
)
from .ref_library.processing import (
    _PLOIDY_TEMPLATES as _PLOIDY_TEMPLATES,
)
from .ref_library.processing import (
    BOOTSTRAP_MARKER_PREFIX as BOOTSTRAP_MARKER_PREFIX,
)
from .ref_library.processing import (
    _download_bootstrap_archive as _download_bootstrap_archive,
)
from .ref_library.processing import (
    _merge_directory as _merge_directory,
)
from .ref_library.processing import (
    _ploidy_content as _ploidy_content,
)
from .ref_library.processing import (
    _ploidy_template as _ploidy_template,
)
from .ref_library.processing import (
    _remove_empty_directories as _remove_empty_directories,
)
from .ref_library.processing import (
    _remove_genome_files as _remove_genome_files,
)
from .ref_library.processing import (
    _remove_macos_metadata as _remove_macos_metadata,
)
from .ref_library.processing import (
    _remove_path as _remove_path,
)
from .ref_library.processing import (
    _run_reference_tool as _run_reference_tool,
)
from .ref_library.processing import (
    bootstrap_has_support_assets as bootstrap_has_support_assets,
)
from .ref_library.processing import (
    bootstrap_is_current as bootstrap_is_current,
)
from .ref_library.processing import (
    download_and_process_genome as download_and_process_genome,
)
from .ref_library.processing import (
    download_bootstrap as download_bootstrap,
)
from .ref_library.processing import (
    download_gnomad as download_gnomad,
)
from .ref_library.processing import (
    extract_archive_keep_existing as extract_archive_keep_existing,
)
from .ref_library.processing import (
    has_ref_ns as has_ref_ns,
)
from .ref_library.processing import (
    install_bootstrap_support_files as install_bootstrap_support_files,
)
from .ref_library.processing import (
    install_ploidy_files as install_ploidy_files,
)
from .ref_library.processing import (
    normalize_bootstrap_layout as normalize_bootstrap_layout,
)
from .ref_library.processing import (
    process_reference_file as process_reference_file,
)
