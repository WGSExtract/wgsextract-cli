"""Internal alignment command helpers, shared through the ``commands.align`` facade.

``reads`` owns read-source classification, input validation and length sampling.
``read_groups`` preserves sample/platform and sequencer read-group metadata.
``selection`` owns backend choice, compatibility checks and preset resolution.
``output`` owns streamed output publication, index staging and validation.
``common`` owns shared job types and reference/index primitives.
``short_reads`` owns native BWA selection and memory planning.
``preparation`` owns read/reference validation and resource preflight.
``gpu`` dispatches Parabricks alignment; ``backends`` owns Ultima,
minimap2 and pbmm2 execution.
"""
