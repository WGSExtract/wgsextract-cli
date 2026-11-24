"""Help entries for ``info``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "info": _h(
        "Prints a summary and caches it as <input file name>.wgse_info.json; "
        "with --format also <input file name>_chromosomes and _qc (QC stats) "
        "as .tsv or .parquet.",
        "wgsextract info --input sample.cram",
        "wgsextract info --input sample.cram --detailed",
        "wgsextract info --input sample.cram --format parquet",
    ),
    "info calculate-coverage": _h(
        "<input file name>_coverage.tsv, or with --bins <input file name>"
        "_coverage_bins.tsv (depth bands, or .parquet with --format parquet) "
        "instead; with --with-bins both .tsv files. With --by, <input>_<bed>_target_coverage.tsv (depth per "
        "target) and <input>_<bed>_target_summary.txt (on- and off-target "
        "depth, enrichment and targets below --min-depth). --status writes nothing.",
        "wgsextract info calculate-coverage --input sample.cram",
        "wgsextract info calculate-coverage --input sample.cram --bins --format parquet",
        "wgsextract info calculate-coverage --input sample.bam --by panel.bed --min-depth 20",
        "wgsextract info calculate-coverage --input sample.cram --status",
    ),
    "info coverage-sample": _h(
        "<input file name>_samplecvg.json; a whole-file run also updates the "
        "info cache.",
        "wgsextract info coverage-sample --input sample.cram",
    ),
    "info gene": _h(
        "Prints the gene's span, transcripts, variant counts and exon coverage; "
        "--json has the full wgsextract.gene/1 document in details. With "
        "--export, <vcf stem>_<gene>_<filter>_variants.tsv.",
        "wgsextract info gene --gene BRCA1 --input sample.cram "
        "--vcf-input sample_annotated.vcf.gz",
        "wgsextract info gene --gene BRCA2 --vcf-input sample_annotated.vcf.gz "
        "--export clinvar-plp",
        "wgsextract info gene --gene CFTR --input sample.bam --min-depth 20 --json",
    ),
    "info chip": _h(
        "Prints the vendor, chip and matching --formats template; writes no files.",
        "wgsextract info chip --input genome_raw_data.txt",
        "wgsextract info chip --list",
    ),
}
