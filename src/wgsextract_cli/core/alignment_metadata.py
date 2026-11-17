"""Public imports for the alignment.metadata implementation."""

import logging as logging
import subprocess as subprocess
from collections.abc import Sequence as Sequence
from os import PathLike as PathLike
from os import fspath as fspath

from wgsextract_cli.core.messages import LOG_MESSAGES as LOG_MESSAGES
from wgsextract_cli.core.paths import PathInput as PathInput
from wgsextract_cli.core.ref_library.assets import header_sequences as header_sequences
from wgsextract_cli.core.ref_library.assets import read_fai_lengths as read_fai_lengths
from wgsextract_cli.core.ref_library.assets import (
    sequence_dictionary as sequence_dictionary,
)
from wgsextract_cli.core.ref_library.models import sequence_lengths as sequence_lengths

from .alignment.metadata import (
    cram_encoded_with as cram_encoded_with,
)
from .alignment.metadata import (
    get_bam_header as get_bam_header,
)
from .alignment.metadata import (
    get_vcf_build as get_vcf_build,
)
from .builds import build_from_chr1_length as build_from_chr1_length
from .builds import build_from_path as build_from_path
from .utils import run_command as run_command
