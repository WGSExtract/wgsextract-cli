"""Internal alignment helpers.

``input`` and ``fastq`` validate complete alignment/read inputs;
``profiles`` checks lossless output encodings.
``memory`` estimates mapping budgets and checks host/cgroup capacity.
``cram_counts`` reads exact container totals; ``cram_staging`` prepares
compatibility copies for tools with older or reference-free CRAM decoders.
``splice`` assembles sorted BAM/CRAM chunks without re-encoding their records.
``health`` samples duplicate marking, quality bins and ALT-aware mapping.
The existing ``core.alignment_health`` public import interface re-exports
the same objects; new internal consumers import ``alignment.health``.
"""
