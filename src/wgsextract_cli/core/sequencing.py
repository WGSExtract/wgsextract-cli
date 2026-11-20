"""Public imports for the alignment.sequencing implementation."""

import os as os
import re as re
import subprocess as subprocess
import zlib as zlib
from functools import lru_cache as lru_cache
from pathlib import Path as Path

from wgsextract_cli.core import sample_workspace as sample_workspace
from wgsextract_cli.core.alignment.fastq_platform import (
    LONG_READ_MIN_MEDIAN as LONG_READ_MIN_MEDIAN,
)
from wgsextract_cli.core.alignment.fastq_platform import (
    FastqSampleLimit as FastqSampleLimit,
)
from wgsextract_cli.core.alignment.fastq_platform import (
    fastq_platform as fastq_platform,
)
from wgsextract_cli.core.alignment.fastq_platform import (
    fastq_sequencer as fastq_sequencer,
)
from wgsextract_cli.core.alignment.metadata import get_bam_header as get_bam_header
from wgsextract_cli.core.constants import SEQUENCERS as SEQUENCERS
from wgsextract_cli.core.paths import PathInput as PathInput
from wgsextract_cli.core.remote_input import is_url as is_url
from wgsextract_cli.core.remote_input import remote_file as remote_file
from wgsextract_cli.core.results import current_result as current_result
from wgsextract_cli.core.results import warn as warn
from wgsextract_cli.core.sample_types import is_platform as is_platform
from wgsextract_cli.core.ultima import read_groups as read_groups
from wgsextract_cli.core.utils import UsageError as UsageError
from wgsextract_cli.core.utils import WGSExtractError as WGSExtractError
from wgsextract_cli.core.utils import run_command as run_command

from .alignment.sequencing import (
    _PLATFORMS as _PLATFORMS,
)
from .alignment.sequencing import (
    _READ_GROUPS as _READ_GROUPS,
)
from .alignment.sequencing import (
    _READ_NAME as _READ_NAME,
)
from .alignment.sequencing import (
    _alignment_platforms as _alignment_platforms,
)
from .alignment.sequencing import (
    _detect_platform as _detect_platform,
)
from .alignment.sequencing import (
    _header_platforms as _header_platforms,
)
from .alignment.sequencing import (
    _input_platform as _input_platform,
)
from .alignment.sequencing import (
    _is_alignment as _is_alignment,
)
from .alignment.sequencing import (
    _platform_provenance as _platform_provenance,
)
from .alignment.sequencing import (
    input_platform as input_platform,
)
from .alignment.sequencing import (
    mixed_platforms as mixed_platforms,
)
from .alignment.sequencing import (
    mixed_platforms_message as mixed_platforms_message,
)
from .alignment.sequencing import (
    pacbio_platform as pacbio_platform,
)
from .alignment.sequencing import (
    platform_from_metadata as platform_from_metadata,
)
from .alignment.sequencing import (
    read_group_platforms as read_group_platforms,
)
from .alignment.sequencing import (
    record_platform_choice as record_platform_choice,
)
from .alignment.sequencing import (
    refuse_mixed_platforms as refuse_mixed_platforms,
)
from .alignment.sequencing import (
    warn_mixed_platforms as warn_mixed_platforms,
)
