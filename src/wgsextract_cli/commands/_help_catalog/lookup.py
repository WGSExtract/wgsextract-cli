"""Help entries for ``lookup``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "lookup": _h(
        "Prints the genotype, evidence state and report findings; writes no "
        "files (--json: the wgsextract.lookup/1 document in details).",
        "wgsextract lookup --input sample.vcf.gz --rsid rs429358",
        "wgsextract lookup --input sample.vcf.gz --locus GRCh38:chr19:44908684:T>C",
        "wgsextract lookup --input sample.vcf.gz --gene APOE --json",
    ),
}
