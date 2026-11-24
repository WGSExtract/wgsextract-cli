"""Help entries for ``typing``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "typing cyp2d6": _h(
        "Cyrius's <stem>.json/.tsv and <stem>.cyp2d6.pharmcat.tsv.",
        "wgsextract typing cyp2d6 --input sample.cram",
    ),
    "typing paraphase": _h(
        "<stem>.paraphase.tsv and .json (locus calls), plus raw Paraphase JSON, "
        "VCFs and a haplotagged BAM.",
        "wgsextract typing paraphase -i sample.cram --ref hs38.fa.gz --genes SMN1,PMS2,STRC -t 4",
    ),
    "typing smn": _h(
        "SMNCopyNumberCaller's <stem>.smn.tsv/.json; copy numbers and carrier "
        "status in the result's metrics.",
        "wgsextract typing smn --input sample.cram",
    ),
    "typing target-cnv": _h(
        "<stem>.target-cnv.tsv: copy-number class, confidence and callability per BED interval.",
        "wgsextract typing target-cnv -i sample.bam --bed targets.bed --ref hg38.fa.gz",
        "wgsextract typing target-cnv -i sample.cram --bed targets.bed --ref hg38.fa.gz --download",
    ),
    "typing hla": _h(
        "<stem>.t1k_genotype.tsv (and T1K's other files) and <stem>.hla.pharmcat.tsv.",
        "wgsextract typing hla --input sample.cram",
    ),
    "typing str": _h(
        "<stem>.str.tsv (or .str.parquet), per-locus repeat calls in the "
        "result JSON, and the caller's calls: "
        "ExpansionHunter's <stem>.str.vcf/.json, LongTR's <stem>.str.vcf.gz or "
        "STRkit's <stem>.str.vcf and <stem>.str.strkit.json. Targeted runs "
        "append a locus digest to the .str prefix.",
        "wgsextract typing str --input sample.cram",
        "wgsextract typing str --input sample.cram --loci HTT,FMR1 --format parquet",
    ),
}
