"""Messages for the info commands."""

CLI_HELP = {
    "cmd_info": "Perform a rapid analysis of your BAM/CRAM file to identify the reference genome build, file integrity, and sequencing metrics.",
    "arg_detailed": "Perform full index and body sample analysis (detailed mode)",
    "arg_csv": "Output the table as CSV instead of formatted text",
    "cmd_calculate-coverage": "Generate a full breadth-of-coverage report. Uses samtools coverage to calculate exactly how much of each chromosome was sequenced.",
    "cmd_coverage-sample": "Quickly estimate per-chromosome breadth of coverage by sampling ~1,000 evenly spaced, length-weighted 1 kb windows. An estimate; use calculate-coverage for exact numbers.",
    "cmd_info-gene": "Look at one gene from local data: its span and RefSeq transcripts, your VCF's variants in it with consequence, ClinVar, gnomAD and research-only ACMG annotations, and BAM/CRAM read depth over each exon against a callable threshold. Nothing is downloaded; missing data names the command that makes it.",
    "cmd_info-chip": "Identify the vendor, chip and revision of a consumer microarray raw-data file (23andMe, AncestryDNA, MyHeritage, FTDNA, ...) from its header and marker set, with a confidence score and the matching --formats template.",
}

LOG_MESSAGES = {
    "info_loading_n": "Loading refined N counts from {file}",
    "info_rendering_name": "By Reference Sequence Name",
    "info_header1": "Seq        Model    Model    # Segs      Map    Map",
    "info_header2": "Name         Len  'N' Len       Map   Gbases    ARD",
    "info_header_breadth": "    Breadth",
    "info_header_coverage": "   Coverage",
    "info_avg_read_depth": "Avg Read Depth",
    "info_avg_read_depth_wes": "Avg Read Depth (WES)",
    "info_gigabases": "Gigabases",
    "info_read_segs": "Read Segs",
    "info_reads": "Reads",
    "info_ref_genome": "Reference Genome",
    "info_refined_ns": "Refined N-counts",
    "info_refined_ns_active": "Active (using sidecar _ncnt.csv)",
    "info_avg_read_len": "Avg Read Length",
    "info_avg_insert_size": "Avg Insert Size",
    "info_file_content": "File Content",
    "info_bio_gender": "Bio Gender",
    "info_sequencer": "Sequencer",
    "info_platforms": "Platforms",
    "info_file_stats": "File Stats",
    "info_could_not_compute": "Could not compute",
    "info_metrics_cached": "(Metrics cached to {path} for future runs)",
    "sampling_metrics": "Sampling reads from {filename} for metrics...",
}

SYSTEM_MESSAGES = {
    "LowCoverageWarning": "This BAM file has a low mapped average read depth. This can lead to incorrect and fewer variant calls.",
    "LongReadSequenceWarning": "Input appears to be from a long-read sequencer: ONT detection, CPU minimap2 map-ont alignment/realignment, MM/ML tag retention and small-variant calling with x86-64 DeepVariant ONT_R104 (R10.4.1 only) or Clair3 (Linux and Apple Silicon), Sniffles structural-variant calling and modkit CpG methylation are supported and were validated on GIAB HG002, including whole-genome small-variant accuracy (see docs/ont.md). PacBio should use its platform-specific aligners/callers; short-read workflows are not validated for long reads.",
}
