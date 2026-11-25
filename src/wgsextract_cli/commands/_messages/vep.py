"""Messages for the vep commands."""

CLI_HELP = {
    "cmd_vep": "Variant Effect Predictor (VEP, with vep-rs by default or Ensembl VEP): annotate variants and manage the VEP cache.",
    "cmd_vep-run": "Variant Effect Predictor, with vep-rs (default) or Ensembl VEP (--engine ensembl). Predicts the functional impact of your variants (e.g., if a mutation likely breaks a gene or causes a specific disease).",
    "cmd_vep-download": "Build vep-rs's VEP cache from Ensembl's files (GRCh37/38, T2T-CHM13v2.0), or download Ensembl VEP's with --engine ensembl, for offline annotation.",
    "cmd_vep-verify": "Check your existing VEP cache for missing files or corruption.",
    "cmd_vep-delete": "Delete one release and assembly of your VEP cache.",
}

LOG_MESSAGES = {
    "vep_downloading": "Downloading VEP cache from {host}...",
    "vep_verifying_checksums": "Verifying download against Ensembl CHECKSUMS...",
    "vep_checksum_ok": "Checksum verification successful: {sum} {blocks}",
    "vep_checksum_failed": "Checksum verification FAILED! Expected: {expected_sum} {expected_blocks}, Got: {actual_sum} {actual_blocks}",
    "vep_extracting": "Download complete. Extracting {filename}...",
    "vep_extraction_complete": "Extraction complete.",
    "vep_ready": "VEP cache is ready at {path}",
    "vep_verifying": "Verifying VEP cache for {species} {version} {assembly}...",
    "vep_location": "Location: {path}",
    "vep_cache_missing": "Cache directory not found: {path}",
    "vep_info_found": "Found info.txt",
    "vep_info_missing": "info.txt missing - cache might be incomplete.",
    "vep_chrs_missing": "Missing chromosomal data for: {chrs}",
    "vep_chrs_ok": "All primary chromosomal directories (1-22, X, Y, MT) present.",
    "vep_verification_complete": "Verification complete.",
    "vep_calling_pre": "Input is BAM/CRAM. Performing variant calling first...",
    "vep_using_cache": "Using VEP cache at {path}",
    "vep_preprocessing_chr": "Preprocessing VCF to add 'chr' prefix: {input} -> {output}",
    "vep_skipping_exists": "Skipping {input}, output already exists at {output}. Use --force to re-run.",
    "vep_batch_summary": "VEP Batch Summary:",
    "vep_batch_item": "{filename:<30} | {status:<10} | {duration:<10}",
    "vep_running": "Running VEP: {command}",
    "vep_complete": "VEP analysis complete. Results saved to {path}",
}
