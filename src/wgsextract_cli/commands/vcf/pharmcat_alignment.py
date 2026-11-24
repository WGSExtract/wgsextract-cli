"""Sparse, local read evidence for definitions missing from a PharmCAT VCF."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Set
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core import reference_evidence as evidence
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.alignment.samtools_commands import alignment_index
from wgsextract_cli.core.calling_reference import validate_calling_reference
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.ref_library.assets import read_fai_lengths
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.results import warn
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command
from wgsextract_cli.core.variant_confidence import Thresholds
from wgsextract_cli.core.variant_files import (
    check_vcf_build,
    get_vcf_samples,
    match_chromosome_name,
)

MIN_BASE_QUALITY = 20
MAX_PILEUP_DEPTH = 10_000
Locus = tuple[str, int]


@dataclass(frozen=True)
class AlignmentPileup:
    path: str
    index: str
    reference: str
    lengths: Mapping[str, int]
    read_groups: tuple[str, ...]

    @classmethod
    def checked(
        cls, alignment: Path, fasta: str, vcf: str, pharmcat_fasta: str
    ) -> AlignmentPileup:
        path = str(absolute_path(alignment))
        if not Path(path).is_file() or not path.lower().endswith((".bam", ".cram")):
            raise UsageError(f"--alignment requires a local BAM or CRAM file: {path}")
        index = alignment_index(path)
        if index is None:
            raise WGSExtractError(
                f"Alignment index missing for {path}. Run `samtools index` first."
            )
        if not Path(fasta).is_file() or not Path(fasta + ".fai").is_file():
            raise WGSExtractError(
                f"An indexed alignment FASTA is required: {fasta} (and .fai)."
            )
        validate_calling_reference(path, fasta, allow_reference_subset=True)
        header = get_bam_header(path)
        lengths = sequence_lengths(header)
        check_vcf_build(vcf, lengths, "the BAM/CRAM")
        pgx_lengths = read_fai_lengths(pharmcat_fasta)
        check_vcf_build(vcf, pgx_lengths, "the PharmCAT FASTA")
        if not any(match_chromosome_name(name, pgx_lengths) for name in lengths):
            raise WGSExtractError(
                "No alignment contig names match the PharmCAT FASTA. "
                "Use an alignment on GRCh38 with standard contig names."
            )
        for name, length in lengths.items():
            target = match_chromosome_name(name, pgx_lengths)
            if target and length != pgx_lengths[target]:
                raise WGSExtractError(
                    f"Alignment reference mismatch at {name}: BAM/CRAM length "
                    f"{length}, PharmCAT FASTA length {pgx_lengths[target]}."
                )
        groups = [
            dict(field.split(":", 1) for field in line.split("\t")[1:] if ":" in field)
            for line in header.splitlines()
            if line.startswith("@RG\t")
        ]
        samples = {group["SM"] for group in groups if group.get("SM")}
        vcf_samples = get_vcf_samples(vcf)
        if len(vcf_samples) != 1:
            raise WGSExtractError("PharmCAT requires a single-sample VCF.")
        if samples and samples != {vcf_samples[0]}:
            raise WGSExtractError(
                f"Alignment sample mismatch: BAM/CRAM SM tags ({', '.join(sorted(samples))}) "
                f"do not match the single VCF sample ({vcf_samples[0]})."
            )
        if not samples:
            warn(
                "alignment_sample_unverified",
                "The BAM/CRAM has no SM tags; verify it is from the VCF sample.",
            )
        read_groups = tuple(
            group["ID"]
            for group in groups
            if group.get("SM") == vcf_samples[0] and group.get("ID")
        )
        if samples and not read_groups:
            raise WGSExtractError("The BAM/CRAM sample has no named read groups.")
        return cls(path, index, fasta, lengths, read_groups)

    def collect(
        self,
        loci: Set[Locus],
        sequence: Mapping[Locus, str],
        thresholds: Thresholds,
        work: str,
    ) -> dict[Locus, tuple[evidence.State, int]]:
        """Read only absent definition bases; return point evidence in PharmCAT names."""
        if not loci:
            return {}
        ref_lengths = read_fai_lengths(self.reference)
        to_alignment = {}
        for chrom in sorted({chrom for chrom, _ in loci}):
            name = match_chromosome_name(chrom, self.lengths)
            if name is not None:
                if name not in ref_lengths:
                    raise WGSExtractError(
                        f"Alignment contig {name} has no exact match in {self.reference}. "
                        "Pass the FASTA with matching names using --alignment-reference."
                    )
                to_alignment[chrom] = name
        if not to_alignment:
            return {}
        wanted = {
            (to_alignment[chrom], pos): (chrom, pos)
            for chrom, pos in loci
            if chrom in to_alignment
        }
        regions = Path(work, "alignment_loci.bed")
        _write_regions(regions, wanted.keys())
        found: dict[Locus, tuple[evidence.State, int]] = {}
        pileup_filters = [
            "-B", "-C", "50",
            "-q", str(evidence.BCFTOOLS_MIN_MAPPING_QUALITY),
            "-Q", str(MIN_BASE_QUALITY),
            "-d", "0", "-f", self.reference,
        ]  # fmt: skip
        try:
            reads = self._read_subset(regions, Path(work))
            # A post-BQ depth cannot reveal truncation. Exclude high-raw-depth
            # targets first, and disable pileup downsampling (even upstream of
            # those targets, where reads later covering them could be discarded).
            raw_depth = run_command(
                ["samtools", "depth", "-J", "-b", str(regions), reads],
                capture_output=True,
            ).stdout
            for line in raw_depth.splitlines():
                chrom, pos, depth = line.split("\t")
                if int(depth) >= MAX_PILEUP_DEPTH:
                    locus = wanted.pop((chrom, int(pos)), None)
                    if locus is not None:
                        found[locus] = (evidence.State.NO_CALL, 0)
            if not wanted:
                return found
            if found:
                _write_regions(regions, wanted.keys())
            # bcftools AD/DP omits deletion placeholders and its indel model
            # may not emit a candidate. Keep the raw pileup as a veto.
            raw = run_command(
                [
                    "samtools", "mpileup", *pileup_filters, "-l", str(regions),
                    "--no-output-ends", "--no-output-ins", "--no-output-del", reads,
                ],
                capture_output=True,
            ).stdout  # fmt: skip
            vetoes = {
                (chrom, int(pos))
                for chrom, pos, _, depth, bases, _ in (
                    line.split("\t") for line in raw.splitlines()
                )
                if int(depth) and set(bases) - {".", ","}
            }
            text = run_command(
                [
                    "bcftools", "mpileup", *pileup_filters, "-I", "--ignore-RG",
                    "-a", "FORMAT/AD,FORMAT/DP", "-T", str(regions), "-Ov", reads,
                ],
                capture_output=True,
            ).stdout  # fmt: skip
        except subprocess.CalledProcessError as exc:
            raise WGSExtractError(
                f"Could not pile up {self.path}. Check its index and use the exact "
                "alignment reference FASTA with --alignment-reference."
            ) from exc
        for line in text.splitlines():
            if line.startswith("#"):
                continue
            fields = line.split("\t")
            locus = wanted.get((fields[0], int(fields[1])))
            if locus is None:
                continue
            if fields[3][0].upper() != sequence[locus].upper():
                raise WGSExtractError(
                    f"Alignment FASTA and PharmCAT FASTA disagree at {locus[0]}:{locus[1]}."
                )
            found[locus] = _point_evidence(
                fields, thresholds, blocked=(fields[0], int(fields[1])) in vetoes
            )
        for aligned_locus, locus in wanted.items():
            state = (
                evidence.State.NO_CALL
                if aligned_locus in vetoes
                else evidence.State.UNCOVERED
            )
            found.setdefault(locus, (state, 0))
        return found

    def _read_subset(self, regions: Path, work: Path) -> str:
        groups = []
        if self.read_groups:
            group_file = work / "alignment_read_groups.txt"
            group_file.write_text(
                "\n".join(self.read_groups) + "\n", encoding="utf-8", newline="\n"
            )
            groups = ["-D", f"RG:{group_file}"]
        reads = str(work / "alignment_reads.bam")
        run_command(
            ["samtools", "view", "-b", "-M", "-L", str(regions), "-T", self.reference,
             "-e", "mapq < 255 && max(qual) < 255", *groups,
             "-o", reads, "-X", self.path, self.index]
        )  # fmt: skip
        return reads


def _write_regions(regions: Path, loci: Set[Locus]) -> None:
    regions.write_text(
        "".join(f"{c}\t{p - 1}\t{p}\n" for c, p in sorted(loci)),
        encoding="utf-8",
        newline="\n",
    )


def _point_evidence(
    fields: list[str], thresholds: Thresholds, *, blocked: bool
) -> tuple[evidence.State, int]:
    values = dict(zip(fields[8].split(":"), fields[9].split(":"), strict=False))
    dp = values.get("DP", ".")
    depth = int(dp) if dp.isdecimal() else 0
    ad = values.get("AD", ".").split(",")
    if (
        blocked
        or fields[3].upper() not in {"A", "C", "G", "T"}
        or (depth and (not ad[0].isdecimal() or int(ad[0]) != depth))
    ):
        return evidence.State.NO_CALL, depth
    point = [*fields[:4], ".", ".", "PASS", ".", "GT:DP", f"0/0:{depth}"]
    return evidence.record_state(point, evidence.DEPTH_ONLY, thresholds), depth
