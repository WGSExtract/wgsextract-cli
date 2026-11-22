"""The data a command line reads, for ``deps ready``: its BAM/CRAM or VCF
input and their indexes, the reference genome and its indexes, and
annotation data. What the command needs is decided as ``ref needs`` decides
it (core/ref_library/needs.py); a missing genome or annotation names the
explicit download that installs it, which only runs when asked for.

An index older than its file is stale: htslib warns of it and reads it
anyway, so a BAM's or FASTA's stale index blocks a run, while a VCF's is
rebuilt by the run itself (``ensure_vcf_indexed``) and is only reported.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.alignment.samtools_commands import alignment_index
from wgsextract_cli.core.bgzf import is_bgzf
from wgsextract_cli.core.calling_reference import validate_calling_reference
from wgsextract_cli.core.paths import append_suffix, expand_path
from wgsextract_cli.core.ref_library.assets import fasta_build, read_fai_lengths
from wgsextract_cli.core.ref_library.needs import (
    HEADER_ONLY_COMMANDS,
    AnnotationNeed,
    GenomeNeed,
    annotation_need,
    genome_need,
    reads_vcf,
    selected_vcf,
    usable_fasta,
)
from wgsextract_cli.core.remote_input import is_url
from wgsextract_cli.core.utils import WGSExtractError

from .model import (
    INCOMPATIBLE,
    INSTALLED,
    MISSING,
    RUNNABLE,
    STALE,
    UNSUPPORTED,
    UNVERIFIED,
    Item,
)

# Commands that accept a reference holding contigs their input lacks
# (vcf/structural.py calls setup_caller with allow_reference_subset).
SUBSET_REFERENCE_COMMANDS = frozenset({"vcf cnv", "vcf sv"})
# Fields naming files the command writes, not reads.
OUTPUT_FIELDS = frozenset({"out", "output"})


def data_items(command: str, args: argparse.Namespace) -> list[Item]:
    """The input, reference and annotation items of a command line, then
    every other file its options name."""
    items = input_items(command, args)
    genome = genome_need(command, args)
    if genome:
        items += reference_items(genome, command)
    annotation = annotation_need(command, args)
    if annotation:
        items += [annotation_item(annotation), *_annotation_reference(annotation)]
    reported = {i.path for i in items} | {i.name for i in items}
    return items + given_file_items(command, args, reported)


def _remote(location: str) -> Item:
    return Item(
        "input",
        location,
        UNSUPPORTED,
        detail="a remote input is read over the network",
        fix_text="Download it, then give the local file to run offline.",
    )


def input_items(command: str, args: argparse.Namespace) -> list[Item]:
    """The input the command reads: a VCF for a VCF command that doesn't
    call variants (--vcf-input before --input, as its handler chooses),
    else its BAM/CRAM ``--input``."""
    if reads_vcf(command, args):
        vcf = selected_vcf(args)
        if is_url(vcf):
            return [_remote(str(vcf))]
        return _vcf_items(vcf) if vcf else []
    raw = getattr(args, "input", None)
    if is_url(raw):
        return [_remote(str(raw))]
    path = expand_path(raw) if raw else None
    if not path or not path.lower().endswith((".bam", ".cram")):
        return []
    return _alignment_items(path, command)


def given_file_items(
    command: str, args: argparse.Namespace, reported: set[str | None]
) -> list[Item]:
    """The files the command's other options name (FASTQ mates, catalogs,
    BEDs, ...), which must exist; the reference is the reference items'."""
    from wgsextract_cli.api._cli import command_params
    from wgsextract_cli.api._params import param_fields

    cls = command_params(command)
    if cls is None:
        return []
    # A VCF command reads the one VCF its handler selects (input_items).
    chosen = {"input", "vcf_input"} if reads_vcf(command, args) else set()
    items = []
    for field in param_fields(cls):
        if (
            field.spec.path not in ("file", "file_or_directory")
            or field.name in OUTPUT_FIELDS
            or field.name in ("ref", "input_ref", *chosen)
        ):
            continue
        value = getattr(args, field.dest, None)
        values = value if isinstance(value, (list, tuple)) else [value]
        for given in (str(v) for v in values if v):
            if is_url(given):
                if given not in reported:
                    items.append(_remote(given))
                    reported.add(given)
                continue
            path = expand_path(given)
            if path in reported:
                continue
            reported.add(path)
            flag = field.flags[0]
            if Path(path).exists():
                items.append(Item("input", path, INSTALLED, detail=flag, path=path))
            else:
                items.append(
                    Item(
                        "input",
                        path,
                        MISSING,
                        detail=f"{flag} names a file that doesn't exist",
                        path=path,
                    )
                )
    return items


def _alignment_items(path: str, command: str) -> list[Item]:
    if not Path(path).is_file():
        return [Item("input", path, MISSING, detail="doesn't exist", path=path)]
    if not get_bam_header(path):
        return [
            Item(
                "input",
                path,
                INCOMPATIBLE,
                detail="samtools can't read its header",
                path=path,
            )
        ]
    kind = "CRAM" if path.lower().endswith(".cram") else "BAM"
    found = Item("input", path, RUNNABLE, detail=f"{kind} header read", path=path)
    fix = ["bam", "index", "--input", path]
    index = alignment_index(path)
    # These read only the header (bam index rebuilds the index): theirs is
    # only reported.
    reads_index = command not in HEADER_ONLY_COMMANDS
    if index is None:
        missing = Item(
            "index",
            f"{Path(path).name} index",
            MISSING,
            required=False,
            detail="steps that read it by region stop without one",
            fix_arguments=fix,
        )
        return [found, missing]
    item = _index_item(Path(path), Path(index), fix_arguments=fix)
    item.required = reads_index
    return [found, item]


def _vcf_items(vcf: str) -> list[Item]:
    path = Path(vcf)
    if not path.is_file():
        return [Item("input", vcf, MISSING, detail="doesn't exist", path=vcf)]
    items = [Item("input", vcf, INSTALLED, detail="VCF", path=vcf)]
    if path.suffix not in {".gz", ".bgz"}:
        return items
    existing = [
        index
        for ext in (".tbi", ".csi")
        for index in (append_suffix(path, ext), path.with_suffix(ext))
        if index.is_file()
    ]
    rebuilt = "the run indexes it itself, which needs write access beside it"
    if not existing:
        items.append(
            Item("index", f"{path.name} index", MISSING, required=False, detail=rebuilt)
        )
        return items
    # The run refreshes every index when any is older than the VCF.
    oldest = min(existing, key=lambda index: index.stat().st_mtime_ns)
    item = _index_item(path, oldest)
    if item.status == STALE:
        item.required, item.detail = False, f"{item.detail}; {rebuilt}"
    items.append(item)
    return items


def _index_item(
    data: Path,
    index: Path,
    *,
    fix_arguments: list[str] | None = None,
    fix_text: str | None = None,
) -> Item:
    """``index`` of ``data``: stale when older than it."""
    if index.stat().st_mtime_ns < data.stat().st_mtime_ns:
        return Item(
            "index",
            index.name,
            STALE,
            detail=f"older than {data.name}, so it may not describe it",
            path=str(index),
            fix_arguments=fix_arguments,
            fix_text=fix_text,
        )
    return Item(
        "index", index.name, RUNNABLE, detail="newer than its file", path=str(index)
    )


def reference_items(need: GenomeNeed, command: str) -> list[Item]:
    """The reference genome a BAM/CRAM is read with, and its indexes."""
    if need.missing:
        mismatched = bool(need.fasta and Path(need.fasta).is_file())
        return [
            Item(
                "reference",
                need.missing.name,
                MISSING,
                detail=f"needed for {need.reason}"
                + (f"; {need.fasta} doesn't match the input" if mismatched else ""),
                evidence={"reason": need.reason},
                fix_arguments=need.missing.install_arguments,
                fix_text=None
                if need.missing.install_arguments
                else "No catalog genome matches the input: give the FASTA it was "
                "aligned to with --ref.",
            )
        ]
    fasta = need.fasta
    if not fasta or not Path(fasta).is_file():
        return [
            Item(
                "reference",
                fasta or "reference genome",
                MISSING,
                detail=f"needed for {need.reason}; the FASTA doesn't exist",
                path=fasta,
            )
        ]
    item = Item(
        "reference",
        Path(fasta).name,
        RUNNABLE,
        path=fasta,
        evidence={
            "reason": need.reason,
            "chosen": need.chosen,
            "build": fasta_build(fasta),
        },
    )
    ordinary_gzip = _ordinary_gzip(fasta)
    has_fai = bool(read_fai_lengths(fasta))
    if need.reason == "variant calling" and (has_fai or ordinary_gzip):
        # The callers' own check (setup_caller), which also compares the M5
        # checksums both dictionaries record. It writes only a missing .fai.
        try:
            validate_calling_reference(
                need.input,
                fasta,
                allow_reference_subset=command in SUBSET_REFERENCE_COMMANDS,
            )
            item.detail = "its sequences match the input's"
        except WGSExtractError as error:
            item.status, item.detail = INCOMPATIBLE, str(error)
    elif not has_fai and not ordinary_gzip:
        item.status = UNVERIFIED
        item.detail = "no .fai to compare its sequences with the input's"
    elif usable_fasta(fasta, need.input, get_bam_header(need.input)):
        item.detail = "its sequences match the input's"
    else:
        item.status = INCOMPATIBLE
        item.detail = (
            "its sequences don't match the input's: it isn't the reference the "
            "input was aligned to"
        )
    return [item, *fasta_index_items(Path(fasta))]


def _ordinary_gzip(fasta: str) -> bool:
    return fasta.lower().endswith(".gz") and not is_bgzf(fasta)


def fasta_index_items(fasta: Path, *, required: bool = True) -> list[Item]:
    """A FASTA's .fai, and the .gzi a BGZF-compressed one also needs. An
    ordinary gzip FASTA can't be indexed in place: callers that decompress
    it (plain_reference) make a temporary copy, so it is only reported."""
    if _ordinary_gzip(str(fasta)):
        return [
            Item(
                "index",
                f"{fasta.name}.fai",
                UNVERIFIED,
                required=False,
                detail="ordinary gzip can't be indexed in place: callers that "
                "decompress it make a temporary copy; tools that read it by "
                "region need it recompressed with bgzip",
                fix_text=f"Recompress it: zcat {fasta} | bgzip > a new .fa.gz",
            )
        ]
    suffixes = [".fai", *([".gzi"] if is_bgzf(str(fasta)) else [])]
    fix = ["ref", "verify", "--ref", str(fasta)]
    items = []
    for suffix in suffixes:
        index = append_suffix(fasta, suffix)
        if index.is_file():
            items.append(
                _index_item(
                    fasta,
                    index,
                    fix_text=f"Delete {index}, then run: wgsextract {' '.join(fix)}",
                )
            )
            items[-1].required = required
            continue
        items.append(
            Item(
                "index",
                index.name,
                MISSING,
                required=required,
                detail=f"tools read {fasta.name} by region through it",
                path=str(index),
                fix_arguments=fix,
            )
        )
    return items


def annotation_item(need: AnnotationNeed) -> Item:
    """The annotation data a VCF command reads."""
    name = need.annotation.name
    if need.missing:
        return Item(
            "annotation",
            name,
            MISSING,
            detail=f"needed by {need.missing.reason}",
            fix_arguments=need.missing.install_arguments,
        )
    if not need.path or not Path(need.path).is_file():
        return Item("annotation", name, MISSING, detail="doesn't exist", path=need.path)
    return Item("annotation", name, INSTALLED, path=need.path)


def _annotation_reference(need: AnnotationNeed) -> list[Item]:
    """The reference FASTA an annotator normalises the VCF with: required by
    those that refuse to run without one."""
    required = need.annotation.requires_fasta
    if need.fasta and Path(need.fasta).is_file():
        found = Item(
            "reference",
            Path(need.fasta).name,
            INSTALLED,
            required=required,
            detail="the VCF's build's reference",
            path=need.fasta,
            evidence={"build": need.build},
        )
        return [found, *fasta_index_items(Path(need.fasta), required=required)]
    build = need.build or "the VCF's build"
    return [
        Item(
            "reference",
            "reference genome",
            MISSING,
            required=required,
            detail=f"{need.annotation.name} matching "
            + ("needs" if required else "uses, when present,")
            + f" the reference FASTA of {build}",
            evidence={"build": need.build},
            fix_text=f"Give the FASTA of {build} with --ref, or install the "
            "genome (`wgsextract ref library --list` names them).",
        )
    ]
