"""Run a targeted modkit pileup on an already-haplotagged alignment."""

import subprocess
from collections.abc import Iterator, Mapping, Sequence
from itertools import groupby
from pathlib import Path

from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.commands.methylation import (
    MODKIT,
    PileupOptions,
    TagSample,
    check_modification_tags,
    modification_tag_flags,
    modkit_command,
    modkit_failure,
    populated_reference,
    require_modification_platform,
)
from wgsextract_cli.commands.vcf.basic import plain_reference
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.alignment.sequencing import (
    input_platform,
    warn_mixed_platforms,
)
from wgsextract_cli.core.methylation_annotations import Chromosome, Region
from wgsextract_cli.core.methylation_phases import (
    IntervalLookup,
    PhaseEvidence,
    reference_end,
)
from wgsextract_cli.core.methylation_regions import merged_spans
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.utils import UsageError, WGSExtractError, popen, run_command


def _write_targets(
    bed: Path,
    reads: Path,
    fasta: Path,
    aliases: Mapping[str, Chromosome],
    names: Mapping[str, str],
    regions: Sequence[Region],
) -> dict[str, IntervalLookup]:
    header = get_bam_header(reads, fasta)
    if not (alignment_lengths := sequence_lengths(header)):
        raise WGSExtractError(f"Could not read contigs from {reads}.")
    for name, length in alignment_lengths.items():
        if (chrom := aliases.get(name)) is not None and (
            name != names[chrom.name] or length != chrom.length
        ):
            raise UsageError(
                f"Alignment contig {name} ({length} bases) does not match --ref contig "
                f"{names[chrom.name]} ({chrom.length} bases). Pileup requires the "
                "original alignment FASTA with matching contig names and lengths."
            )
    targets = {}
    with bed.open("w", encoding="utf-8", newline="\n") as out:
        for chrom, group in groupby(
            sorted(enumerate(regions), key=lambda item: item[1].chrom),
            key=lambda item: item[1].chrom,
        ):
            target_name = names.get(chrom)
            if target_name is None or target_name not in alignment_lengths:
                continue
            length = alignment_lengths[target_name]
            members = list(group)
            # Include the G at the right edge for strand combination by modkit.
            bounds = [
                (r.start, min(r.end + 1, length), i)
                for i, r in members
                if r.start is not None and r.end is not None and r.start < r.end
            ]
            if bounds:
                targets[target_name] = IntervalLookup(bounds)
                out.writelines(
                    f"{target_name}\t{start}\t{min(end + 1, length)}\n"
                    for start, end in merged_spans(r for _, r in members)
                )
    if not targets:
        raise UsageError(
            "No methylation regions have contigs in both --input and --ref."
        )
    return targets


def _target_alignments(reads: Path, fasta: Path, bed: Path) -> Iterator[list[str]]:
    # Match modkit's default exclusion of secondary, supplementary and duplicate reads.
    command = ["samtools", "view", "-M", "-L", str(bed), "-F", "0xD04"]
    if reads.suffix.lower() == ".cram":
        command.extend(("-T", str(fasta)))
    with bed.with_suffix(".stderr").open(
        "w+", encoding="utf-8", newline="\n"
    ) as stderr:
        process = popen(
            [*command, reads], stdout=subprocess.PIPE, stderr=stderr, text=True
        )
        assert process.stdout is not None
        exhausted = False
        try:
            for n, line in enumerate(process.stdout):
                if n % 1024 == 0:
                    checkpoint()
                yield line.rstrip("\n").split("\t")
            exhausted = True
        finally:
            process.stdout.close()
            if not exhausted and process.poll() is None:
                process.terminate()
            returncode = process.wait()
        if returncode:
            stderr.seek(0)
            raise WGSExtractError(
                f"samtools could not read targeted alignments in {reads}: "
                f"{stderr.read().strip() or f'exit code {returncode}'}"
            )


def _scan_phase_tags(
    reads: Path, fasta: Path, bed: Path, targets: Mapping[str, IntervalLookup]
) -> tuple[TagSample, int, dict[int, PhaseEvidence]]:
    count = tagged = opposite = duplex = hp_reads = 0
    phase: dict[int, PhaseEvidence] = {}
    for fields in _target_alignments(reads, fasta, bed):
        tags = fields[11:]
        has_tags, has_opposite, is_duplex = modification_tag_flags(tags)
        count += 1
        tagged += has_tags
        opposite += has_opposite
        duplex += is_duplex
        if not has_tags or not ("HP:i:1" in tags or "HP:i:2" in tags):
            continue
        hp_reads += 1
        ps = next((tag[5:] for tag in tags if tag.startswith("PS:i:")), "")
        phase_set = int(ps) if ps.isascii() and ps.isdecimal() and int(ps) > 0 else None
        start = int(fields[3]) - 1
        # A read spanning the target may contribute at the G just beyond its end.
        end = reference_end(start, fields[5])
        for _, _, index in targets[fields[2]].overlapping(start, end):
            if index not in phase:
                phase[index] = PhaseEvidence()
            phase[index].add(phase_set)
    return TagSample(count, tagged, opposite, duplex), hp_reads, phase


def phased_pileup(
    reads: Path,
    fasta: Path,
    compressed_ref: bool,
    work: Path,
    aliases: Mapping[str, Chromosome],
    names: Mapping[str, str],
    regions: Sequence[Region],
    options: PileupOptions,
    threads: str,
) -> tuple[tuple[Path, Path, Path], dict[int, PhaseEvidence]]:
    bed = work / "targets.bed"
    targets = _write_targets(bed, reads, fasta, aliases, names, regions)
    platform = input_platform(str(reads), str(fasta))
    require_modification_platform(platform)
    warn_mixed_platforms(
        str(reads),
        str(fasta),
        "only reads carrying MM/ML modification tags are summarized.",
    )
    sample, hp_reads, evidence = _scan_phase_tags(reads, fasta, bed, targets)
    check_modification_tags(sample, platform)
    if not hp_reads:
        raise WGSExtractError(
            "No targeted primary reads with MM/ML carry HP:i:1 or HP:i:2. Use an "
            "indexed haplotagged BAM/CRAM (for example from `vcf phase --haplotag`)."
        )
    partitions = work / "partitions"
    partitions.mkdir()
    reference_dir = work / "pileup-reference"
    reference_dir.mkdir()
    with plain_reference(str(fasta), str(work), compressed=compressed_ref) as ref:
        pileup_ref = populated_reference(ref, targets, str(reference_dir))
        command = modkit_command(
            str(reads),
            str(partitions),
            pileup_ref,
            options,
            threads,
            bed=str(bed),
            phased=True,
        )
        try:
            run_command(command, stdout=subprocess.PIPE)
        except (OSError, subprocess.SubprocessError) as error:
            raise WGSExtractError(modkit_failure(error, str(reads))) from error
    checkpoint()
    combined, hp1, hp2 = (
        partitions / f"{group}.bed.gz" for group in ("combined", "hp1", "hp2")
    )
    if not all(path.is_file() for path in (combined, hp1, hp2)):
        raise WGSExtractError(
            f"{MODKIT} did not write the expected phased bedMethyl files."
        )
    return (combined, hp1, hp2), evidence
