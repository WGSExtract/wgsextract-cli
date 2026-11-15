"""Translate the shared bcftools sex/PAR contract into caller intervals."""

import hashlib
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypeVar

from wgsextract_cli.api.vcf import VcfClair3Params, VcfGatkParams
from wgsextract_cli.commands.info.metrics import sample_sex
from wgsextract_cli.commands.vcf.basic import CallerSetup
from wgsextract_cli.core.bgzf import is_bgzf
from wgsextract_cli.core.builds import canonical_build, ploidy_for_build
from wgsextract_cli.core.reference_assets import read_fai_lengths, sequence_dictionary
from wgsextract_cli.core.reference_resolver import ReferenceLibrary
from wgsextract_cli.core.region_selection import Intervals, selected_intervals
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import WGSExtractError, run_command


@dataclass(frozen=True)
class PloidyInterval:
    chrom: str
    start: int
    end: int
    ploidy: int


def _sex_contig(chrom: str) -> str | None:
    key = chrom.upper().removeprefix("CHR")
    if key in {"X", "Y"}:
        return key
    if key in {"M", "MT"} or re.fullmatch(r"NC_012920\.\d+", key):
        return "M"
    for accession, name in (("NC_000023", "X"), ("NC_000024", "Y")):
        if re.fullmatch(rf"{accession}\.\d+", key):
            return name
    return None


def _policy(
    rows: list[list[str]], selected: Intervals, sex: str
) -> list[PloidyInterval]:
    rules = []
    try:
        for chrom, first_text, last_text, code, value in rows:
            if code in {sex, "*"}:
                start = int(first_text) - 1 if first_text != "*" else None
                end = int(last_text) if last_text != "*" else None
                ploidy = int(value)
                if (start is not None and start < 0) or (end is not None and end < 1):
                    raise ValueError
                if start is not None and end is not None and end <= start:
                    raise ValueError
                if ploidy not in {0, 1, 2}:
                    raise ValueError
                rules.append((chrom, start, end, ploidy))
    except ValueError as exc:
        raise WGSExtractError(
            "Malformed or unsupported reference ploidy rule."
        ) from exc
    result: list[PloidyInterval] = []
    for chrom, spans in selected.items():
        key = _sex_contig(chrom)
        relevant = [
            row
            for row in rules
            if row[0] in {chrom, "*"}
            or (key is not None and _sex_contig(row[0]) == key)
        ]
        for start, end in spans:
            breaks = {start, end}
            for _, first, last, _ in relevant:
                for bound in (first, last):
                    if bound is not None:
                        breaks.add(max(start, min(end, bound)))
            points = sorted(breaks)
            for left, right in zip(points, points[1:], strict=False):
                matches = [
                    row
                    for row in relevant
                    if (row[1] is None or row[1] <= left)
                    and (row[2] is None or row[2] >= right)
                ]
                specific = [row for row in matches if row[0] != "*"]
                values = {row[3] for row in specific or matches} or {2}
                if len(values) != 1:
                    raise WGSExtractError(
                        "Overlapping reference ploidy rules disagree."
                    )
                ploidy = values.pop()
                if (
                    result
                    and result[-1].chrom == chrom
                    and result[-1].end == left
                    and result[-1].ploidy == ploidy
                ):
                    result[-1] = replace(result[-1], end=right)
                else:
                    result.append(PloidyInterval(chrom, left, right, ploidy))
    return result


def calling_ploidy(
    alignment: str,
    reference: str,
    library: ReferenceLibrary,
    directory: str,
    region: str | None,
    *,
    commas: bool = True,
    default_contigs: set[str] | frozenset[str] | None = None,
) -> list[PloidyInterval]:
    """Zero-based half-open intervals, including ploidy-zero exclusions.

    Sex resolution uses the same workspace override/inference as bcftools.
    No scan is needed for an autosome-only request.
    """
    lengths = read_fai_lengths(reference)
    if not lengths:
        if reference.endswith(".gz") and not is_bgzf(reference):
            lengths = {
                row["SN"]: int(row["LN"])
                for row in sequence_dictionary(reference) or ()
            }
        else:
            run_command(["samtools", "faidx", reference])
            lengths = read_fai_lengths(reference)
    if not lengths:
        raise WGSExtractError(
            "Cannot determine reference contigs for the ploidy policy."
        )
    try:
        selected = (
            selected_intervals(region, lengths, commas=commas) if region else None
        )
    except (ValueError, OSError) as exc:
        raise WGSExtractError(f"Invalid calling region: {exc}") from exc
    selected = (
        selected
        if selected is not None
        else {
            chrom: [(0, length)]
            for chrom, length in lengths.items()
            if default_contigs is None or chrom in default_contigs
        }
    )
    special = any(_sex_contig(chrom) in {"X", "Y"} for chrom in selected)
    if not special and not library.ploidy_file:
        return [
            PloidyInterval(
                chrom,
                start,
                end,
                1 if _sex_contig(chrom) == "M" else 2,
            )
            for chrom, spans in selected.items()
            for start, end in spans
        ]
    if library.ploidy_file:
        text = Path(library.ploidy_file).read_text()
    elif canonical_build(library.build) in {"hg19", "hg38"}:
        from wgsextract_cli.core.reference_processing import _ploidy_content

        assert library.build is not None
        text = _ploidy_content(ploidy_for_build(library.build))
    else:
        raise WGSExtractError(
            "Sex/PAR-aware calling needs a GRCh37/GRCh38 reference or a "
            "reference library ploidy file; this build is unsupported."
        )
    rows = [
        line.split()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not rows or any(len(row) != 5 for row in rows):
        raise WGSExtractError("Malformed reference library ploidy file.")
    sexes = {row[3] for row in rows}
    if not sexes <= {"M", "F", "*"} or ("*" not in sexes and sexes != {"M", "F"}):
        raise WGSExtractError(
            "This caller does not support a single-sex or custom-sex ploidy table. "
            "Provide M and F rules or a sex-neutral '*' fallback."
        )
    male, female = _policy(rows, selected, "M"), _policy(rows, selected, "F")
    if male == female:
        return female
    sex = sample_sex(alignment, directory)
    if sex is not None and sex not in {"Male", "Female"}:
        warn(
            "caller_sex_unknown",
            "Sample sex is unknown; applying the female ploidy policy, as bcftools "
            "does. Set the sample workspace sex to override.",
        )
    if sex is not None:
        current_result().add_metrics(calling_sex=sex)
    return male if sex == "Male" else female


def write_intervals(path: Path, intervals: list[PloidyInterval]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{i.chrom}\t{i.start}\t{i.end}\n" for i in intervals)
    path = path.with_stem(f"{path.stem}-{hashlib.sha256(content.encode()).hexdigest()}")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".policy-")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", newline="\n") as stream:
            stream.write(content)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_text() != content:
                raise WGSExtractError(
                    f"Calling policy input has changed: {path}"
                ) from None
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)


def require_diploid(intervals: list[PloidyInterval], caller: str) -> None:
    if any(i.ploidy != 2 for i in intervals):
        raise WGSExtractError(
            f"{caller} does not support this sex/PAR ploidy policy. "
            "Use the CPU GATK/Clair3 path or Google's DeepVariant pipeline."
        )


P = TypeVar("P", VcfClair3Params, VcfGatkParams)


def call_partitioned(
    params: P,
    setup: CallerSetup,
    name: str,
    run: Callable[[P, int, CallerSetup | None, bool], None],
) -> None:
    """Run haploid and diploid jobs separately, then gather in reference order."""
    from wgsextract_cli.commands.vcf.basic import record_variants
    from wgsextract_cli.core.atomic_output import safe_output
    from wgsextract_cli.core.output_checks import warn_if_no_variants
    from wgsextract_cli.core.variant_files import ensure_vcf_indexed

    default_contigs = None
    if name == "clair3":
        from .clair3 import _MAJOR_CONTIGS

        default_contigs = _MAJOR_CONTIGS
    intervals = calling_ploidy(
        str(params.input),
        setup.ref,
        setup.lib,
        setup.outdir,
        params.region,
        default_contigs=default_contigs,
    )
    active = {i.ploidy for i in intervals if i.ploidy}
    if not active:
        raise WGSExtractError("The sex/ploidy policy excludes every requested base.")
    if active == {2} and all(i.ploidy == 2 for i in intervals):
        run(params, 2, setup, True)
        return
    if params.gpu:
        require_diploid(intervals, f"{name} --gpu")
    if getattr(params, "scatter", False):
        raise WGSExtractError(
            "GATK --scatter does not support sex/PAR ploidy partitions."
        )
    if name == "gatk" and params.region is None:
        from wgsextract_cli.core.cram_staging import cram_version

        version = cram_version(str(params.input))
        if version is not None and version >= (3, 1):
            raise WGSExtractError(
                "Whole-genome GATK CRAM 3.1 ploidy partitions require BAM input. "
                "Convert to BAM first, or select a bounded --region."
            )
    from wgsextract_cli.core.dependency_checks import verify_dependencies

    verify_dependencies(["bcftools", "tabix"])
    output = str(Path(setup.outdir, f"{name}.vcf.gz"))
    with tempfile.TemporaryDirectory(dir=setup.outdir, prefix=".ploidy_") as work:
        parts: list[str] = []
        for ploidy in sorted(active):
            part = Path(work, str(ploidy))
            bed = write_intervals(
                part / "regions.bed", [i for i in intervals if i.ploidy == ploidy]
            )
            # The final manifest must name only persistent outputs.
            run(
                replace(params, outdir=part, region=bed),
                ploidy,
                replace(setup, outdir=str(part)),
                False,
            )
            bounded = str(part / "bounded.vcf.gz")
            # Callers may emit variants overlapping an interval edge; each POS
            # belongs to exactly one ploidy partition.
            run_command(
                [
                    "bcftools",
                    "view",
                    "-R",
                    bed,
                    "--regions-overlap",
                    "0",
                    "-Oz",
                    "-o",
                    bounded,
                    str(part / f"{name}.vcf.gz"),
                ]
            )
            ensure_vcf_indexed(bounded)
            parts.append(bounded)
        with safe_output(output) as staged:
            combined = str(Path(work, "combined.vcf.gz"))
            run_command(["bcftools", "concat", "-a", "-Oz", "-o", combined, *parts])
            run_command(["bcftools", "sort", "-T", work, "-Oz", "-o", staged, combined])
    ensure_vcf_indexed(output)
    record_variants(
        output, "variants", warn_if_no_variants(output, setup.ref, params.region)
    )
