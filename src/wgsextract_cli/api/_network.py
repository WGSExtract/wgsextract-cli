"""Shared, typed consent for commands that can fetch missing public data."""

from dataclasses import dataclass

from ._params import Params, param


@dataclass(frozen=True, kw_only=True)
class DownloadParams(Params):
    download: bool | None = param(
        None,
        help="Allow missing public reference/model downloads; no sample files or "
        "genotypes are uploaded. Hosts and request data: docs/privacy.md. "
        "Default: config allow_reference_downloads, else off.",
        action="boolean_optional",
        config_key="allow_reference_downloads",
        writes=True,
        safe_value=False,
    )
