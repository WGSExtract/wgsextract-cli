"""Internal realignment preparation, planning and resumable execution.

``preparation`` owns the job model, FASTQ discovery and input qualification.
``resources`` estimates memory, disk and time requirements.
``mapping`` journals alignment and index publication.
``long_reads`` runs single-pass and chunked long-read realignment.
``steps`` coordinates extraction, streaming, retained FASTQs and resumption.

The commands.realign facade remains the command entry point and preserves
existing helper and job imports. Helpers depend only on earlier layers above,
not on the realign facade.
"""
