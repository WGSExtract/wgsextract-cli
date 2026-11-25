"""Messages for the pet commands."""

CLI_HELP = {
    "cmd_pet-align": "Align raw FASTQ reads against a cat or dog reference genome and call variants to produce a VCF file.",
}

LOG_MESSAGES = {
    "pet_aligning": "Step 1/2: Aligning {species} reads...",
    "pet_calling": "Step 2/2: Calling variants...",
    "pet_complete": "Pet Analysis complete!",
    "pet_results": "Results: {bam}, {vcf}",
}
