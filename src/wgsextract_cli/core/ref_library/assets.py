import json
import os
import re
from collections.abc import Iterable
from fnmatch import fnmatch
from os import fspath
from pathlib import Path
from typing import Any

from wgsextract_cli.core.builds import (
    BUILD_PATH_TOKENS,
    build_from_chr1_length,
    build_from_path,
    canonical_build,
)
from wgsextract_cli.core.paths import (
    PathInput,
    absolute_path,
    append_suffix,
    expand_path,
)
from wgsextract_cli.core.ref_library.layout import download_dirs

FASTA_EXTENSIONS = (".fa", ".fasta", ".fna", ".fa.gz", ".fasta.gz", ".fna.gz")
FASTA_SUFFIX_RE = re.compile(r"\.(fa|fasta|fna)(\.gz)?$", re.IGNORECASE)
# Genome-wide SNP targets. Not the bootstrap's snps_<build>.vcf.gz: it holds only
# Y-chromosome SNPs (for Y-DNA work), so a microarray kit made from it is empty.
TARGET_PATTERNS = (
    "All_SNPs*.tab.gz",
    "All_SNPs*.vcf.gz",
    "common_all.vcf.gz",
)


def build_hint(*values: PathInput | None) -> str:
    """Infer a build from file/folder names, never their ancestor directories."""
    for value in values:
        if not value:
            continue
        build = build_from_path(Path(value).name)
        if build:
            return build
    return ""


def is_fasta_path(path: PathInput) -> bool:
    return fspath(path).lower().endswith(FASTA_EXTENSIONS)


def fasta_prefix(fasta: PathInput) -> str:
    """Strip the FASTA extension: ref/hg38.fa.gz -> ref/hg38."""
    return FASTA_SUFFIX_RE.sub("", fspath(fasta))


# Files made from a genome's FASTA beside it: download state, indexes, aligner indexes.
GENOME_SIDECARS = (
    ".partial", ".partial.source", ".partial.segments",
    ".fai", ".gzi", ".dict", ".alt",
    ".amb", ".ann", ".bwt", ".pac", ".sa", ".bwa.pending", ".bwa.complete",
    ".0123", ".bwt.2bit.64", ".bwa-mem2.pending", ".bwa-mem2.complete",
)  # fmt: skip


def reference_dict_path(fasta: PathInput) -> str:
    """Sequence dictionary path GATK/Picard expect: hg38.fa.gz -> hg38.dict."""
    return fasta_prefix(fasta) + ".dict"


def header_sequences(header: str) -> list[dict[str, str]]:
    """The tags (SN, LN, M5, ...) of each @SQ line in a SAM header or .dict."""
    return [
        dict(f.split(":", 1) for f in line.split("\t")[1:] if ":" in f)
        for line in header.splitlines()
        if line.startswith("@SQ")
    ]


def sequence_dictionary(fasta: PathInput) -> list[dict[str, str]] | None:
    """A FASTA's @SQ tags from its .dict, else from samtools dict (which reads
    the whole FASTA to compute each sequence's M5); None if neither works."""
    import subprocess

    from wgsextract_cli.core.utils import run_command

    dict_path = Path(reference_dict_path(fasta))
    try:
        if dict_path.is_file():
            text = dict_path.read_text()
        else:
            cmd = ["samtools", "dict", fspath(fasta)]
            text = run_command(cmd, capture_output=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return header_sequences(text) or None


def n_count_paths(fasta: PathInput, outdir: PathInput | None = None) -> tuple[str, str]:
    """Return the (_ncnt.csv, _nbin.csv) sidecar paths for a FASTA."""
    prefix = fasta_prefix(fasta)
    if outdir:
        prefix = str(Path(outdir, Path(prefix).name))
    return prefix + "_ncnt.csv", prefix + "_nbin.csv"


def default_reference_library() -> str:
    from wgsextract_cli.core.runtime import repo_root

    return str(repo_root() / "reference")


def _path_key(path: PathInput) -> str:
    return os.path.normcase(str(absolute_path(expand_path(fspath(path)))))


def reference_genomes_dir(reflib: PathInput) -> str:
    """The folder holding a reference library's genomes and the files made from
    them: the configured reference_genome_library for the configured library
    (or for any library when reference_library is unset), else <reflib>/genomes.
    Only reference genomes are ever written there."""
    from wgsextract_cli.core.config import settings

    configured = settings.get("reference_genome_library")
    if isinstance(configured, str) and configured:
        library = settings.get("reference_library")
        if not library or _path_key(str(library)) == _path_key(reflib):
            return str(absolute_path(expand_path(configured)))
    return str(Path(reflib, "genomes"))


def library_of_genomes_dir(folder: PathInput) -> str | None:
    """The library whose genomes ``folder`` holds: the configured library for the
    configured reference_genome_library, the parent of a ``genomes`` folder,
    else None."""
    from wgsextract_cli.core.config import settings

    configured = settings.get("reference_genome_library")
    if (
        isinstance(configured, str)
        and configured
        and _path_key(folder) == _path_key(configured)
    ):
        return resolve_reference_library()
    path = absolute_path(expand_path(fspath(folder)))
    return str(path.parent) if path.name.lower() == "genomes" else None


def reference_library_of(path: PathInput) -> str:
    """The library a ``--ref`` names: a FASTA's folder (the library whose
    genomes folder holds it), else ``path`` itself."""
    absolute = absolute_path(expand_path(fspath(path)))
    if not absolute.is_file():
        return str(absolute)
    folder = absolute.parent
    return library_of_genomes_dir(folder) or str(folder)


def resolve_reference_library(explicit: PathInput | None = None) -> str:
    """Explicit path, else configured reference_library, else <repo>/reference."""
    from wgsextract_cli.core.config import settings

    configured = settings.get("reference_library")
    reflib = explicit or (str(configured) if configured else None)
    return str(absolute_path(expand_path(reflib or default_reference_library())))


def read_fai_lengths(fasta: PathInput) -> dict[str, int]:
    """Contig lengths from the FASTA's .fai index; empty if it is missing or unreadable."""
    lengths: dict[str, int] = {}
    try:
        with append_suffix(fasta, ".fai").open(encoding="utf-8") as fai:
            for line in fai:
                cols = line.split("\t")
                if len(cols) >= 2:
                    lengths[cols[0]] = int(cols[1])
    except (OSError, ValueError):
        return {}
    return lengths


def fasta_build(fasta: PathInput) -> str | None:
    """The build a FASTA declares itself to be ("hg19", "hg38" or "t2t"), from
    its chr1 length (.fai, else .dict), the .dict's AS assembly tags, the
    description on its first header line, then its name; None when nothing
    names one. Nothing here reads the sequence."""
    dict_path = Path(reference_dict_path(fasta))
    try:
        dictionary = (
            header_sequences(dict_path.read_text()) if dict_path.is_file() else []
        )
    except (OSError, UnicodeDecodeError):
        dictionary = []
    lengths = read_fai_lengths(fasta) or {
        row["SN"]: int(row["LN"])
        for row in dictionary
        if "SN" in row and row.get("LN", "").isdigit()
    }
    build = build_from_chr1_length(lengths)
    for assembly in sorted({row["AS"] for row in dictionary if "AS" in row}):
        build = build or canonical_build(assembly) or build_from_path(assembly)
    return (
        build
        or build_from_path(_fasta_description(fasta))
        or build_from_path(Path(fasta).name)
    )


def _fasta_description(fasta: PathInput) -> str:
    """The text after the name on a FASTA's first header line, such as
    Ensembl's "dna:chromosome chromosome:GRCh38:1:..." or NCBI's
    "Homo sapiens chromosome 1, GRCh38.p14 Primary Assembly"."""
    import gzip
    import zlib

    path = fspath(fasta)
    opener = gzip.open if path.lower().endswith(".gz") else open
    try:
        with opener(path, "rb") as handle:
            line = handle.readline(4096)
    except (OSError, EOFError, zlib.error):
        return ""
    parts = line[1:].decode("utf-8", "replace").split(None, 1)
    return parts[1].strip() if line.startswith(b">") and len(parts) == 2 else ""


def sequence_signature(contig_lengths: dict[str, int]) -> str:
    """WGS Extract SN/LN signature, the same value calculate_bam_md5 gives a BAM header."""
    from wgsextract_cli.core.variant_files import calculate_bam_md5

    header = "".join(
        f"@SQ\tSN:{name}\tLN:{length}\n" for name, length in contig_lengths.items()
    )
    return calculate_bam_md5("", header=header)


def verify_sequence_signature(
    filename: str, lengths: dict[str, int], expected: str
) -> str:
    """Check the catalog's SN/LN signature (not a checksum of FASTA bytes)."""
    from wgsextract_cli.core.utils import WGSExtractError

    signature = sequence_signature(lengths)
    if signature != expected:
        raise WGSExtractError(
            f"{filename} does not match the catalog sequence dictionary "
            f"(expected signature {expected}, found {signature}). The file is "
            "a different build or patch level than the catalog entry."
        )
    return signature


def fasta_signature(fasta: PathInput) -> str | None:
    lengths = read_fai_lengths(fasta)
    return sequence_signature(lengths) if lengths else None


def find_fasta_by_signature(
    directories: Iterable[PathInput], signature: str
) -> str | None:
    """Find an indexed FASTA whose sequence dictionary matches a BAM/CRAM signature."""
    for directory in directories:
        for candidate in reference_fasta_candidates(directory):
            if fasta_signature(candidate) == signature:
                return candidate
    return None


def find_reference_fasta(root: PathInput, *hints: PathInput | None) -> str:
    candidates = []
    for directory in [Path(root), Path(reference_genomes_dir(root))]:
        candidates.extend(reference_fasta_candidates(directory))
    return select_reference_fasta(candidates, *hints)


def resolve_input_reference_fasta(input_path: PathInput | None) -> str:
    if not input_path:
        return ""
    directory = Path(input_path).parent
    if not directory.is_dir():
        return ""
    manifest_path = directory / "manifest.json"
    if manifest_path.is_file():
        try:
            with manifest_path.open(encoding="utf-8") as manifest_file:
                manifest: dict[str, Any] = json.load(manifest_file)
            ref_file = manifest.get("files", {}).get("ref")
            if ref_file:
                candidate = directory / ref_file
                if candidate.is_file():
                    return str(candidate)
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            pass
    return select_reference_fasta(reference_fasta_candidates(directory), input_path)


def reference_fasta_candidates(directory: PathInput) -> list[str]:
    root = Path(directory)
    if not root.is_dir():
        return []
    return [
        str(file)
        for file in sorted(root.iterdir())
        if is_fasta_path(file) and file.is_file()
    ]


def select_reference_fasta(candidates: list[str], *hints: PathInput | None) -> str:
    if not candidates:
        return ""
    build = build_hint(*hints)
    if build:
        for alias in BUILD_PATH_TOKENS.get(build, (build,)):
            for candidate in candidates:
                name = Path(candidate).name.lower()
                if alias in name and build_from_path(name) == build:
                    return candidate
    if len(candidates) == 1:
        return candidates[0]
    return ""


def find_annotation_vcf(reflib: PathInput, build: str) -> str:
    directories = [
        reflib,
        *map(str, download_dirs(reflib)),
        str(Path(reflib, "microarray")),
        str(Path(reflib, "genomes", "microarray")),
    ]
    names = annotation_vcf_names(build)
    return first_existing_named_file(directories, names)


def annotation_vcf_names(build: str) -> list[str]:
    """Candidate SNP target file names; with a known build, only that build's files."""
    generic = ["All_SNPs.vcf.gz", "common_all.vcf.gz"]
    build_specific = [
        "All_SNPs_hg19_ref.tab.gz",
        "All_SNPs_hg38_ref.tab.gz",
        "All_SNPs_HG19_ref.tab.gz",
        "All_SNPs_HG38_ref.tab.gz",
        "All_SNPs_GRCh37_ref.tab.gz",
        "All_SNPs_GRCh38_ref.tab.gz",
        "All_SNPs_grch37_ref.tab.gz",
        "All_SNPs_grch38_ref.tab.gz",
    ]
    if not build:
        return generic + build_specific
    # A file for another build would silently give wrong-build coordinates.
    tokens = {"hg19": ("hg19", "grch37"), "hg38": ("hg38", "grch38")}.get(
        canonical_build(build) or "", ()
    )
    matching = [n for n in build_specific if any(t in n.lower() for t in tokens)]
    return matching + generic


def find_annotation_resource(
    reflib: PathInput, prefix: str, build: str, extensions: list[str]
) -> str:
    directories = [reflib, *map(str, download_dirs(reflib))]
    patterns: list[str] = []
    if build:
        patterns.extend(f"{prefix}*{build}*{extension}" for extension in extensions)
    patterns.extend(f"{prefix}*{extension}" for extension in extensions)
    return first_matching_file(directories, patterns)


def resolve_input_target_tab(input_path: PathInput | None) -> str:
    if not input_path:
        return ""
    directory = Path(input_path).parent
    if not directory.is_dir():
        return ""
    stem = input_stem(input_path)
    exact = first_existing_file(
        [
            directory / f"{stem}.targets.tab.gz",
            directory / f"{stem}.target.tab.gz",
            directory / f"{stem}.snps.tab.gz",
        ]
    )
    if exact:
        return exact
    return first_matching_file(
        [directory],
        ["*.targets.tab.gz", "*.target.tab.gz", "*.snps.tab.gz", *TARGET_PATTERNS],
    )


def find_reference_target_tab(reflib: PathInput, build: str) -> str:
    directories = [
        reflib,
        *map(str, download_dirs(reflib)),
        str(Path(reflib, "microarray")),
        str(Path(reflib, "genomes", "microarray")),
    ]
    names = annotation_vcf_names(build)
    names.sort(key=lambda name: not name.endswith("_ref.tab.gz"))
    named = first_existing_named_file(directories, names)
    if named or build:
        return named
    return first_matching_file(directories, list(TARGET_PATTERNS))


def input_stem(path: PathInput) -> str:
    name = Path(path).name
    for extension in (
        ".vcf.gz",
        ".vcf.bgz",
        ".fasta.gz",
        ".fna.gz",
        ".fa.gz",
        ".tsv.gz",
        ".tab.gz",
        ".bam",
        ".cram",
        ".vcf",
        ".bcf",
    ):
        if name.lower().endswith(extension):
            return name[: -len(extension)]
    return Path(name).stem


def first_existing_file(paths: Iterable[PathInput]) -> str:
    for path in paths:
        if Path(path).is_file():
            return fspath(path)
    return ""


def first_existing_named_file(
    directories: Iterable[PathInput], names: list[str]
) -> str:
    for directory in directories:
        if not Path(directory).is_dir():
            continue
        for name in names:
            path = Path(directory) / name
            if path.is_file():
                return str(path)
    return ""


def first_matching_file(directories: Iterable[PathInput], patterns: list[str]) -> str:
    for directory in directories:
        if not Path(directory).is_dir():
            continue
        for path in sorted(Path(directory).iterdir()):
            for pattern in patterns:
                if fnmatch(path.name.lower(), pattern.lower()):
                    if path.is_file():
                        return str(path)
    return ""
