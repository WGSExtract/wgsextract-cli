"""Internal alignment helpers.

``input`` and ``fastq`` validate complete alignment/read inputs;
``profiles`` checks lossless output encodings.
``memory`` estimates mapping budgets and checks host/cgroup capacity.
``cram_counts`` reads exact container totals; ``cram_staging`` prepares
compatibility copies for tools with older or reference-free CRAM decoders.
``splice`` assembles sorted BAM/CRAM chunks without re-encoding their records.
"""
