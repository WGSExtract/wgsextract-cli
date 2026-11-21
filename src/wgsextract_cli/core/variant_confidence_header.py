"""Caller identity and mapping-quality semantics from VCF headers."""

import re
from collections.abc import Iterable

_CALLER_MARKERS = (
    ("mutect2", re.compile(r'^##source="?Mutect2\b', re.I)),
    ("deepvariant", re.compile(r"^##DeepVariant_version", re.I)),
    ("dragen", re.compile(r"^##(DRAGENCommandLine|source=DRAGEN)", re.I)),
    ("clair3", re.compile(r"^##source=Clair3|^##clair3", re.I)),
    (
        "gatk",
        re.compile(
            r"^##(GATKCommandLine|source=(HaplotypeCaller|GenotypeGVCFs))", re.I
        ),
    ),  # fmt: skip
    ("freebayes", re.compile(r"^##source=freeBayes", re.I)),
    ("strelka", re.compile(r"^##source=strelka", re.I)),
    ("bcftools", re.compile(r"^##bcftools_callCommand")),
)


def mapping_quality_kind(header_lines: Iterable[str]) -> str:
    """ "mean" when the header declares INFO/MQ as an average, else "rms"."""
    for line in header_lines:
        if line.startswith("##INFO=<ID=MQ,"):
            text = line.lower()
            rms = "rms" in text or "root mean square" in text
            if not rms and ("average" in text or "mean" in text):
                return "mean"
    return "rms"


def detect_caller(header_lines: Iterable[str]) -> str:
    """The variant caller named in a VCF header, or "unknown"."""
    lines = list(header_lines)
    for line in lines:
        if line.lower().startswith("##gatkcommandline=<") and line.endswith(">"):
            # Consume quoted values whole, so text such as ",ID=Mutect2"
            # inside CommandLine is not mistaken for an attribute.
            attributes = re.findall(
                r'(?:^|,)\s*([^=,]+)=(?:"((?:\\.|[^"\\])*)"|([^,]*))',
                line.partition("<")[2][:-1],
            )
            if any(
                key.strip().lower() == "id"
                and (quoted or plain).strip().lower() == "mutect2"
                for key, quoted, plain in attributes
            ):
                return "mutect2"
    for caller, marker in _CALLER_MARKERS:
        if any(marker.match(line) for line in lines):
            return caller
    return "unknown"
