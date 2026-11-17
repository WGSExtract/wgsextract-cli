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
from wgsextract_cli.core.reference_assets import is_fasta_path, read_fai_lengths

# Commands that read the reference sequence even for a BAM: variant calling,
# consensus sequences, typing and the like.
GENOME_COMMANDS = frozenset(
    {
        "analyze comprehensive",
        "bam to-cram",
        "extract mito-fasta",
        "extract mito-vcf",
        "extract ydna-vcf",
        "lineage mt-haplogroup",
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


def _library_option(library: str | None) -> list[str]:
    """``--ref`` naming the library a command looks in, for its install. A
    library folder that doesn't exist yet is kept: the install creates it,
    and that is where the command will look."""
    if library and not is_fasta_path(library):
        return ["--ref", library]
    return []


def _explicit_library(ref: str | None) -> str | None:
    """The library folder an explicit ``--ref`` names (existing or not), or
    None for no ``--ref`` or a FASTA."""
    return ref if ref and not is_fasta_path(ref) else None


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


def _reads_vcf(command: str, args: argparse.Namespace) -> bool:
    """Whether ``command`` reads a VCF rather than its BAM/CRAM ``--input``:
    a VCF command (it has --vcf-input) that doesn't call variants."""
    return hasattr(args, "vcf_input") and command not in GENOME_COMMANDS


def _selected_vcf(args: argparse.Namespace) -> str | None:
    """The VCF a VCF command reads, chosen as its handler chooses it
    (``vcf_input_handler``): --vcf-input or config default_input_vcf, or
    --input."""
    from wgsextract_cli.commands.vcf.basic import _select_vcf_input
    from wgsextract_cli.core.config import settings

    view = argparse.Namespace(**vars(args))
    explicit: set[str] = getattr(args, "_explicit_dests", set())
    if getattr(view, "vcf_input", None) is None and "vcf_input" not in explicit:
        view.vcf_input = settings.get("default_input_vcf")
    selected = _select_vcf_input(view)
    return expand_path(selected) if selected else None


def _fits_bam(fasta: str, header: str) -> bool:
    """Whether every @SQ of a BAM header is in ``fasta`` with the same length
    (from its .fai; a FASTA without one is given the benefit of the doubt)."""
    from wgsextract_cli.core.reference_assets import header_sequences

    lengths = read_fai_lengths(fasta)
    if not lengths:
        return True
    return all(
        str(lengths.get(sq.get("SN", ""), "")) == sq.get("LN")
        for sq in header_sequences(header)
    )


def _usable_fasta(fasta: str | None, input_path: str, header: str) -> bool:
    """Whether the command can use ``fasta`` with this input: decoding a CRAM
    takes the very sequences it was written against (names, lengths and the
    recorded M5 checksums); a BAM needs its contigs in the FASTA."""
    if not fasta or not Path(fasta).is_file():
        return False
    if input_path.lower().endswith(".cram"):
        from wgsextract_cli.core.alignment_metadata import cram_encoded_with

        return cram_encoded_with(fasta, input_path)
    return _fits_bam(fasta, header)


def _missing_genome(command: str, args: argparse.Namespace) -> MissingReference | None:
    input_path = _given_path(args, "input")
    if (
        not input_path
        or not input_path.lower().endswith((".bam", ".cram"))
        or not Path(input_path).is_file()
        or command.split()[0] in {"ref", "help", "deps", "serve"}
        or _reads_vcf(command, args)
    ):
        return None
    reason = _needs_genome(command, args, input_path)
    if not reason:
        return None
    # realign and align name the target genome with --ref; the input's own
    # reference is --input-ref.
    realigns = hasattr(args, "input_ref")
    ref = _given_path(args, "input_ref" if realigns else "ref")
    if ref and is_fasta_path(ref):
        # A FASTA the user chose is theirs to vouch for; a missing one is the
        # command's own error, which nothing here installs.
        return None
    library = _explicit_library(ref)

    from wgsextract_cli.core.alignment_metadata import get_bam_header
    from wgsextract_cli.core.ref_library.catalog import catalog_genome_for
    from wgsextract_cli.core.reference_models import identify_reference
    from wgsextract_cli.core.reference_resolver import ReferenceLibrary
    from wgsextract_cli.core.variant_files import calculate_bam_md5

    header = get_bam_header(input_path) or ""
    if not header:
        return None  # The command itself reports an unreadable input.
    target = _given_path(args, "ref") if realigns else None
    if target and _usable_fasta(target, input_path, header):
        return None  # realign decodes with a --ref target that fits the input.
    signature = calculate_bam_md5(input_path, header=header)
    lib = ReferenceLibrary(
        library, signature, skip_full_search=True, input_path=input_path
    )
    if _usable_fasta(lib.fasta, input_path, header):
        return None
    genome = catalog_genome_for(header, signature)
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
    if not annotation:
        return None
    vcf = _selected_vcf(args)
    if not vcf or not Path(vcf).is_file():
        return None
    if getattr(args, annotation.option, None):
        return None
    from wgsextract_cli.core.builds import canonical_build
    from wgsextract_cli.core.reference_resolver import ReferenceLibrary

    ref = _given_path(args, "ref")
    if ref and is_fasta_path(ref) and not Path(ref).is_file():
        return None  # The command reports the missing FASTA itself.
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
