"""Messages for the qc commands."""

CLI_HELP = {
    "cmd_vcf-qc": "Generate statistical reports for your VCF file to check the quality and distribution of your variant calls.",
    "cmd_fake-data": "Generate fake BAM, CRAM, VCF, and FASTQ files for testing. BAM generation defaults to the fast streaming reference-backed SNP simulator.",
    "cmd_fastqc": "The industry-standard quality check for raw reads. Produces a visual report of base quality, GC content, and adapter contamination.",
    "cmd_fastp": "An ultra-fast all-in-one pre-processor. Automatically trims adapters and filters low-quality reads while generating a QC report.",
    "cmd_qc-relate": "Sex, relatedness and 1000 Genomes superpopulation of BAM/CRAM/VCF files with somalier; flags sample swaps. Optional: Linux and macOS, install with `pixi install -e somalier`.",
    "cmd_qc": "Quality control for raw reads, alignments and VCFs (fastp, FastQC, contamination, ancestry, runs of homozygosity, VCF stats) and test data generation.",
    "cmd_qc-ancestry": "Admixture-style ancestry proportions (HGDP+1KG reference components) from a BAM/CRAM, VCF or microarray file.",
    "cmd_qc-contamination": "Estimate how much of a GRCh37/38 BAM/CRAM's DNA comes from another person (VerifyBamID2 FREEMIX). Optional: Linux and macOS, install with `pixi install -e verifybamid2`.",
    "cmd_qc-roh": "Find runs of homozygosity in a VCF with bcftools roh and your local population allele frequencies; flags long runs over imprinted regions for review (not a diagnosis).",
}

LOG_MESSAGES = {
    "vcf_stats": "Running bcftools stats on {input} to {output}",
    "running_fastp": "Running fastp on {input}",
    "running_fastqc": "Running FastQC on {input}",
}
