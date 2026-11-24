"""Provenance records of a result manifest: the reference and the public
data resources a run used, with the exact release of each (#1179)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from wgsextract_cli.core.output_schema import Constraints

Fingerprint = Annotated[
    Annotated[str, Constraints(pattern="^wgse-fp1:[0-9a-f]{64}$")] | None,
    Constraints(
        description="wgse-fp1:<sha256 of the size and the first, middle and last MiB>; see docs/result-manifest.md."
    ),
]


@dataclass
class ReferenceRecord:
    path: str
    build: str | None = None
    fingerprint: Fingerprint = None


ReleaseBasis = Literal["file_header", "pinned_sha256", "unrecorded"]


@dataclass
class ResourceRecord:
    """A public data resource the run used, and which release of it."""

    id: Annotated[
        str,
        Constraints(
            description='Stable resource identifier, e.g. "clinvar" or "gene-list".',
            pattern="^[a-z0-9][a-z0-9-]*$",
        ),
    ]
    name: str
    role: Annotated[
        str, Constraints(description="What the run used it for, e.g. annotation.")
    ]
    build: Annotated[
        str | None, Constraints(description="hg19 or hg38, when the resource says.")
    ] = None
    release: Annotated[
        str | None,
        Constraints(
            description="The exact release name the run used, or null when the "
            "resource doesn't record one (unknown, never guessed)."
        ),
    ] = None
    release_basis: Annotated[
        ReleaseBasis,
        Constraints(
            description="Where the release came from: the file's own header, a "
            "SHA-256-pinned catalog entry, or unrecorded."
        ),
    ] = "unrecorded"
    path: Annotated[str | None, Constraints(description="Absolute path.")] = None
    fingerprint: Fingerprint = None
    source_url: str | None = None
    licence: Annotated[
        str | None,
        Constraints(
            description="The source's terms as audited; recording them grants no "
            "redistribution rights."
        ),
    ] = None
    licence_url: str | None = None
    attribution: str | None = None

    def key(self) -> tuple[str, str | None]:
        return self.id, self.build
