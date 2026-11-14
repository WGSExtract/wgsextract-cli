"""Translate the shared bcftools sex/PAR contract into caller intervals."""

import hashlib
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
from wgsextract_cli.core.region_selection import selected_intervals
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import WGSExtractError, run_command


@dataclass(frozen=True)
class PloidyInterval:
    chrom: str
    start: int
    end: int
    ploidy: int


def calling_ploidy(
    alignment: str,
    reference: str,
    library: ReferenceLibrary,
    directory: str,
    region: str | None,
    *,
    commas: bool = True,
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
        else {chrom: [(0, length)] for chrom, length in lengths.items()}
    )
    special = any(chrom.lower().removeprefix("chr") in {"x", "y"} for chrom in selected)
    if not special and not library.ploidy_file:
        return [
            PloidyInterval(
                chrom,
                start,
                end,
                1 if chrom.lower().removeprefix("chr") in {"m", "mt"} else 2,
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
    sex = sample_sex(alignment, directory) if sexes != {"*"} else None
    if sex is not None and sex not in {"Male", "Female"}:
        warn(
            "caller_sex_unknown",
            "Sample sex is unknown; applying the female ploidy policy, as bcftools "
            "does. Set the sample workspace sex to override.",
        )
    if sex is not None:
        current_result().add_metrics(calling_sex=sex)
    code = "M" if sex == "Male" else "F"
    rules = []
    for fields in rows:
        chrom, rule_start, rule_end, rule_sex, value = fields
        if rule_sex in {code, "*"}:
            try:
                rules.append((chrom, rule_start, rule_end, int(value)))
            except ValueError as exc:
                raise WGSExtractError(
                    "Malformed reference library ploidy file."
                ) from exc
    result = []
    for chrom, spans in selected.items():
        relevant = [row for row in rules if row[0] in {chrom, "*"}]
        for start, end in spans:
            breaks = {start, end}
            for _, first, last, _ in relevant:
                if first != "*" and last != "*":
                    breaks.update(
                        (
                            max(start, min(end, int(first) - 1)),
                            max(start, min(end, int(last))),
                        )
                    )
            points = sorted(breaks)
            for left, right in zip(points, points[1:], strict=False):
                matches = [
                    row
                    for row in relevant
                    if (row[1] == "*" or int(row[1]) - 1 <= left)
                    and (row[2] == "*" or int(row[2]) >= right)
                ]
                specific = [row for row in matches if row[0] == chrom]
                applicable = specific or matches or [("", "", "", 2)]
                if len({row[3] for row in applicable}) != 1:
                    raise WGSExtractError(
                        "Overlapping reference ploidy rules disagree; this caller "
                        "does not support that policy."
                    )
                ploidy = applicable[0][3]
                if ploidy not in {0, 1, 2}:
                    raise WGSExtractError("This caller supports only ploidy 0, 1 or 2.")
                result.append(PloidyInterval(chrom, left, right, ploidy))
    return result


def write_intervals(path: Path, intervals: list[PloidyInterval]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{i.chrom}\t{i.start}\t{i.end}\n" for i in intervals)
    path = path.with_stem(f"{path.stem}-{hashlib.sha256(content.encode()).hexdigest()}")
    try:
        with path.open("x") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_text() != content:
            raise WGSExtractError(f"Calling policy input has changed: {path}") from None
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
    import tempfile

    from wgsextract_cli.commands.vcf.basic import record_variants
    from wgsextract_cli.core.atomic_output import safe_output
    from wgsextract_cli.core.output_checks import warn_if_no_variants
    from wgsextract_cli.core.variant_files import ensure_vcf_indexed

    intervals = calling_ploidy(
        str(params.input), setup.ref, setup.lib, setup.outdir, params.region
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
