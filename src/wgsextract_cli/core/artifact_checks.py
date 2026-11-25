"""Consumers refusing BAM/CRAM/VCF/BCF inputs they can't use, before the
scientific tool starts, with a stable ``code`` on the error (#1182)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from wgsextract_cli.core.artifact_facts import (
    GENOMIC_KINDS,
    FileFacts,
    file_facts,
)
from wgsextract_cli.core.artifacts import recorded_producer
from wgsextract_cli.core.errors import CodedError


class ArtifactIncompatible(CodedError):
    """An input a consumer can't use; ``code`` names the problem."""


_FIXES = {
    "stale": "it is older than the file. Rebuild it ({tool}) or remove it",
    "mismatched": "it doesn't describe this file's sequences (an index of "
    "another file?). Rebuild it ({tool}) or remove it",
    "missing": "this run needs one. Create it ({tool})",
}


def _index_tool(path: str, kind: str) -> str:
    tool = {"bam": "samtools index", "cram": "samtools index", "bcf": "bcftools index"}
    return f"{tool.get(kind, 'tabix -p vcf')} {path}"


def require_consumable(
    inputs: Iterable[tuple[str, str]],
    *,
    reference: str | None = None,
    need_index: Iterable[str] = (),
    refresh_vcf_index: bool = True,
    same_sample: bool = False,
) -> None:
    """Refuse inputs a consumer can't use before it runs.

    ``inputs`` are (role, path) pairs of BAM/CRAM/VCF/BCF files. Checked:
    a stale or mismatched index (a missing one only for ``need_index``
    roles; with ``refresh_vcf_index`` a VCF's missing or stale index is left
    for the consumer to rebuild, as it does); the files' assemblies against
    each other and ``reference``; a VCF's contig lengths against
    ``reference``; and, with ``same_sample``, that every VCF names a sample
    of the reads. Unknown facts (a CRAM's header, a FASTA without .fai)
    aren't held against an input."""
    from wgsextract_cli.core.results import file_kind

    required = set(need_index)
    reads_samples: set[str] = set()
    facts: list[tuple[str, str, str, FileFacts]] = []
    for role, path in inputs:
        kind = file_kind(path)
        if kind not in GENOMIC_KINDS or not Path(path).is_file():
            continue
        found = file_facts(path, kind)
        facts.append((role, path, kind, found))
        if kind in ("bam", "cram"):
            reads_samples.update(found.samples)
        index = found.index
        if index is None or index.state == "ready":
            continue
        vcf = kind in ("vcf", "bcf")
        if index.state == "missing" and role not in required:
            continue
        if vcf and refresh_vcf_index and index.state in ("missing", "stale"):
            continue
        code = {"missing": "index_missing", "stale": "index_stale"}.get(
            index.state, "index_mismatch"
        )
        where = index.path or "no index"
        raise ArtifactIncompatible(
            code,
            f"Can't use {path} ({role}): its index ({where}) is {index.state}; "
            + _FIXES[index.state].format(tool=_index_tool(path, kind))
            + ", then rerun.",
        )
    _require_same_assembly(facts, reference)
    if same_sample and reads_samples:
        for role, path, kind, found in facts:
            if kind in ("vcf", "bcf") and found.samples:
                if not reads_samples & set(found.samples):
                    _sample_mismatch(role, path, found.samples, reads_samples)


def _sample_mismatch(
    role: str, path: str, samples: Sequence[str], reads: set[str]
) -> None:
    """Refuse a VCF wgsextract called from reads (``called_from``: directly,
    or filtered or annotated from such calls with their samples kept), whose
    sample column is their @RG SM, naming another sample than these reads;
    only warn for any other VCF, whose sample may just be named differently
    (a lab ID, a caller that names it after the file)."""
    from wgsextract_cli.core.results import FileRecord, warn

    message = (
        f"{path} ({role}) holds samples {', '.join(samples[:5])}, but the reads "
        f"are {', '.join(sorted(reads))}"
    )
    recorded = recorded_producer(FileRecord.describe(path, role))
    if recorded is not None and recorded.called_from:
        raise ArtifactIncompatible(
            "sample_mismatch",
            message + ": they were called from another individual's reads. "
            "Use the VCF called from these reads.",
        )
    warn(
        "sample_mismatch",
        message + "; check that they are the same individual.",
        path=path,
    )


def reference_assembly(reference: str) -> str | None:
    from wgsextract_cli.core.ref_library.assets import fasta_build

    try:
        return fasta_build(reference)
    except (OSError, ValueError):
        return None


def _require_same_assembly(
    facts: Sequence[tuple[str, str, str, FileFacts]], reference: str | None
) -> None:
    from wgsextract_cli.core.ref_library.assets import read_fai_lengths
    from wgsextract_cli.core.variant_files import match_chromosome_name

    expected = reference_assembly(reference) if reference else None
    label = f"the reference {reference}" if expected else None
    for role, path, _kind, found in facts:
        if found.assembly is None:
            continue
        if expected is None:
            expected, label = found.assembly, f"{path} ({role})"
        elif found.assembly != expected:
            raise ArtifactIncompatible(
                "assembly_mismatch",
                f"{path} ({role}) is on {found.assembly}, but {label} is on "
                f"{expected}. Use files from one genome build.",
            )
    lengths = read_fai_lengths(reference) if reference else {}
    if not lengths:
        return
    for role, path, kind, found in facts:
        if kind not in ("vcf", "bcf") or not found.lengths:
            continue
        differ = [
            name
            for name, length in found.lengths.items()
            if (other := match_chromosome_name(name, list(lengths)))
            and lengths[other] != length
        ]
        if differ:
            raise ArtifactIncompatible(
                "reference_mismatch",
                f"{path} ({role}) was made against another reference than "
                f"{reference}: contig lengths differ for {', '.join(differ[:5])}. "
                "Use the FASTA it was called with.",
            )
