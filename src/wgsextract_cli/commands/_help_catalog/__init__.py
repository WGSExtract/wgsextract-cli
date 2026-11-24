"""Examples and outputs for every runnable command (the help contract).

Each command's ``--help`` ends with these, ``wgsextract help <command> --json``
and docs/commands.md publish them, and tests/test_cli_help.py checks that
every runnable command has an entry and that every example parses.

``outputs`` names the files a command writes, which go to the output
directory (--outdir, the configured outdir or the --genome folder) unless the
text says otherwise. A run that registers outputs also writes one
``.manifest.json`` beside its main output; that isn't repeated here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandHelp:
    examples: tuple[str, ...]
    outputs: str


def _h(outputs: str, *examples: str) -> CommandHelp:
    return CommandHelp(examples, outputs)


# From the curated example-genome catalog (_examples_catalog.EXAMPLES); a test
# checks the 'example-genome' examples name real IDs and tags.
_STARTER_EXAMPLE = "na12878-lowcov-bam"
_PACBIO_TAG = "pacbio"

_VCF_IN = "--vcf-input sample.vcf.gz"
_ANNOTATED = "{name}_annotated.vcf.gz (+ .tbi)"
_SCORED = _ANNOTATED + "; with --min-score N, also {name}_gt_N.vcf.gz."
_LIBRARY = "Into the reference library (reflib)"


def _build_catalog() -> dict[str, CommandHelp]:
    # Group modules import the helpers and constants defined above.
    from . import (
        align,
        analyze,
        bam,
        basecall,
        benchmark,
        cohort,
        config,
        deps,
        example_genome,
        extract,
        help,
        info,
        lineage,
        methylation,
        microarray,
        panel,
        pet_align,
        qc,
        realign,
        ref,
        repair,
        report,
        report_issue,
        sample,
        score,
        serve,
        sign,
        typing,
        vcf,
        vep,
    )

    catalog: dict[str, CommandHelp] = {}
    # Keep the existing command and group order.
    for group in (
        score,
        help,
        config,
        info,
        sample,
        microarray,
        lineage,
        extract,
        realign,
        basecall,
        align,
        vcf,
        cohort,
        vep,
        ref,
        deps,
        panel,
        report,
        report_issue,
        serve,
        example_genome,
        bam,
        qc,
        repair,
        analyze,
        typing,
        methylation,
        pet_align,
        sign,
        benchmark,
    ):
        for key, entry in group.ENTRIES.items():
            if key in catalog:
                raise ValueError(
                    f"Duplicate command help key {key!r} in {group.__name__}"
                )
            catalog[key] = entry
    return catalog


COMMAND_HELP = _build_catalog()
