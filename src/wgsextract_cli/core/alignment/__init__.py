"""Internal alignment helpers.

``input`` and ``fastq`` validate complete alignment/read inputs;
``profiles`` checks lossless output encodings.
``memory`` estimates mapping budgets and checks host/cgroup capacity.
``cram_counts`` reads exact container totals; ``cram_staging`` prepares
compatibility copies for tools with older or reference-free CRAM decoders.
``splice`` assembles sorted BAM/CRAM chunks without re-encoding their records.
``health`` samples duplicate marking, quality bins and ALT-aware mapping.
``metadata`` reads alignment/VCF headers and checks CRAM reference dictionaries;
``read_counts`` bounds alignment/FASTQ sampling for resource estimates;
``sequencing`` identifies platforms and preserves platform-detection provenance.
``fastq_platform`` infers low-level platform evidence from a bounded FASTQ prefix.
The retained ``core.sequencing`` and ``core.fastq_platform`` interfaces
re-export the same implementations and caches for existing callers.
Internal metadata, sampling and health callers use the package paths directly.
"""
