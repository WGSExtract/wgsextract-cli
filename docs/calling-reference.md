# Calling reference compatibility

BAM/CRAM variant callers validate the resolved FASTA before sampling reads or
calling variants. Every alignment header contig must exist under exactly the
same name and length in the FASTA index. An alignment containing only a subset
of a reference's contigs is supported; extra FASTA contigs are harmless.
`chr1` and `1` are not interchangeable. Use the reference used for alignment,
not merely another FASTA labelled with the same build.

When an existing FASTA `.dict` and the input both supply sequence MD5 tags,
these must agree too. Without these tags, names and lengths do **not** prove
identical sequence content: same-build masking, decoy substitutions, and other
same-length edits cannot be detected. Indexes and dictionaries must correspond
to the current FASTA. No unsafe calling override is provided.

ALT-unaware mapping warnings recommend the BWA path with its matching `.alt`
file. The minimap2 alignment path does not currently configure ALT declarations
and is not advertised as ALT-aware.
