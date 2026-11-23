"""The chip template formats and the --formats IDs that name them.

The original format table is ported from WGS Extract v4 ``program/aconv.py``
(Copyright (C) 2018-2020 City Farmer, (C) 2020-2022 Randy Harr, GNU GPL v3 or
later), extended by WGS Extract CLI contributors from 2026-03-02; see NOTICE.
"""

from typing import TypedDict

from wgsextract_cli.core.utils import UsageError


class _TemplateFormatOptions(TypedDict, total=False):
    # The template's positions are on this build (NCBI36 or GRCh38), not
    # GRCh37: lifted to GRCh37 to find their calls, and written as they are.
    build: str


class TemplateFormat(_TemplateFormatOptions):
    suffix: str
    parts: int


# Ported from WGS Extract v4 program/aconv.py, then extended
_TEMPLATE_FORMATS: dict[str, TemplateFormat] = {
    # v1 and v2 chips share one marker list (see assets/microarray).
    "23andMe_V2": {"suffix": ".txt", "parts": 1},
    "23andMe_V3": {"suffix": ".txt", "parts": 1},
    "23andMe_V4": {"suffix": ".txt", "parts": 2},
    "23andMe_V5": {"suffix": ".txt", "parts": 2},
    "23andMe_V5_2025": {"suffix": ".txt", "parts": 1},
    "Ancestry_V1": {"suffix": ".txt", "parts": 4},
    "Ancestry_V2": {"suffix": ".txt", "parts": 5},
    "Ancestry_V2_2021": {"suffix": ".txt", "parts": 1},
    "Ancestry_V2_2024": {"suffix": ".txt", "parts": 1},
    "Ancestry_V2ab": {"suffix": ".txt", "parts": 1},
    "Ancestry_V2cd": {"suffix": ".txt", "parts": 1},
    "Ancestry_V2e": {"suffix": ".txt", "parts": 1},
    "FTDNA_V1_Affy": {"suffix": ".csv", "parts": 1, "build": "NCBI36"},
    "FTDNA_V2": {"suffix": ".csv", "parts": 1},
    "FTDNA_V3": {"suffix": ".csv", "parts": 3},
    "FTDNA_V4": {"suffix": ".csv", "parts": 1},
    # FTDNA delivers its Y-chromosome files separately, on GRCh38.
    "FTDNA_V2Y": {"suffix": ".csv", "parts": 1, "build": "GRCh38"},
    "FTDNA_V3Y": {"suffix": ".csv", "parts": 1, "build": "GRCh38"},
    "FTDNA_V4Y": {"suffix": ".csv", "parts": 1, "build": "GRCh38"},
    "MyHeritage_V1": {"suffix": ".csv", "parts": 1},
    "MyHeritage_V2": {"suffix": ".csv", "parts": 1},
    "MyHeritage_GSA_2025": {"suffix": ".csv", "parts": 1},
    "MyHeritage_GSA_2025_609424": {"suffix": ".csv", "parts": 1},
    "MyHeritage_V3": {"suffix": ".csv", "parts": 1},
    "23andMe_SNPs_API": {"suffix": ".txt", "parts": 1},
    "23andMe_V35": {"suffix": ".txt", "parts": 1},
    "LDNA_V1": {"suffix": ".txt", "parts": 1},
    "LDNA_V2": {"suffix": ".txt", "parts": 1},
    "Nucleus_V1": {"suffix": ".txt", "parts": 1},
    "TellMeGen_V1": {"suffix": ".csv", "parts": 1},
    "Genera_V1": {"suffix": ".csv", "parts": 1},
    "MTHFR_V1": {"suffix": ".txt", "parts": 1},
    "meuDNA_V1": {"suffix": ".csv", "parts": 1},
    "meuDNA_V2": {"suffix": ".csv", "parts": 1},
    "SelfDecode_V1": {"suffix": ".txt", "parts": 1},
}

FORMAT_ALIASES = {
    # 23andMe's v1 and v2 chips export the same marker list.
    "23andme_v1": "23andMe_V2",
    "23andme_v2": "23andMe_V2",
    "23andme_v3": "23andMe_V3",
    "23andme_v4": "23andMe_V4",
    "23andme_v5": "23andMe_V5",
    "23andme_v5_2025": "23andMe_V5_2025",
    "23andme_v3+v5": "23andMe_V35",
    "23andme_v3_v5": "23andMe_V35",
    "23andme_v35": "23andMe_V35",
    "23andme_api": "23andMe_SNPs_API",
    "ancestry_v1": "Ancestry_V1",
    "ancestry_v2": "Ancestry_V2",
    "ancestry_v2_2021": "Ancestry_V2_2021",
    "ancestry_v2_2024": "Ancestry_V2_2024",
    # Letter-named variants (h600.org's "Microarray File Formats"); similar
    # minor variants are merged into one template, e.g. v2a and v2b.
    "ancestry_v2ab": "Ancestry_V2ab",
    "ancestry_v2cd": "Ancestry_V2cd",
    "ancestry_v2e": "Ancestry_V2e",
    "ftdna_v1": "FTDNA_V1_Affy",
    "ftdna_v1_affy": "FTDNA_V1_Affy",
    "familytreedna_v1": "FTDNA_V1_Affy",
    "ftdna_v2": "FTDNA_V2",
    "ftdna_v3": "FTDNA_V3",
    "familytreedna_v2": "FTDNA_V2",
    "familytreedna_v3": "FTDNA_V3",
    "ftdna_v4": "FTDNA_V4",
    "familytreedna_v4": "FTDNA_V4",
    # FTDNA's separate Y-chromosome files (GRCh38).
    "ftdna_v2y": "FTDNA_V2Y",
    "ftdna_v3y": "FTDNA_V3Y",
    "ftdna_v4y": "FTDNA_V4Y",
    "ldna_v1": "LDNA_V1",
    "ldna_v2": "LDNA_V2",
    "livingdna_v1": "LDNA_V1",
    "livingdna_v2": "LDNA_V2",
    "myheritage_v1": "MyHeritage_V1",
    "myheritage_v2": "MyHeritage_V2",
    "myheritage_gsa_2025": "MyHeritage_GSA_2025",
    "myheritage_gsa_2025_609424": "MyHeritage_GSA_2025_609424",
    "myheritage_v3": "MyHeritage_V3",
    "nucleus": "Nucleus_V1",
    "nucleus_v1": "Nucleus_V1",
    "tellmegen": "TellMeGen_V1",
    "tellmegen_v1": "TellMeGen_V1",
    "genera": "Genera_V1",
    "genera_v1": "Genera_V1",
    "mthfr": "MTHFR_V1",
    "mthfr_v1": "MTHFR_V1",
    "mthfr_genetics": "MTHFR_V1",
    "meudna_v1": "meuDNA_V1",
    "meudna_v2": "meuDNA_V2",
    "selfdecode": "SelfDecode_V1",
    "selfdecode_v1": "SelfDecode_V1",
}


COMBINED_V3_FORMAT = "CombinedKit_v3"
"""Canonical format name for the classic 14-template WGS Extract CombinedKit v3."""

# The 14 classic WGS Extract v4 templates that make up CombinedKit v3 (in
# precedence order for a site's marker ID). CombinedKit v5 (all) expands this
# set with the newer vendor layouts.
COMBINED_V3_TEMPLATES: tuple[str, ...] = (
    "23andMe_V3",
    "23andMe_V35",
    "23andMe_V4",
    "23andMe_V5",
    "23andMe_SNPs_API",
    "Ancestry_V1",
    "Ancestry_V2",
    "FTDNA_V2",
    "FTDNA_V3",
    "LDNA_V1",
    "LDNA_V2",
    "MyHeritage_V1",
    "MyHeritage_V2",
    "FTDNA_V1_Affy",
)


def resolve_format_key(format_key: str) -> str | None:
    """The format name for a --formats item, or None for CombinedKit v5
    ("all", "combined_all", "combined_v5" or empty). "combined_v3" resolves to
    ``COMBINED_V3_FORMAT``. Refuses anything else: the name becomes part of
    template and output paths."""
    normalized = format_key.strip().lower().replace("-", "_")
    if normalized in {"", "all", "combined_all", "combined_v5", "combinedkit_v5"}:
        return None
    if normalized in {"combined_v3", "combinedkit_v3"}:
        return COMBINED_V3_FORMAT
    name = FORMAT_ALIASES.get(normalized) or next(
        (known for known in _TEMPLATE_FORMATS if known.lower() == normalized), None
    )
    if name is None:
        raise UsageError(
            f"Unknown microarray format {format_key.strip()!r}; see "
            "`wgsextract microarray --help` for the list."
        )
    return name


def get_template_format(format_name: str) -> TemplateFormat | None:
    """Returns metadata for known microarray formats."""
    return _TEMPLATE_FORMATS.get(format_name)
