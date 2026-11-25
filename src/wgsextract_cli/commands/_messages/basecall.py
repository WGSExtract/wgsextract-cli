"""Messages for the basecall commands."""

CLI_HELP = {
    "cmd_basecall": "Basecall Nanopore POD5 signal with ONT's Dorado (optional; install it yourself) into an unaligned BAM that keeps the MM/ML methylation tags and feeds `align --r1`. Picks the GPU (Apple silicon, else NVIDIA) or CPU, downloads hac/sup models into the wgsextract cache, and can re-basecall only a panel's reads with sup.",
}
