"""VCF shared parameter declarations and their field helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from wgsextract_cli.api._params import Params, param
from wgsextract_cli.api._shared import (
    optional_input_param,
    outdir_param,
    ref_param,
    threads_param,
)
from wgsextract_cli.core.settings_schema import REFERENCE_EVIDENCE_MODES

_Public = TypeVar("_Public")


def _public(value: _Public) -> _Public:
    """Retain the public API origin used by pickle and type hints."""
    value.__module__ = "wgsextract_cli.api.vcf"
    return value


@_public
def reference_evidence_param(gvcf: str, cost: str) -> Any:
    """``--reference-evidence``: every caller's opt-in gVCF (#863)."""
    return param(
        None,
        help=f"Also write reference evidence: a gVCF, {gvcf} (+ .tbi), beside "
        f"the unchanged variant VCF (gvcf; costs {cost}), or not (none). "
        "Default: config reference_evidence, else none.",
        doc=f"gvcf also writes {gvcf} and its index (output roles "
        "reference_evidence, reference_evidence_index); the variant VCF is "
        "unchanged. None uses config reference_evidence, else none. See "
        "docs/reference-evidence.md.",
        choices=REFERENCE_EVIDENCE_MODES,
        config_key="reference_evidence",
    )


_OVERRIDE_HELP = "Optional override for VCF input file."


@_public
def _vcf_input_param(help: str = _OVERRIDE_HELP) -> Any:
    return param(
        None,
        help=help,
        path="file",
        doc="The VCF to read; wins over ``input``. On the CLI, --vcf-input "
        "defaults to config default_input_vcf when --input isn't given.",
    )


@_public
def _resource_param(help: str, config_key: str, **kwargs: Any) -> Any:
    return param(None, help=help, path="file", config_key=config_key, **kwargs)


@_public
def _min_score_param(label: str, example: str) -> Any:
    return param(
        None,
        help=f"Minimum {label} score to filter for (e.g., {example}).",
        doc=f"Also write the calls with a {label} score of at least this.",
    )


@_public
def _comma_items(params: Any, text: str) -> list[str]:
    from wgsextract_cli.core.region_selection import region_items

    return region_items(text, commas=True)


@_public
def _deepvariant_items(params: Any, text: str) -> list[str]:
    """Parabricks (``gpu``) splits on commas, DeepVariant on whitespace."""
    from wgsextract_cli.core.region_selection import region_items

    return region_items(text, commas=params.gpu)


@_public
def _region_param(help: str = "Chromosomal region") -> Any:
    return param(None, help=help, flags=("-r", "--region"))


@dataclass(frozen=True, kw_only=True)
@_public
class _VcfFileParams(Params):
    VCF_INPUT_OVERRIDES_INPUT = True

    input: Path | None = optional_input_param(
        "The VCF to read when ``vcf_input`` is unset."
    )
    vcf_input: Path | None = _vcf_input_param()
    outdir: Path | None = outdir_param("the VCF's folder")


@dataclass(frozen=True, kw_only=True)
@_public
class _AnnotatorParams(_VcfFileParams):
    ref: Path | None = ref_param()
    threads: int | None = threads_param()
