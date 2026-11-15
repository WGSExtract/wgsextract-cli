"""Sample fact enums shared by runtime detection and generated document types."""

from typing import Literal, TypedDict, TypeGuard, get_args

Sex = Literal["Male", "Female"]
Platform = Literal["illumina", "ont", "pacbio", "ultima", "dnbseq"]
Confidence = Literal["high", "medium", "low"]
Superpopulation = Literal["AFR", "AMR", "EAS", "EUR", "SAS"]
FactName = Literal[
    "sex",
    "platform",
    "build",
    "somalier_sex",
    "superpopulation",
    "read_length",
    "mean_coverage",
    "target_depths",
    "contamination",
    "ancestry",
]
Status = Literal["ok", "failed", "cancelled"]
SampleSchema = Literal["wgsextract.sample/1"]
SCHEMA_ID: SampleSchema = get_args(SampleSchema)[0]


class OverrideChanges(TypedDict, total=False):
    """User input before alias normalization; None removes an override."""

    sex: str | None
    platform: str | None


def is_sex(value: object) -> TypeGuard[Sex]:
    return value in get_args(Sex)


def is_platform(value: object) -> TypeGuard[Platform]:
    return value in get_args(Platform)


def is_superpopulation(value: object) -> TypeGuard[Superpopulation]:
    return value in get_args(Superpopulation)
