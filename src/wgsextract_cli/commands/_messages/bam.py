"""Messages for the bam commands."""

CLI_HELP = {
    "cmd_sort": "Sort alignments by genomic coordinates. Required by almost all downstream tools (like variant callers) to function correctly.",
    "cmd_index": "Create a random-access index (.bai/.crai) for your file. This allows tools to jump to specific regions instantly without reading the whole file.",
    "cmd_unindex": "Remove the index file associated with your BAM/CRAM. Use this to force a re-indexing or to clean up workspace.",
    "cmd_unsort": "Mark the file as 'unsorted' in the header. Rarely needed, but useful if a tool requires a specific header state for processing.",
    "cmd_to-cram": "Convert BAM to the modern CRAM format. Highly recommended for long-term storage as it is ~30-50%% smaller than BAM without losing data.",
    "cmd_to-bam": "Convert a CRAM file back to the traditional BAM format. Useful if you need to use an older tool that doesn't support CRAM.",
    "cmd_unalign": "Extract raw reads from a BAM/CRAM back into FASTQ format. Use this if you want to re-align your data to a different reference genome.",
    "cmd_bam-identify": "Analyze your BAM header to determine exactly which reference genome was used for the original alignment. It reads only the header; 'info' shows the same MD5 signature along with its other checks.",
    "cmd_bam-merge": "Merge several coordinate-sorted BAM/CRAM files (e.g. per-lane files) aligned to the same reference into one indexed file.",
    "cmd_bam-to-ubam": "Convert an aligned BAM/CRAM or FASTQ reads into a queryname-sorted unaligned BAM (uBAM) for GATK pipelines.",
    "cmd_bam_mgmt": "BAM/CRAM management commands.",
}

LOG_MESSAGES = {
    "delete_success": "Successfully deleted {filename}.",
    "indexing_file": "Indexing {path}...",
    "sorting_file": "Sorting {input} to {output}...",
    "converting_file": "Converting {input} to {output}...",
    "unaligning_reads": "Unaligning reads to FASTQ...",
    "ref_md5_signature": "MD5 Signature for {input}: {sig}",
}
