"""Public imports for the alignment.read_counts implementation."""

import contextlib as contextlib
import logging as logging
import math as math
import stat as stat
import subprocess as subprocess
import zlib as zlib
from collections.abc import Iterator as Iterator
from pathlib import Path as Path
from typing import IO as IO
from typing import NamedTuple as NamedTuple
from typing import cast as cast

from wgsextract_cli.core.paths import PathInput as PathInput

from .alignment.read_counts import (
    _AUX_FIXED_BYTES as _AUX_FIXED_BYTES,
)
from .alignment.read_counts import (
    _CHUNK_BYTES as _CHUNK_BYTES,
)
from .alignment.read_counts import (
    _COMPRESSED_SAMPLE_BYTES as _COMPRESSED_SAMPLE_BYTES,
)
from .alignment.read_counts import (
    _DECODED_SAMPLE_BYTES as _DECODED_SAMPLE_BYTES,
)
from .alignment.read_counts import (
    AlignmentSample as AlignmentSample,
)
from .alignment.read_counts import (
    FastqStatistics as FastqStatistics,
)
from .alignment.read_counts import (
    UnpairedSample as UnpairedSample,
)
from .alignment.read_counts import (
    _bam_record_bytes as _bam_record_bytes,
)
from .alignment.read_counts import (
    _decoded_prefix as _decoded_prefix,
)
from .alignment.read_counts import (
    _sam_lines as _sam_lines,
)
from .alignment.read_counts import (
    alignment_read_length as alignment_read_length,
)
from .alignment.read_counts import (
    alignment_sample as alignment_sample,
)
from .alignment.read_counts import (
    fasta_read_length as fasta_read_length,
)
from .alignment.read_counts import (
    fastq_input_reads as fastq_input_reads,
)
from .alignment.read_counts import (
    fastq_input_statistics as fastq_input_statistics,
)
from .alignment.read_counts import (
    fastq_statistics as fastq_statistics,
)
from .alignment.read_counts import (
    unpaired_sample as unpaired_sample,
)
