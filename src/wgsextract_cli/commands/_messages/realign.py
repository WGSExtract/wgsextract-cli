"""Messages for the realign commands."""

CLI_HELP = {
    "cmd_realign": "Re-align an existing BAM/CRAM to a (possibly different) reference. Reuses previously extracted FASTQs when available, otherwise extracts on demand. Verifies free disk space up-front.",
}

SYSTEM_MESSAGES = {
    "RealignBAMTimeWarnMesg": """There are several long tasks to (re)align a BAM file. The major steps are:
(1) Unalign the BAM to create the FASTQ files
(2) Create new Reference Genome Index
(3) Align FASTQ to Reference Genome
(4) Remove Duplicates, Sort and Index to Create the Final BAM
(5) Create the CRAM

We estimate, on this machine, this will take {time} hours.""",
}
