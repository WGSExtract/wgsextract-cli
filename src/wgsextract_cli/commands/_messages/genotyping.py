"""Messages for the genotyping commands."""

CLI_HELP = {
    "cmd_typing": "Genotype genes and repeats a VCF can't capture from a WGS BAM/CRAM, or screen BED targets for copy number.",
    "cmd_typing-paraphase": "Type paralogous loci and copy number with Paraphase from PacBio HiFi or experimental, unvalidated ONT reads.",
    "cmd_typing-smn": "Call SMN1/SMN2 copy number with SMNCopyNumberCaller for SMA carrier screening (one SMN1 copy: carrier; none: affected). Short-read ~30x WGS only; not a diagnosis.",
    "cmd_typing-cyp2d6": "Call CYP2D6 star alleles, including copy-number and hybrid alleles, with Cyrius, and write a PharmCAT outside call. Cyrius's license allows noncommercial use only, and no distribution or modification.",
    "cmd_typing-hla": "Call HLA class I and II alleles with T1K from reads in the MHC, and write a PharmCAT outside call for HLA-A/HLA-B. The first run downloads 45 MB.",
    "cmd_typing-str": "Size the disease-causing repeats of ExpansionHunter's catalog (HTT, FMR1, C9ORF72, ...) with ExpansionHunter, or LongTR for Nanopore and PacBio reads, and flag those outside STRchive's normal range.",
    "cmd_typing-target-cnv": "Screen BED targets for GC-normalized copy number from short-read WGS. Uses a matching indexed FASTA; masked targets are withheld except clear calls at curated deletion loci.",
}
