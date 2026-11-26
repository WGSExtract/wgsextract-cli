"""Public vocabulary for command certification, independent of the registry."""

from enum import Enum
from typing import Annotated, TypedDict

from wgsextract_cli.core.output_schema import Constraints


class CertificationLevel(str, Enum):
    CERTIFIED = "Certified"
    VALIDATED = "Validated"
    TESTED = "Tested"
    EXPERIMENTAL = "Experimental"


LEVEL_CRITERIA = {
    CertificationLevel.CERTIFIED: (
        "Real-data truth or upstream parity on Linux, macOS and Windows, "
        "real-tool regression tests, and production use without open correctness bugs."
    ),
    CertificationLevel.VALIDATED: (
        "Real-data validation on at least one platform, real-tool tests and documented limits."
    ),
    CertificationLevel.TESTED: (
        "Fixture or fake-genome tests exercise the real tools or built-in operation."
    ),
    CertificationLevel.EXPERIMENTAL: (
        "Coverage is partial or unverified, or the command is research-only. Use with care."
    ),
}

CertificationLabel = Annotated[
    str, Constraints(enum=tuple(level.value for level in CertificationLevel))
]


class CertificationEvidence(TypedDict):
    summary: str
    links: list[str]
    platforms: list[str]
    reviewed: str
