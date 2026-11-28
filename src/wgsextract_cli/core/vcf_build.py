"""Strict assembly checks for annotation tracks whose coordinates are fixed."""

from functools import cache

from wgsextract_cli.core.alignment.metadata import (
    vcf_detection_header,
    vcf_metadata_build_hint,
)
from wgsextract_cli.core.builds import build_from_path, canonical_build, is_hg38_build
from wgsextract_cli.core.methylation_annotations import Chromosome, chromosomes
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.core.vcf_header import attributes


@cache
def primary_grch38_aliases() -> dict[str, Chromosome]:
    return {
        alias.upper(): chrom for chrom in chromosomes("hg38") for alias in chrom.aliases
    }


def _assembly_build(value: str) -> str | None:
    value = value.strip().strip('"')
    return canonical_build(value, exact=True) or build_from_path(value)


def require_grch38(vcf: str, annotation: str) -> None:
    def wrong_build(reason: str) -> UsageError:
        return UsageError(
            f"{annotation} annotation requires a GRCh38 VCF; {reason}. "
            "No liftover is performed."
        )

    aliases = primary_grch38_aliases()
    accessions = {alias.partition(".")[0] for alias in aliases if "." in alias}
    confirmed = False
    for line in vcf_detection_header(vcf).splitlines():
        if line.startswith("##contig=<") and line.endswith(">"):
            fields = attributes(line[10:-1])
            name = fields.get("ID", "").strip('"')
            key = name.upper()
            chrom = aliases.get(key)
            length = fields.get("length", "").strip('"')
            if chrom and length.isdecimal():
                if int(length) != chrom.length:
                    raise wrong_build(
                        f"{name} has length {int(length):,}, expected {chrom.length:,}"
                    )
                confirmed |= chrom.name != "chrM"
            if chrom and "." in key and chrom.name != "chrM":
                confirmed = True
            if not chrom and key.partition(".")[0] in accessions:
                raise wrong_build(f"incompatible primary accession {name}")
            declared = _assembly_build(fields.get("assembly", ""))
        elif line.lower().startswith("##assembly="):
            declared = _assembly_build(line.partition("=")[2])
        else:
            declared = vcf_metadata_build_hint(line)
        if declared:
            if not is_hg38_build(declared):
                raise wrong_build(f"the header declares {declared}")
            confirmed = True
    if not confirmed:
        raise wrong_build("no build in the header")
