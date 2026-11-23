"""Prepare bound, unsplit VCFs and stream one chromosome at a time."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.commands.cohort_inputs import (
    Artifact,
    Cohort,
    Participant,
    verify_artifact,
)
from wgsextract_cli.core.builds import (
    build_from_chr1_length,
    build_from_reference_header,
)
from wgsextract_cli.core.reference_evidence import GQ, HEADER_KEY, ReferenceEvidence
from wgsextract_cli.core.reference_resolver import header_contig_lengths
from wgsextract_cli.core.region_selection import literal_region
from wgsextract_cli.core.utils import UsageError, WGSExtractError, popen, run_command
from wgsextract_cli.core.variant_confidence import detect_caller
from wgsextract_cli.core.variant_files import (
    chromosome_rename_mapping,
    match_chromosome_name,
)


@dataclass(frozen=True)
class Prepared:
    variants: Path
    evidence: Path
    reference_calls: ReferenceEvidence
    primary_samples: int
    explicit_gq: bool


def _headers(
    header: str, cohort: Cohort, lengths: Mapping[str, int]
) -> list[tuple[str, str]]:
    declared = header_contig_lengths(header)
    builds = {
        build_from_chr1_length(declared),
        *(
            build_from_reference_header(line.partition("=")[2])
            for line in header.splitlines()
            if line.startswith("##reference=")
        ),
    } - {None}
    if builds != {cohort.build}:
        raise UsageError("Unknown or mismatched VCF reference build.")
    if not declared:
        raise UsageError("VCF contig lengths must be declared.")
    mapped: set[str] = set()
    for chrom, size in declared.items():
        alias = match_chromosome_name(chrom, lengths)
        if alias is None or alias in mapped or lengths[alias] != size:
            raise UsageError("Reference/build mismatch in the VCF contig dictionary.")
        mapped.add(alias)
    return chromosome_rename_mapping(list(declared), list(lengths))


def _bcf(work: Path, operation: str, *args: object, label: str) -> str:
    try:
        return run_command(
            ["bcftools", operation, *args],
            cwd=work,
            capture_output=True,
            private_arguments=True,
        ).stdout
    except (OSError, WGSExtractError, subprocess.SubprocessError) as error:
        if operation == "norm":
            raise UsageError(
                f"Reference/build verification or normalization failed for {label}."
            ) from error
        raise UsageError(f"bcftools could not {operation} {label}.") from error


def _prepare(
    artifact: Artifact,
    sample: str,
    cohort: Cohort,
    lengths: dict[str, int],
    work: Path,
    label: str,
) -> tuple[Path, str, int]:
    verify_artifact(artifact, label)
    try:
        with artifact.path.open("rb") as source:
            run_command(
                ["bcftools", "view", "--no-version", "-Ob", "-o", "input.bcf", "-"],
                stdin=source,
                cwd=work,
                capture_output=True,
                private_arguments=True,
            )
    except (OSError, WGSExtractError, subprocess.SubprocessError) as error:
        raise UsageError(f"Could not read VCF/BCF for {label}.") from error
    header = _bcf(work, "view", "-h", "--no-version", "input.bcf", label=label)
    mapping = _headers(header, cohort, lengths)
    samples = _bcf(work, "query", "-l", "input.bcf", label=label).splitlines()
    if sample not in samples:
        raise UsageError(f"The requested VCF sample is not present in {label}.")
    if len(samples) > 1:
        (work / "sample.txt").write_text(sample + "\n", encoding="utf-8", newline="\n")
        _bcf(
            work,
            "view",
            "--no-version",
            "-S",
            "sample.txt",
            "-I",
            "-Ob",
            "-o",
            "sample.bcf",
            "input.bcf",
            label=label,
        )
        (work / "input.bcf").unlink()
        (work / "sample.bcf").rename(work / "input.bcf")
        (work / "sample.txt").unlink()
    if mapping:
        (work / "chroms.txt").write_text(
            "".join(f"{a}\t{b}\n" for a, b in mapping), encoding="utf-8", newline="\n"
        )
        _bcf(
            work,
            "annotate",
            "--no-version",
            "--rename-chrs",
            "chroms.txt",
            "-Ob",
            "-o",
            "renamed.bcf",
            "input.bcf",
            label=label,
        )
        (work / "input.bcf").unlink()
        (work / "renamed.bcf").rename(work / "input.bcf")
    # Do not split: the original GT's alternate indices are needed for AN and
    # reference-call status. Sorting also handles indels that moved left.
    _bcf(
        work,
        "norm",
        "--no-version",
        "-f",
        cohort.reference.path,
        "-c",
        "e",
        "-Ob",
        "-o",
        "normalized.bcf",
        "input.bcf",
        label=label,
    )
    (work / "input.bcf").unlink()
    _bcf(
        work,
        "sort",
        "-T",
        "sort",
        "-Ob",
        "-o",
        "calls.bcf",
        "normalized.bcf",
        label=label,
    )
    (work / "normalized.bcf").unlink()
    _bcf(work, "index", "calls.bcf", label=label)
    return work / "calls.bcf", header, len(samples)


def prepare_participant(
    cohort: Cohort,
    person: Participant,
    number: int,
    lengths: dict[str, int],
    work: Path,
) -> Prepared:
    primary, evidence_dir = work / "variants", work / "evidence"
    primary.mkdir(parents=True)
    evidence_dir.mkdir()
    label = f"participant #{number}"
    variants, vheader, primary_samples = _prepare(
        person.variants, person.sample, cohort, lengths, primary, f"{label}'s variants"
    )
    evidence, eheader, _ = _prepare(
        person.evidence,
        person.sample,
        cohort,
        lengths,
        evidence_dir,
        f"{label}'s evidence",
    )
    renamed = _bcf(
        evidence_dir, "view", "-h", "--no-version", evidence.name, label=label
    )
    ref = ReferenceEvidence(str(evidence), lengths, header_text=renamed)
    declarations = sum(
        line.startswith(f"##{HEADER_KEY}=") for line in eheader.splitlines()
    )
    if declarations and (declarations != 1 or ref.provenance is None):
        raise UsageError(f"Invalid reference evidence declaration for {label}.")
    engines = {
        detect_caller(vheader.splitlines()),
        detect_caller(eheader.splitlines()),
        ref.provenance.engine if ref.provenance else "unknown",
    } - {"unknown"}
    if engines - {cohort.caller}:
        raise UsageError(f"Mixed or mismatched callers for {label}.")
    if cohort.caller != "bcftools" and ref.quality_model != GQ:
        raise UsageError(f"Missing or incompatible GQ evidence model for {label}.")
    if ref.provenance:
        # Evidence headers are data, not permission to read a path. Current
        # callers publish literal intervals; bind them to the FASTA aliases.
        from dataclasses import replace

        provenance = ref.provenance
        if provenance.region and not re.fullmatch(
            r"[\w.:-]+(?:,[\w.:-]+)*", provenance.region
        ):
            raise UsageError(f"Nonliteral assessed region in {label}'s gVCF.")
        regions = provenance.region.split(",") if provenance.region else []

        def mapped(items: list[str], *, excluded: bool = False) -> list[str]:
            result = []
            for item in items:
                chrom, sep, span = item.partition(":")
                alias = match_chromosome_name(chrom, ref.lengths)
                try:
                    if alias is None or (excluded and "-" not in span):
                        raise ValueError
                    _, start, end = literal_region(alias + sep + span, ref.lengths)
                    if start >= end or end > ref.lengths[alias]:
                        raise ValueError
                except ValueError as error:
                    raise UsageError(
                        f"Invalid assessed or excluded interval for {label}."
                    ) from error
                result.append(f"{alias}:{start + 1}-{end}")
            return result

        ref.provenance = replace(
            provenance,
            region=",".join(mapped(regions)) or None,
            ploidy_zero=tuple(mapped(list(provenance.ploidy_zero), excluded=True)),
        )
    return Prepared(
        variants,
        evidence,
        ref,
        primary_samples,
        (ref.quality_model == GQ or "\n##FORMAT=<ID=GQ," in vheader),
    )


@contextmanager
def chromosome_records(path: Path, chrom: str) -> Iterator[Iterator[list[str]]]:
    proc = popen(
        ["bcftools", "view", "-H", "-r", chrom, path.name],
        cwd=path.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdout is not None
    try:
        yield (line.rstrip("\n").split("\t") for line in proc.stdout)
        # Confirm a successful exit even if there were no records.
        for _ in proc.stdout:
            pass
        if proc.wait():
            raise UsageError("bcftools could not stream a prepared cohort chromosome.")
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
