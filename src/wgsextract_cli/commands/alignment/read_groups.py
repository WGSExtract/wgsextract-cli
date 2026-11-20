"""Read-group construction and retained sequencer metadata for alignment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wgsextract_cli.core import ultima
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.genome_library import pair_stem

if TYPE_CHECKING:
    from ..align import AlignJob

# SAM-spec @RG PL values for --platform.
_RG_PLATFORMS = {
    "illumina": "ILLUMINA",
    "dnbseq": "DNBSEQ",
    "pacbio": "PACBIO",
    "hifi": "PACBIO",
    "clr": "PACBIO",
    "ont": "ONT",
    "nanopore": "ONT",
    "ultima": ultima.PLATFORM,
}


def read_group(
    stem: str,
    sample: str | None = None,
    platform: str | None = None,
    description: str = "",
) -> str:
    """The @RG line for -R (bwa, minimap2 expand the \\t). Variant callers and
    multi-sample tools need each sample's own SM, and GATK requires a read group
    with a platform. The default sample is the reads' name without its mate
    marker (family.mother_R1 -> family.mother), so different people stay apart."""
    sample = sample or pair_stem(stem)
    platform = _RG_PLATFORMS.get(platform or "", "ILLUMINA")
    line = f"@RG\\tID:{stem}\\tSM:{sample}\\tLB:{sample}\\tPL:{platform}"
    return line + (f"\\tDS:{description}" if description else "")


def read_group_descriptions(reads: str, input_ref: str | None) -> str:
    """The DS of a BAM/CRAM's read groups, which name the basecaller models
    (Dorado's basecall_model=..., that Clair3's model choice reads) or PacBio's
    READTYPE; several distinct ones are all kept."""
    descriptions = dict.fromkeys(
        group["DS"]
        for group in ultima.read_groups(get_bam_header(reads, input_ref))
        if group.get("DS")
    )
    return " ".join(descriptions)


def ultima_read_groups(header: str, sample: str | None, stem: str) -> list[str]:
    """The @RG lines of an Ultima BAM/CRAM for the realigned output, which keeps
    each read's RG: the flow order (FO) and max homopolymer class (mc) GATK
    needs live there. Output flow tags are in reference orientation (tp:)."""
    lines = []
    for rg in ultima.read_groups(header):
        rg.setdefault("SM", sample or pair_stem(stem))
        if sample:
            rg["SM"] = sample
        rg.setdefault("PL", ultima.PLATFORM)
        rg["tp"] = "reference"
        lines.append("\t".join(["@RG", *(f"{k}:{v}" for k, v in rg.items())]))
    return lines


def _ultima_read_group(job: AlignJob, stem: str, tagged: bool) -> str:
    """read_group's line, plus the flow order GATK needs for tagged reads."""
    rg = read_group(stem, job.sample, job.platform)
    return rg + f"\\tFO:{ultima.DEFAULT_FLOW_ORDER}\\ttp:reference" if tagged else rg
