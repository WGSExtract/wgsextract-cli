"""VCF header metadata used for allele projection and table provenance."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from wgsextract_cli.core import acmg
from wgsextract_cli.core import variant_scores as scores
from wgsextract_cli.core._variant_table_fields import NHOMALT_INFO, POPMAX_INFO
from wgsextract_cli.core.gnomad_fields import GNOMAD_FAF95, GNOMAD_NATIVE_FAF95

_FIELD_LINE = re.compile(r"^##(INFO|FORMAT)=<(.*)>$")
_ATTRIBUTE = re.compile(r'([^,=\s]+)=("(?:[^"\\]|\\.)*"|[^,]*)')
_FORMAT_LIST = re.compile(r"Format:\s*(.+)$")


@dataclass
class VcfHeader:
    """What the table needs from a VCF header."""

    info_number: dict[str, str] = field(default_factory=dict)
    format_number: dict[str, str] = field(default_factory=dict)
    csq_fields: list[str] | None = None
    ann_fields: list[str] | None = None
    samples: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    info_formats: dict[str, list[str]] = field(default_factory=dict)

    @property
    def annotations(self) -> list[str]:
        """The annotation sources present, for provenance and the manifest."""
        found = []
        if self.csq_fields:
            found.append("vep")
        if self.ann_fields:
            found.append("snpeff")
        for name, keys in (
            ("clinvar", ("CLNSIG",)),
            (
                "gnomad",
                (
                    "GNOMAD_AF",
                    *POPMAX_INFO,
                    *NHOMALT_INFO,
                    GNOMAD_FAF95,
                    *GNOMAD_NATIVE_FAF95,
                ),
            ),
            ("pharmgkb", ("PHARMGKB",)),
            ("confidence", ("WGSE_CONF",)),
            ("sv_freq", ("SV_FREQ",)),
            ("acmg", (acmg.CLASS,)),
        ):
            if any(key in self.info_number for key in keys):
                found.append(name)
        csq = set(self.csq_fields or ())
        for name, score_keys in scores.ANNOTATIONS:
            if any(key in self.info_number or key in csq for key in score_keys):
                found.append(name)
        return found


def _subfield_names(description: str) -> list[str]:
    """VEP's 'Format: Allele|Consequence|...' or SnpEff's
    "Functional annotations: 'Allele | Annotation | ...'" as a list of names."""
    match = _FORMAT_LIST.search(description)
    names = match.group(1) if match else description.partition(":")[2]
    return [name.strip(" '\"") for name in names.split("|")]


def parse_header(lines: Iterable[str]) -> VcfHeader:
    """Parse ``##`` header lines and the ``#CHROM`` line."""
    header = VcfHeader()
    for line in lines:
        line = line.rstrip("\r\n")
        header.lines.append(line)
        if line.startswith("#CHROM"):
            header.samples = line.split("\t")[9:]
            continue
        match = _FIELD_LINE.match(line)
        if not match:
            continue
        kind, body = match.groups()
        attributes = dict(_ATTRIBUTE.findall(body))
        key, number = attributes.get("ID"), attributes.get("Number")
        if key is None or number is None:
            continue
        if kind == "FORMAT":
            header.format_number[key] = number
            continue
        header.info_number[key] = number
        description = attributes.get("Description", "").strip('"')
        if key == "CSQ":
            header.csq_fields = _subfield_names(description)
        elif key == "ANN":
            header.ann_fields = _subfield_names(description)
        elif _FORMAT_LIST.search(description):
            header.info_formats[key] = _subfield_names(description)
    return header
