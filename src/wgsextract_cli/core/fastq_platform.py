"""Public imports for the alignment.fastq_platform implementation."""

import hashlib as hashlib
import os as os
import re as re
from functools import lru_cache as lru_cache
from statistics import median as median
from statistics import quantiles as quantiles
from typing import NamedTuple as NamedTuple

from wgsextract_cli.core.alignment.read_counts import (
    _COMPRESSED_SAMPLE_BYTES as _COMPRESSED_SAMPLE_BYTES,
)
from wgsextract_cli.core.alignment.read_counts import (
    _DECODED_SAMPLE_BYTES as _DECODED_SAMPLE_BYTES,
)
from wgsextract_cli.core.alignment.read_counts import _decoded_prefix as _decoded_prefix
from wgsextract_cli.core.constants import SEQUENCERS as SEQUENCERS

from .alignment.fastq_platform import (
    LONG_READ_MIN_MEDIAN as LONG_READ_MIN_MEDIAN,
)
from .alignment.fastq_platform import (
    FastqPlatform as FastqPlatform,
)
from .alignment.fastq_platform import (
    FastqSampleLimit as FastqSampleLimit,
)
from .alignment.fastq_platform import (
    _evidence_fingerprint as _evidence_fingerprint,
)
from .alignment.fastq_platform import (
    fastq_evidence_fingerprint as fastq_evidence_fingerprint,
)
from .alignment.fastq_platform import (
    fastq_platform as fastq_platform,
)
from .alignment.fastq_platform import (
    fastq_sequencer as fastq_sequencer,
)
