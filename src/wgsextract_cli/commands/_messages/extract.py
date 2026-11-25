"""Messages for the extract commands."""

CLI_HELP = {
    "cmd_bam-subset": "Create a smaller version of your BAM file using a random fraction of reads (e.g. 0.1 for 10%%). Useful for testing pipelines quickly.",
    "cmd_mt-bam": "Isolate all mitochondrial-related reads into a new, smaller BAM file. Used for high-resolution mtDNA analysis or maternal haplogroup prediction on services like Mitoverse (Haplogrep). (Format: .BAM)",
    "cmd_mito-fasta": "Extract the Mitochondrial DNA (chrM) consensus sequence. Compatible with yFull (female-only mtDNA upload) and other sequence analysis tools. (Format: .FASTA)",
    "cmd_mito-vcf": "Call variants specifically for the mitochondrial DNA. Used by Mitoverse (Haplogrep) for haplogroup classification. (Format: .VCF)",
    "cmd_ydna-bam": "Extract all Y-chromosome reads into a separate BAM file. Compatible with yDNA Warehouse, yTree, and specialized paternal lineage tools. (Format: .BAM)",
    "cmd_ydna-vcf": "Call variants specifically for the Y-chromosome. Used by services like Cladefinder for haplogroup prediction. (Format: .VCF)",
    "cmd_y-mt-extract": "Extract both Y-chromosome and Mitochondrial reads into a single, combined BAM file. Recommended for yFull (male-only) WGS uploads. (Format: .BAM)",
    "cmd_unmapped": "Extract reads that did not align to the reference. Often used to find viral contamination or non-human DNA using services like Kaiju and CosmosID.",
    "cmd_custom": "Extract reads from a specific chromosomal region or gene of interest.",
    "cmd_region": "Extract the mtDNA, the Y chromosome, both, or the autosomes and X chromosome as any of a BAM, a VCF and a consensus FASTA. (Format: .BAM, .VCF, .FASTA)",
    "arg_fraction": "Decimal percentage (e.g. 0.1 for 10%%)",
}

LOG_MESSAGES = {
    "subsetting_file": "Subsetting {fraction} of reads to {output}...",
    "extracting_mt": "Extracting mtDNA reads ({mt_chr}) to {output}...",
    "extracting_y": "Extracting Y-chromosome reads ({chr_y}) to {output}...",
    "extracting_y_mt": "Extracting Y and MT reads to {output}...",
    "extracting_unmapped": "Extracting unmapped reads to {output}...",
    "extracting_region": "Extracting region {region} to {output}...",
    "calling_mito_variants": "Calling mitochondrial variants to {output}...",
    "calling_y_variants": "Calling Y-chromosome variants to {output}...",
}
