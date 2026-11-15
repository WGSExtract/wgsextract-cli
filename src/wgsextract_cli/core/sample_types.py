"""Sample fact enums shared by runtime detection and generated document types."""

from typing import Literal, get_args

Sex = Literal["Male", "Female"]
Platform = Literal["illumina", "ont", "pacbio", "ultima", "dnbseq"]
Confidence = Literal["high", "medium", "low"]
Status = Literal["ok", "failed", "cancelled"]
SampleSchema = Literal["wgsextract.sample/1"]
SCHEMA_ID: str = get_args(SampleSchema)[0]
