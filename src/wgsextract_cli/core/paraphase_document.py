"""The normalized ``typing paraphase`` output, separate from upstream JSON."""

from typing import Annotated, Literal, TypedDict

from wgsextract_cli.core.output_schema import Constraints

SCHEMA_ID: Literal["wgsextract.paraphase/1"] = "wgsextract.paraphase/1"

CopyNumber = Annotated[int, Constraints(minimum=0)]


class LocusCall(TypedDict):
    gene: str
    total_cn: CopyNumber | None
    call_status: Literal["called", "partial", "no_call"]
    experimental: bool
    gene_fields: dict[str, object]


class ParaphaseDocument(TypedDict):
    schema: Literal["wgsextract.paraphase/1"]
    genome: Literal["19", "37", "38", "chm13"]
    platform: Literal["pacbio", "ont"]
    experimental: bool
    engine_version: str
    engine_license: Literal["BSD-3-Clause-Clear", "PacBio Software License Agreement"]
    loci: list[LocusCall]
