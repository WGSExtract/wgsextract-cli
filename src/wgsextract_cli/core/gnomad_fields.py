"""Names and callset selection for gnomAD ancestry-group maximum FAF95."""

from collections.abc import Mapping

GNOMAD_FAF95 = "GNOMAD_FAF95"
GNOMAD_JOINT_FAF95 = "fafmax_faf95_max_joint"
_STANDALONE_FAF95 = "fafmax_faf95_max"
GNOMAD_NATIVE_FAF95 = (GNOMAD_JOINT_FAF95, _STANDALONE_FAF95)


def gnomad_native_faf95(numbers: Mapping[str, str]) -> str | None:
    """Choose one callset from the header; a missing ALT never changes it."""
    key = (
        GNOMAD_JOINT_FAF95
        if GNOMAD_JOINT_FAF95 in numbers or "AF_joint" in numbers
        else _STANDALONE_FAF95
    )
    return key if numbers.get(key) == "A" else None
