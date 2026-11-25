"""Shared English strings for the WGS Extract CLI."""

CLI_HELP = {
    "description": "WGS Extract Command-Line Interface (CLI)",
    "arg_debug": "Enable verbose debug logging.",
    "arg_card": "Also write <name>.card.html beside the result manifest: a "
    "self-contained page of the result, versions and input to view, print or "
    "share (overrides result_card).",
    "arg_full_help": "Print help info for all subcommands.",
    "arg_vcf_input": "Input VCF file path.",
    "arg_input": "Path to the input BAM/CRAM, FASTQ, or VCF file. Optional when --genome is used.",
    "arg_outdir": "Destination directory for outputs. Defaults to the selected --genome folder when set.",
    "arg_ref": "Path to the reference genome FASTA.",
    "arg_threads": "CPU threads to use: a positive integer, or 'auto' (default).",
    "arg_memory": "Memory per thread, e.g. '2G' (sorts: at most half the RAM in all).",
    "arg_region": "Chromosomal region (e.g. chrM, chrY:10000-20000)",
    "arg_r1": "Read 1 FASTQ file",
    "arg_r2": "Read 2 FASTQ file (optional)",
}

LOG_MESSAGES = {
    "generating_consensus": "Generating consensus FASTA to {output}...",
    "vcf_indexing": "Indexing VCF: {path}",
    "util_header_retry": "Header fetch failed with reference, retrying without reference...",
    "util_header_failed": "Failed to fetch BAM header for {path}: {error}",
    "input_required": "--input is required.",
    "ref_required": "--ref is required.",
    "ref_required_for": "--ref is required for {task}.",
    "file_not_found": "{label} file not found: {path}",
}

SYSTEM_MESSAGES = {
    "infoFreeSpace": "Operation {app} needs {size} GB of Free Space in the Temporary Directory to proceed. You likely also need {final} GB of free space in the Output Directory for the final file to be created. This is in addition to any space already used. Make sure the needed space is available before continuing.",
    "ExpectedWait": "Expected Wait is {time}",
    "insufficient_disk_title": "EXTRA LARGE WARNING: Insufficient Disk Space Detected!",
    "insufficient_disk_msg": "Detected: {detected} GB available on {path}\nEstimated: {needed} GB needed\nThis operation will likely consume more space than you have available.",
}
