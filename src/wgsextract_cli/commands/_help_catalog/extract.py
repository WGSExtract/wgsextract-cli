"""Help entries for ``extract``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "extract mito-fasta": _h(
        "<input stem>_MT.fasta: the mtDNA consensus sequence.",
        "wgsextract extract mito-fasta --input sample.cram",
    ),
    "extract mito-vcf": _h(
        "<input stem>_MT.vcf.gz (+ .tbi).",
        "wgsextract extract mito-vcf --input sample.cram",
    ),
    "extract mt-bam": _h(
        "<input stem>_mtDNA.bam (+ .bai).",
        "wgsextract extract mt-bam --input sample.cram",
    ),
    "extract bam-subset": _h(
        "<input stem>_subset.bam (+ .bai).",
        "wgsextract extract bam-subset --input sample.cram --fraction 0.1",
    ),
    "extract ydna-bam": _h(
        "<input stem>_Y.bam (+ .bai).",
        "wgsextract extract ydna-bam --input sample.cram",
    ),
    "extract ydna-vcf": _h(
        "<input stem>_Y.vcf.gz (+ .tbi).",
        "wgsextract extract ydna-vcf --input sample.cram",
    ),
    "extract y-mt-extract": _h(
        "<input stem>_Y_MT.bam (+ .bai).",
        "wgsextract extract y-mt-extract --input sample.cram",
    ),
    "extract unmapped": _h(
        "<input stem>_unmapped.bam (+ .bai); FASTQ files with --fastq.",
        "wgsextract extract unmapped --input sample.cram",
        "wgsextract extract unmapped --input sample.cram --fastq",
    ),
    "extract custom": _h(
        "<input stem>_<region>.bam (+ .bai).",
        "wgsextract extract custom --input sample.cram --region chr17:43044295-43125483",
        "wgsextract extract custom --input sample.cram --gene BRCA1",
    ),
    "extract region": _h(
        "Per chosen format: <input stem>_mtDNA|Y|Y_MT|autosomes_X.bam (+ .bai), "
        "<input stem>_MT|Y|Y_MT|autosomes_X.vcf.gz (+ .tbi) and .fasta.",
        "wgsextract extract region --input sample.cram --target ymt --formats bam,vcf",
        "wgsextract extract region --input sample.cram --target mt --formats fasta",
        "wgsextract extract region --input sample.cram --target autox --formats vcf,fasta",
    ),
}
