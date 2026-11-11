"""The reference data a command line needs and the library lacks.

``wgsextract ref needs`` answers this before a command runs, so the desktop
app can offer to install a missing reference genome or annotation file
instead of starting a run that would fail (or, like ``info`` on a CRAM, finish
without the read-based numbers).

A command needs the reference genome its BAM/CRAM ``--input`` was aligned to
when it decodes a CRAM's reads, or when it calls variants or otherwise reads
the reference sequence (``GENOME_COMMANDS``). The genome is named from the
input header's sequence-dictionary signature and the reference catalog, which
also gives the code ``ref library --install`` takes. VCF annotation commands
need their annotation data file (``ANNOTATION_COMMANDS``).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.reference_assets import fasta_signature, is_fasta_path

# Commands that read the reference sequence even for a BAM: variant calling,
# consensus sequences, typing and the like.
GENOME_COMMANDS = frozenset(
    {
        "analyze comprehensive",
        "bam to-cram",
        "extract mito-fasta",
        "extract mito-vcf",
        "extract ydna-vcf",
        "methylation pileup",
        "microarray",
        "qc contamination",
        "typing cyp2d6",
        "typing str",
        "vcf clair3",
        "vcf cnv",
        "vcf deepvariant",
        "vcf freebayes",
        "vcf gatk",
        "vcf indel",
        "vcf phase",
        "vcf snp",
        "vcf sv",
    }
)

# Commands whose --input is a BAM/CRAM they read only the header or index of,
# or not at all, so a CRAM's reference isn't needed.
HEADER_ONLY_COMMANDS = frozenset(
    {
        "bam identify",
        "bam index",
        "bam unindex",
        "sample show",
        "sample set",
        "sample unset",
        "report",
    }
)


@dataclass(frozen=True)
class Annotation:
    attribute: str  # ReferenceLibrary attribute holding the installed file
    option: str  # the command's own option naming a file instead
    resource: str  # `wgsextract ref <resource>` installs it
    name: str  # shown to people
    per_build: bool = False  # the install takes --build
    builds: tuple[str, ...] = ("hg19", "hg38")


ANNOTATION_COMMANDS = {
    "vcf clinvar": Annotation("clinvar_vcf", "clinvar_file", "clinvar", "ClinVar"),
    "vcf revel": Annotation("revel_file", "revel_file", "revel", "REVEL"),
    "vcf cadd": Annotation("cadd_file", "cadd_file", "cadd", "CADD", per_build=True),
    "vcf gerp": Annotation("gerp_file", "gerp_file", "gerp", "GERP", per_build=True),
    "vcf phylop": Annotation(
        "phylop_file", "phylop_file", "phylop", "PhyloP", per_build=True
    ),
    "vcf gnomad": Annotation("gnomad_vcf", "gnomad_file", "gnomad", "gnomAD"),
    "vcf spliceai": Annotation(
        "spliceai_vcf", "spliceai_file", "spliceai", "SpliceAI", builds=("hg38",)
    ),
    "vcf alphamissense": Annotation(
        "alphamissense_vcf", "am_file", "alphamissense", "AlphaMissense"
    ),
    "vcf pharmgkb": Annotation(
        "pharmgkb_vcf", "pharmgkb_file", "pharmgkb", "PharmGKB", builds=("hg38",)
    ),
}


@dataclass(frozen=True)
class MissingReference:
    """Reference data the command needs that isn't installed."""

    id: str  # catalog code or annotation resource, e.g. "hs37d5", "clinvar"
    kind: str  # "genome" or "annotation"
    name: str  # e.g. "hs37d5", "ClinVar"
    reason: str
    # The `wgsextract` arguments that install it, or None when nothing can.
    install_arguments: list[str] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "reason": self.reason,
            "installArguments": self.install_arguments,
        }


def missing_references(args: argparse.Namespace) -> list[MissingReference]:
    """What the parsed command line ``args`` needs and the library lacks."""
    command = str(getattr(args, "_command", "") or "")
    missing: list[MissingReference] = []
    genome = _missing_genome(command, args)
    if genome:
        missing.append(genome)
    annotation = _missing_annotation(command, args)
    if annotation:
        missing.append(annotation)
    return missing


def _library_option(ref: str | None) -> list[str]:
    """``--ref`` naming the library a command looks in, for its install."""
    if ref and Path(ref).is_dir():
        return ["--ref", ref]
    return []


def _given_path(args: argparse.Namespace, dest: str) -> str | None:
    value = getattr(args, dest, None)
    return expand_path(value) if value else None


def _needs_genome(command: str, args: argparse.Namespace, input_path: str) -> str:
    """Why ``command`` needs the input's reference genome, or ''."""
    if command == "extract region":
        formats = str(getattr(args, "formats", "") or "").lower()
        if getattr(args, "ybrowse", False) or "fasta" in formats or "vcf" in formats:
            return "variant calling"
    if command in GENOME_COMMANDS:
        return "variant calling" if command.startswith("vcf ") else command
    if input_path.lower().endswith(".cram") and command not in HEADER_ONLY_COMMANDS:
        return "reading the CRAM"
    return ""


def _missing_genome(command: str, args: argparse.Namespace) -> MissingReference | None:
    input_path = _given_path(args, "input")
    if (
        not input_path
        or not input_path.lower().endswith((".bam", ".cram"))
        or not Path(input_path).is_file()
        or command.split()[0] in {"ref", "help", "deps", "serve"}
    ):
        return None
    reason = _needs_genome(command, args, input_path)
    if not reason:
        return None
    # realign and align name the target genome with --ref; the input's own
    # reference is --input-ref.
    ref = _given_path(args, "input_ref" if hasattr(args, "input_ref") else "ref")
    if ref and Path(ref).is_file() and is_fasta_path(ref):
        return None  # A FASTA the user chose is theirs to vouch for.
    library = ref if ref and Path(ref).is_dir() else None

    from wgsextract_cli.core.alignment_metadata import get_bam_header
    from wgsextract_cli.core.ref_library.catalog import catalog_genome_for
    from wgsextract_cli.core.reference_models import identify_reference
    from wgsextract_cli.core.reference_resolver import ReferenceLibrary
    from wgsextract_cli.core.variant_files import calculate_bam_md5

    header = get_bam_header(input_path) or ""
    if not header:
        return None  # The command itself reports an unreadable input.
    signature = calculate_bam_md5(input_path, header=header)
    lib = ReferenceLibrary(
        library, signature, skip_full_search=True, input_path=input_path
    )
    fasta = lib.fasta if lib.fasta and Path(lib.fasta).is_file() else None
    genome = catalog_genome_for(header, signature)
    if fasta and (
        # Decoding a CRAM takes the very sequences it was written against.
        not input_path.lower().endswith(".cram")
        or not genome
        or fasta_signature(fasta) == signature
    ):
        return None
    if genome:
        return MissingReference(
            id=str(genome["code"]),
            kind="genome",
            name=str(genome["code"]),
            reason=reason,
            install_arguments=[
                "ref",
                "library",
                "--install",
                str(genome["code"]),
                *_library_option(library),
            ],
        )
    model = identify_reference(header, signature)
    name = model.name if model else "matching"
    return MissingReference(
        id=name, kind="genome", name=name, reason=reason, install_arguments=None
    )


def _missing_annotation(
    command: str, args: argparse.Namespace
) -> MissingReference | None:
    annotation = ANNOTATION_COMMANDS.get(command)
    vcf = _given_path(args, "vcf_input")
    if not annotation or not vcf or not Path(vcf).is_file():
        return None
    if getattr(args, annotation.option, None):
        return None
    from wgsextract_cli.core.builds import canonical_build
    from wgsextract_cli.core.reference_resolver import ReferenceLibrary

    ref = _given_path(args, "ref")
    lib = ReferenceLibrary(ref, None, input_path=vcf)
    if getattr(lib, annotation.attribute, None):
        return None
    build = canonical_build(lib.build) if lib.build else None
    if build not in annotation.builds:
        return None  # The command explains the build it can't annotate.
    install = ["ref", annotation.resource, *_library_option(lib.root)]
    if annotation.per_build:
        install += ["--build", build]
    return MissingReference(
        id=annotation.resource,
        kind="annotation",
        name=annotation.name,
        reason=command,
        install_arguments=install,
    )
