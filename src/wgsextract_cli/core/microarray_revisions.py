"""Public imports for the microarray.revisions implementation."""

from __future__ import annotations

import base64 as base64
import csv as csv
import functools as functools
import gzip as gzip
import hashlib as hashlib
import heapq as heapq
import io as io
import json as json
import math as math
import re as re
import zipfile as zipfile
import zlib as zlib
from collections.abc import Iterable as Iterable
from collections.abc import Sequence as Sequence
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from pathlib import Path as Path
from typing import Any as Any
from typing import TextIO as TextIO

from wgsextract_cli.core.microarray.templates import (
    _resolve_templates_for_format as _resolve_templates_for_format,
)
from wgsextract_cli.core.microarray.templates import (
    get_template_format as get_template_format,
)
from wgsextract_cli.core.microarray.templates import template_rows as template_rows
from wgsextract_cli.core.microarray.templates import vendor_chrom as vendor_chrom
from wgsextract_cli.core.utils import WGSExtractError as WGSExtractError

from .microarray.revisions import (
    _BUILD_CUES as _BUILD_CUES,
)
from .microarray.revisions import (
    _CHIP_CUES as _CHIP_CUES,
)
from .microarray.revisions import (
    _COLUMN_LABELS as _COLUMN_LABELS,
)
from .microarray.revisions import (
    _DATE_CUES as _DATE_CUES,
)
from .microarray.revisions import (
    _EXACT_MARGIN as _EXACT_MARGIN,
)
from .microarray.revisions import (
    _FORMAT as _FORMAT,
)
from .microarray.revisions import (
    _HEADER_LIMIT as _HEADER_LIMIT,
)
from .microarray.revisions import (
    _KINDS as _KINDS,
)
from .microarray.revisions import (
    _MONTHS as _MONTHS,
)
from .microarray.revisions import (
    _RELATIONS as _RELATIONS,
)
from .microarray.revisions import (
    _VENDOR_CUES as _VENDOR_CUES,
)
from .microarray.revisions import (
    CATALOGUE as CATALOGUE,
)
from .microarray.revisions import (
    SKETCH_SIZE as SKETCH_SIZE,
)
from .microarray.revisions import (
    Candidate as Candidate,
)
from .microarray.revisions import (
    Detection as Detection,
)
from .microarray.revisions import (
    Fingerprint as Fingerprint,
)
from .microarray.revisions import (
    RawExport as RawExport,
)
from .microarray.revisions import (
    Revision as Revision,
)
from .microarray.revisions import (
    _first as _first,
)
from .microarray.revisions import (
    _hash64 as _hash64,
)
from .microarray.revisions import (
    _header_month as _header_month,
)
from .microarray.revisions import (
    _margin as _margin,
)
from .microarray.revisions import (
    _multiset_sha256 as _multiset_sha256,
)
from .microarray.revisions import (
    _rank as _rank,
)
from .microarray.revisions import (
    _revision as _revision,
)
from .microarray.revisions import (
    bottom_k as bottom_k,
)
from .microarray.revisions import (
    decode_sketch as decode_sketch,
)
from .microarray.revisions import (
    decode_text as decode_text,
)
from .microarray.revisions import (
    detect as detect,
)
from .microarray.revisions import (
    encode_sketch as encode_sketch,
)
from .microarray.revisions import (
    estimate_jaccard as estimate_jaccard,
)
from .microarray.revisions import (
    fingerprint as fingerprint,
)
from .microarray.revisions import (
    load_catalogue as load_catalogue,
)
from .microarray.revisions import (
    parse_raw_export as parse_raw_export,
)
from .microarray.revisions import (
    read_raw_bytes as read_raw_bytes,
)
from .microarray.revisions import (
    read_raw_export as read_raw_export,
)
from .microarray.revisions import (
    template_ids as template_ids,
)
