"""Messages for the align commands."""

CLI_HELP = {
    "cmd_align": "Map raw FASTQ reads to a reference genome. This is the primary step to convert raw data into a usable BAM/CRAM file.",
    "arg_se": "Single-End FASTQ file",
    "arg_long_read": "Use minimap2 for long-read alignment",
}

LOG_MESSAGES = {
    "aligning_reads": "Aligning {input} to {output} using {tool}",
    "indexing_output": "Indexing output BAM...",
}
