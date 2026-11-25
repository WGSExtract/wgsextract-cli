"""Messages for the ref commands."""

CLI_HELP = {
    "cmd_ref-cadd": "Download CADD v1.7 deleteriousness scores (all possible SNVs plus gnomAD indels, ~85 GB per build) with their tabix indexes.",
    "cmd_ref-gerp": "Download GERP++ conservation scores as a bigWig (hg38 from Ensembl, ~9.6 GB; hg19 from UCSC, ~7.6 GB).",
    "cmd_ref-index": "Prepare a FASTA file for use by indexing it. This is required before you can align reads or call variants against it.",
    "cmd_ref-download": "Download a reference FASTA from a URL (--url) to a path (--out). For curated hg19/hg38/T2T genomes use 'ref library --install'.",
    "cmd_ref-verify": "Ensure your reference genome file isn't corrupted and has all the necessary companion files (indexes, dicts).",
    "cmd_ref-count-ns": "Calculate the percentage of 'N' (unknown) bases in a genome. Useful for understanding the 'mappability' of different reference builds.",
    "cmd_ref-gene-map": "Download or delete lightweight gene-to-coordinate mapping files (hg19/hg38), enabling you to filter VCFs by gene name (e.g. BRCA1), with the RefSeq transcript and exon models the genome viewer and info gene show.",
    "cmd_ref-library": "Opens the interactive reference manager to help you organize and download genomic data.",
}

LOG_MESSAGES = {
    "del_genemap_success": "Successfully deleted Gene Maps.",
    "del_genemap_failed": "Failed to delete Gene Maps.",
    "dl_genemap_success": "Successfully downloaded Gene Maps.",
    "dl_genemap_failed": "Gene Map download cancelled or failed.",
    "analyzing_ns": "Analyzing N segments in {path}",
    "ref_downloading": "Downloading {url} to {path}",
    "ref_indexing": "Indexing {path} with faidx",
    "ref_creating_dict": "Creating dict {path}",
    "ref_verifying": "Verifying integrity of {path}...",
    "ref_gzip_test": "Running gzip integrity test...",
    "ref_gzip_ok": "Gzip integrity: OK",
    "ref_faidx_check": "Running samtools faidx check...",
    "ref_faidx_ok": "Samtools faidx: OK",
    "ref_valid": "Reference {filename} appears to be valid.",
}
