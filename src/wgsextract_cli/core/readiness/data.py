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
from wgsextract_cli.core.paths import append_suffix, expand_path
from wgsextract_cli.core.ref_library.assets import fasta_build, read_fai_lengths
from wgsextract_cli.core.ref_library.needs import (
    AnnotationNeed,
    GenomeNeed,
    annotation_need,
    genome_need,
    reads_vcf,
    selected_vcf,
    usable_fasta,
)
from wgsextract_cli.core.remote_input import is_url

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


def data_items(command: str, args: argparse.Namespace) -> list[Item]:
    """The input, reference and annotation items of a command line."""
    items = input_items(command, args)
    genome = genome_need(command, args)
    if genome:
        items += reference_items(genome)
    annotation = annotation_need(command, args)
    if annotation:
        items.append(annotation_item(annotation))
    return items


def input_items(command: str, args: argparse.Namespace) -> list[Item]:
    """The input the command reads: a VCF for a VCF command that doesn't
    call variants, else its BAM/CRAM ``--input``."""
    raw = getattr(args, "input", None)
    if is_url(raw):
        return [
            Item(
                "input",
                str(raw),
                UNSUPPORTED,
                detail="a remote input is read over the network",
                fix_text="Download it, then give the local file to run offline.",
            )
        ]
    if reads_vcf(command, args):
        vcf = selected_vcf(args)
        return _vcf_items(vcf) if vcf else []
    path = expand_path(raw) if raw else None
    if not path or not path.lower().endswith((".bam", ".cram")):
        return []
    return _alignment_items(path)


def _alignment_items(path: str) -> list[Item]:
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
    return [found, _index_item(Path(path), Path(index), fix_arguments=fix)]


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


def reference_items(need: GenomeNeed) -> list[Item]:
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
    if not read_fai_lengths(fasta):
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


def fasta_index_items(fasta: Path) -> list[Item]:
    """A FASTA's .fai, and the .gzi a BGZF-compressed one also needs."""
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
            continue
        items.append(
            Item(
                "index",
                index.name,
                MISSING,
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
