"""Messages for the repair commands."""

CLI_HELP = {
    "cmd_repair-ftdna-bam": "Fix formatting errors specific to Family Tree DNA (FTDNA) BAM files that cause them to fail in standard tools like GATK.",
    "cmd_repair-ftdna-vcf": "Fix formatting errors in FTDNA VCF files to make them compatible with modern annotation tools like VEP.",
}

LOG_MESSAGES = {
    "repair_bam": "Repairing FTDNA BAM (SAM) from stdin to stdout...",
    "repair_vcf": "Repairing FTDNA VCF from stdin to stdout...",
}
