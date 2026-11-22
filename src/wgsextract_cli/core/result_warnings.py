"""Structured command warnings and their per-command recording policy."""

import os
from dataclasses import dataclass, field
from typing import Annotated

from wgsextract_cli.core.output_schema import Constraints

Scalar = str | int | float | bool | None
RESOURCE_REF_MISMATCH_EXCLUDED = "annotation_ref_mismatch_excluded"


@dataclass
class ResultWarning:
    """A condition the user should know about; ``code`` is stable for machines."""

    code: Annotated[str, Constraints(pattern="^[a-z][a-z0-9_]*$")]
    message: str
    details: dict[str, Scalar] = field(default_factory=dict)


def _same_resource(a: dict[str, Scalar], b: dict[str, Scalar]) -> bool:
    left, right = a.get("resource_path"), b.get("resource_path")
    if isinstance(left, str) and isinstance(right, str):
        return os.path.normcase(left) == os.path.normcase(right)
    # Older analyze step results stored only the basename and counts.
    return bool(a.get("resource")) and all(
        a.get(key) == b.get(key) for key in ("resource", "count", "total")
    )


def add_warning(warnings: list[ResultWarning], warning: ResultWarning) -> bool:
    """Record a warning unless that resource's REF check was already reported."""
    if warning.code == RESOURCE_REF_MISMATCH_EXCLUDED and any(
        existing.code == warning.code
        and _same_resource(existing.details, warning.details)
        for existing in warnings
    ):
        return False
    warnings.append(warning)
    return True
