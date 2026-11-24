"""Help entries for ``qc``."""

from . import _VCF_IN, CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "qc fastqc": _h(
        "FastQC's <input>_fastqc.html and .zip (and --r2's); with --r2 and "
        "--multiqc, multiqc_report.html too.",
        "wgsextract qc fastqc --input sample_R1.fastq.gz",
        "wgsextract qc fastqc --input s_R1.fastq.gz --r2 s_R2.fastq.gz --multiqc",
    ),
    "qc fastp": _h(
        "<stem>_fp_1.fastq.gz (and _fp_2), <stem>_fastp.html and <stem>_fastp.json.",
        "wgsextract qc fastp --r1 sample_R1.fastq.gz --r2 sample_R2.fastq.gz",
    ),
    "qc multiqc": _h(
        "multiqc_report.html, in --outdir, else in the --input directory (or "
        "beside an --input file).",
        "wgsextract qc multiqc --input fastqc_reports/",
    ),
    "qc multiqc-metrics": _h(
        "wgsextract_*_mqc.json MultiQC custom-content files, in --outdir, else "
        "in the --input directory (or beside an --input file).",
        "wgsextract qc multiqc-metrics --input results/",
    ),
    "qc vcf": _h(
        "<input file name>.vcfstats.txt (e.g. sample.vcf.gz.vcfstats.txt), in "
        "--outdir, else beside the input.",
        f"wgsextract qc vcf {_VCF_IN}",
    ),
    "qc relate": _h(
        "relate.samples.tsv (sex, superpopulation), relate.pairs.tsv "
        "(relatedness), relate.html and the sketches in somalier/, in --outdir, "
        "else beside the first input.",
        "wgsextract qc relate me.cram",
        "wgsextract qc relate child.cram mother.cram father.cram --ped trio.ped",
        "wgsextract qc relate me.cram somalier/cousin.somalier --no-ancestry",
    ),
    "qc ancestry": _h(
        "<stem>.ancestry.tsv: K=7 and K=12 proportions with confidence intervals.",
        "wgsextract qc ancestry --input sample.cram",
        "wgsextract qc ancestry --input 23andme_raw.txt",
    ),
    "qc contamination": _h(
        "<stem>.contamination.selfSM (FREEMIX) and <stem>.contamination.Ancestry.",
        "wgsextract qc contamination --input sample.cram",
        "wgsextract qc contamination --input sample.bam --sites 10k",
    ),
    "qc roh": _h(
        "<stem>.roh.tsv (runs), <stem>.roh.json (wgsextract.roh/1: evidence, "
        "scope, F_ROH, isodisomy review flags, provenance) and "
        "<stem>.roh.markers.tsv.gz.",
        "wgsextract qc roh --input sample.vcf.gz --af-file gnomad.genomes.v4.1.sites.chr15.vcf.bgz --regions chr15",
        "wgsextract qc roh --input sample.vcf.gz --af-file 1kgp_af.vcf.gz --af-tag EUR_AF",
    ),
    "qc fake-data": _h(
        "fake.cram, fake.bam, fake.vcf or fake_R1/_R2.fastq.gz, and the fake "
        "reference.",
        "wgsextract qc fake-data --outdir out/fake --type bam,vcf --seed 1",
    ),
}
