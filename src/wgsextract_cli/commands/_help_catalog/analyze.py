"""Help entries for ``analyze``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "analyze comprehensive": _h(
        "The QC, lineage, variant and annotation outputs of each step, and a "
        "summary; analyze.steps.json records the completed steps.",
        "wgsextract analyze comprehensive --input sample.cram",
        "wgsextract analyze comprehensive --input sample.cram --force-step annotate",
        "wgsextract analyze comprehensive --vcf-inputs snps.vcf.gz sv.vcf.gz",
    ),
    "analyze batch-gen": _h(
        "A batch CSV/TSV for 'analyze comprehensive --batch' (--output).",
        "wgsextract analyze batch-gen --directory genomes/ --output batch.tsv",
    ),
}
