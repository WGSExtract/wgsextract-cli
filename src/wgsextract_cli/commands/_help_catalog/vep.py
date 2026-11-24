"""Help entries for ``vep``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "vep run": _h(
        "<input stem>_vep.vcf.gz (or .tsv/.json with --format), with VEP's summary.",
        "wgsextract vep run --input sample.vcf.gz",
        "wgsextract vep run --input sample.vcf.gz --assembly GRCh37 --format tab",
    ),
    "vep download": _h(
        "Into the VEP cache directory (vep_cache, or --vep-cache).",
        "wgsextract vep download --assembly GRCh38",
    ),
    "vep verify": _h(
        "Prints what is missing or damaged; writes no files.",
        "wgsextract vep verify --assembly GRCh38",
    ),
    "vep delete": _h(
        "Deletes <cache>/<species>/<release>_<assembly>; writes no files.",
        "wgsextract vep delete --assembly GRCh38",
    ),
}
