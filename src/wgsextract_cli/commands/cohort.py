"""Local research summaries over a versioned cohort manifest."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.api.cohort import CohortSummarizeParams
from wgsextract_cli.commands.cohort_counts import chromosome_rows
from wgsextract_cli.commands.cohort_inputs import (
    Cohort,
    local_path,
    read_cohort,
    verify_artifact,
)
from wgsextract_cli.commands.cohort_qc import join
from wgsextract_cli.commands.cohort_vcf import prepare_participant
from wgsextract_cli.core.atomic_output import atomic_write
from wgsextract_cli.core.builds import build_from_chr1_length
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.reference_resolver import read_fai_lengths
from wgsextract_cli.core.region_selection import Intervals, selected_intervals
from wgsextract_cli.core.result_card import CARD_SUFFIX, FAILED_MANIFEST
from wgsextract_cli.core.results import (
    MANIFEST_SUFFIX,
    current_result,
    human_output,
    manifest_dir_for,
    warn,
)
from wgsextract_cli.core.table_writer import (
    Column,
    TableWriter,
    check_format,
    provenance,
    table_path,
)
from wgsextract_cli.core.temp_directory import scratch_directory
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.core.variant_confidence import Thresholds
from wgsextract_cli.core.variant_files import chromosome_aliases
from wgsextract_cli.core.variant_table import COLUMNS, INTEGER, REAL

_SCHEMA = "wgsextract.cohort_variants/1"
_AUTOSOME = re.compile(r"(?:chr)?(?:[1-9]|1[0-9]|2[0-2])\Z")
_REGION = r"(?:chr)?(?:[1-9]|1[0-9]|2[0-2])(?::[0-9]+(?:-[0-9]+)?)?"
_COLUMNS = (
    *COLUMNS[:4],
    Column("ac", INTEGER, "Number of called copies of this ALT."),
    Column("an", INTEGER, "Number of eligible called chromosomes at this locus."),
    Column("af", REAL, "AC/AN; null when AN is zero."),
    Column("n_het", INTEGER, "Diploid participants carrying one copy of this ALT."),
    Column(
        "n_hom_alt", INTEGER, "Diploid participants carrying two copies of this ALT."
    ),
    Column("n_hemi", INTEGER, "Haploid participants carrying this ALT."),
    Column("callable_samples", INTEGER, "Participants with a complete eligible call."),
    Column(
        "reference_called_samples", INTEGER, "Participants called entirely reference."
    ),
    Column(
        "not_assessed_samples",
        INTEGER,
        "Participants without evidence that the locus was assessed.",
    ),
    Column(
        "uncallable_samples",
        INTEGER,
        "Participants with assessed but ineligible, missing or contradictory evidence.",
    ),
)


def _scope(params: CohortSummarizeParams, lengths: dict[str, int]) -> Intervals:
    autosomes = {chrom: n for chrom, n in lengths.items() if _AUTOSOME.fullmatch(chrom)}
    aliases: dict[str, str] = {}
    for chrom in autosomes:
        for alias in chromosome_aliases(chrom):
            if alias in aliases and aliases[alias] != chrom:
                raise UsageError("The reference contains ambiguous autosome aliases.")
            aliases[alias] = chrom
    if not params.region and params.bed is None:
        if not autosomes:
            raise UsageError("The reference has no supported autosomes.")
        return {chrom: [(0, n)] for chrom, n in autosomes.items()}
    if params.region and params.bed is not None:
        raise UsageError("Choose --region or --bed, not both.")
    try:
        if params.bed is not None:
            bed = local_path(Path.cwd(), str(params.bed))
            if not bed.name.lower().endswith((".bed", ".bed.gz")) or not bed.is_file():
                raise ValueError
            selection = str(bed)
        else:
            assert params.region is not None
            selection = params.region.replace(" ", "")
            if not re.fullmatch(rf"{_REGION}(?:,{_REGION})*", selection):
                raise ValueError
        # Resolve aliases, then merge again to union intervals supplied under
        # both names (e.g., one BED uses both chr1 and 1).
        intervals = selected_intervals(
            selection,
            {alias: autosomes[chrom] for alias, chrom in aliases.items()},
            commas=True,
        )
        canonical = ",".join(
            f"{aliases[alias]}:{start + 1}-{end}"
            for alias, ranges in intervals.items()
            for start, end in ranges
        )
        return selected_intervals(canonical, autosomes, commas=True)
    except (OSError, ValueError) as error:
        raise UsageError(
            "Cohort scope must cover a reference autosome; use --region or local --bed."
        ) from error


def _load_reference(cohort: Cohort) -> dict[str, int]:
    verify_artifact(cohort.reference, "the reference FASTA")
    verify_artifact(cohort.reference_index, "the FASTA index")
    with cohort.reference.path.open("rb") as stream:
        if stream.read(1) != b">":
            raise UsageError("Cohort analysis requires an indexed, uncompressed FASTA.")
    lengths = read_fai_lengths(cohort.reference.path)
    if not lengths or any(n <= 0 for n in lengths.values()):
        raise UsageError("The bound reference FASTA index is invalid.")
    if build_from_chr1_length(lengths) not in {None, cohort.build}:
        raise UsageError(
            "Reference/build mismatch: the FASTA disagrees with the cohort assembly."
        )
    return lengths


def _refuse_collisions(targets: Iterable[Path], inputs: set[Path]) -> None:
    # Resolved strings alone miss hard links and differently cased names on
    # case-insensitive filesystems.
    def identity(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
            return stat.st_dev, stat.st_ino
        except FileNotFoundError:
            return None
        except OSError as error:
            raise UsageError(
                "Could not verify the cohort's input/output paths."
            ) from error

    existing = {file_id for path in inputs if (file_id := identity(path)) is not None}
    for target in targets:
        if (
            local_path(target.parent, target.name) in inputs
            or identity(target) in existing
        ):
            raise UsageError("Cohort outputs must not overwrite an input file.")


def run_summary(params: CohortSummarizeParams) -> None:
    verify_dependencies(["bcftools"])
    check_format(params.table_format)
    cohort = read_cohort(params.cohort_manifest)
    lengths = _load_reference(cohort)
    scope = _scope(params, lengths)
    thresholds = Thresholds(
        params.min_depth, params.min_depth, params.min_gq, params.min_gq
    )
    outdir = local_path(Path.cwd(), str(params.outdir or Path.cwd()))
    stem = outdir / "cohort.variants"
    output = table_path(stem, params.table_format)
    private = outdir / "cohort.participants.private.json"
    inputs = {
        cohort.reference.path,
        cohort.reference_index.path,
        local_path(Path.cwd(), str(params.cohort_manifest)),
        *(
            artifact.path
            for person in cohort.participants
            for artifact in person.artifacts
        ),
    }
    if params.bed is not None:
        inputs.add(local_path(Path.cwd(), str(params.bed)))
    if cohort.relatedness:
        inputs.update(artifact.path for artifact in cohort.relatedness.artifacts)
    targets = [
        Path(output),
        *(
            manifest_dir_for(outdir) / (stem.name + suffix)
            for suffix in (MANIFEST_SUFFIX, FAILED_MANIFEST)
        ),
        *(
            stem.with_name(stem.name + suffix)
            for suffix in (CARD_SUFFIX, ".failed" + CARD_SUFFIX)
        ),
        *(
            index
            for suffix in (".bai", ".crai", ".csi", ".tbi", ".idx")
            for index in (Path(output + suffix), Path(output).with_suffix(suffix))
        ),
    ]
    if params.include_identifiers:
        targets.append(private)
    _refuse_collisions(targets, inputs)
    outdir.mkdir(parents=True, exist_ok=True)
    result = current_result()
    # Only allowlisted metadata: never inherit a sample table's footer.
    metadata = provenance(
        reference_build=cohort.build,
        sorted_by="reference contig order, position, ALT",
        schema=_SCHEMA,
        caller=cohort.caller,
        sample_count=str(len(cohort.participants)),
        min_depth=str(params.min_depth),
        min_gq=str(params.min_gq),
    )
    with scratch_directory(outdir, ".wgse_cohort_") as work:
        prepared = []
        for number, person in enumerate(cohort.participants, 1):
            checkpoint()
            prepared.append(
                prepare_participant(
                    cohort, person, number, lengths, Path(work, str(number))
                )
            )
        joined = join(cohort, (item.primary_samples for item in prepared))
        with TableWriter(
            output, _COLUMNS, params.table_format, metadata=metadata
        ) as table:
            for chrom, length in lengths.items():
                if chrom in scope:
                    checkpoint()
                    table.write(
                        chromosome_rows(
                            prepared, chrom, length, scope[chrom], thresholds
                        )
                    )
        rows = table.rows
    result.set_manifest_stem(stem)
    result.add_output(output, "cohort_variant_table", metrics={"rows": rows})
    if params.include_identifiers:
        atomic_write(
            private,
            (
                json.dumps(
                    {
                        "schema": "wgsextract.cohort_participants/1",
                        "participants": joined,
                    },
                    indent=2,
                )
                + "\n"
            ).encode("utf-8"),
        )
        result.add_output(private, "private_cohort_participants", kind="json")
    result.add_metrics(
        rows=rows,
        samples=len(cohort.participants),
        ancestry_available=sum(row["ancestry"] is not None for row in joined),
        reference_build=cohort.build,
        caller=cohort.caller,
        table_schema=_SCHEMA,
        min_depth=params.min_depth,
        min_gq=params.min_gq,
    )
    warn(
        "cohort_sensitive_summary",
        "Exact cohort counts are sensitive even without sample names; keep them private.",
    )
    human_output(f"{rows:,} cohort allele rows: {output}")
