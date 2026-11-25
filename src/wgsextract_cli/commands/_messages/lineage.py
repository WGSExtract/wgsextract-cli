"""Messages for the lineage commands."""

CLI_HELP = {
    "cmd_lineage-y-haplogroup": "Predict paternal haplogroups with native Yleaf-derived analysis (BAM/CRAM/VCF).",
    "cmd_lineage-y-private": "List candidate private (novel) Y SNPs: calls absent from the installed YBrowse/YFull marker sets, in callable regions, passing strict filters.",
    "cmd_lineage-y-str": "Count Y-STR repeats (FamilyTreeDNA Y-12 to Y-111, PowerPlex Y23, Yfiler Plus) from a BAM/CRAM for genealogy matching.",
    "cmd_lineage-mt-haplogroup": "Predict your maternal haplogroup using Haplogrep 3, optionally checking mtDNA contamination with haplocheck. Requires a BAM, CRAM or VCF with mitochondrial data.",
}

LOG_MESSAGES = {
    "running_haplogrep": "Running Haplogrep 3 lineage analysis on {input}",
}

SYSTEM_MESSAGES = {
    "YorubaWarning": "Your BAM file uses the Yoruba reference genome for mitochondrial DNA. This is incompatible with the rCRS genome that tools use. We cannot convert this for you at this time.",
}
