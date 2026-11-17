"""Public imports for the alignment.health implementation."""

import logging as logging
import subprocess as subprocess
from typing import Any as Any

from wgsextract_cli.core.alignment.alt_mapping import alt_unaware as alt_unaware
from wgsextract_cli.core.alignment.alt_mapping import (
    alt_unaware_message as alt_unaware_message,
)
from wgsextract_cli.core.paths import PathInput as PathInput
from wgsextract_cli.core.results import warn as warn
from wgsextract_cli.core.samtools_commands import alignment_index as alignment_index
from wgsextract_cli.core.utils import run_command as run_command

from .alignment.health import (
    LONG_READ_LENGTH as LONG_READ_LENGTH,
)
from .alignment.health import (
    MIN_SAMPLE_READS as MIN_SAMPLE_READS,
)
from .alignment.health import (
    QUALITY_SAMPLE as QUALITY_SAMPLE,
)
from .alignment.health import (
    SAMPLE_END as SAMPLE_END,
)
from .alignment.health import (
    SAMPLE_START as SAMPLE_START,
)
from .alignment.health import (
    _sample_region as _sample_region,
)
from .alignment.health import (
    alignment_health as alignment_health,
)
from .alignment.health import (
    duplicates_unmarked_message as duplicates_unmarked_message,
)
from .alignment.health import (
    warn_about_alignment as warn_about_alignment,
)
