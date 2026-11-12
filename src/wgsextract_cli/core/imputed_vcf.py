"""Recognise a VCF that ``vcf impute`` wrote, from its header line
``##wgsextract_imputation=<Tool=...,Version="...",Panel="...",...>``."""

from __future__ import annotations

import re
from collections.abc import Iterable

HEADER_KEY = "wgsextract_imputation"
IMPUTED_TAG = "IMP"
LOW_CONFIDENCE_FILTER = "ImpLowConf"
_FIELD = re.compile(r'(\w+)=("[^"]*"|[^,>]*)')


def imputation_info(header_lines: Iterable[str]) -> dict[str, str] | None:
    """The fields of the imputation header line, or None for direct calls."""
    prefix = f"##{HEADER_KEY}=<"
    for line in header_lines:
        if line.startswith(prefix):
            body = line[len(prefix) :].rstrip().removesuffix(">")
            return {k: v.strip('"') for k, v in _FIELD.findall(body)}
    return None


def describe(info: dict[str, str]) -> str:
    """e.g. "imputed (GLIMPSE2 2.0.1, 1000 Genomes 30x ... panel)"."""
    tool = " ".join(filter(None, (info.get("Tool"), info.get("Version"))))
    panel = info.get("Panel")
    return f"imputed ({tool}{', ' + panel if panel else ''})"
