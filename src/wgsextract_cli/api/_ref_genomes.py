"""Parameter types for FASTAs, catalog genomes and library status."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core.utils import UsageError

from ._network import DownloadParams
from ._params import Params, param
from ._ref_library import _ConfiguredLibrary, _InLibrary, _library_named
from ._shared import optional_input_param, ref_param, threads_param

ALIGNER_INDEXES = ("bwa", "bwa-mem2")


def _fasta_param() -> Any:
    return param(
        None,
        doc="The reference FASTA (required; default: config reference_fasta).",
        flags=("--ref",),
        path="file",
        shared=True,
        config_key="reference_fasta",
    )


@dataclass(frozen=True, kw_only=True)
class RefDownloadParams(Params):
    """Download a reference FASTA from ``url`` to ``out``."""

    COMMAND = "ref download"
    # A server must not fetch arbitrary URLs for its clients.
    LOCAL_ONLY = True

    url: str = param(help="URL to download from")
    out: Path = param(help="Output FASTA file path", path="file")


@dataclass(frozen=True, kw_only=True)
class RefIndexParams(DownloadParams):
    """Index a FASTA: ``.fai`` (and ``.gzi``), the ``.dict`` sequence
    dictionary and the aligner's index, written beside it."""

    COMMAND = "ref index"
    WRITES_BESIDE = "ref"

    ref: Path | None = _fasta_param()
    aligner: str = param(
        "bwa",
        help="Aligner index to make. bwa-mem2's maps faster but takes 17 GB of "
        "memory for a human genome; --download permits fetching hs38DH's, others need ~90 GB "
        "of memory to build.",
        choices=ALIGNER_INDEXES,
    )


@dataclass(frozen=True, kw_only=True)
class RefDenseSaParams(Params):
    """Build wgse-bwa's optional dense suffix-array file for a FASTA's BWA
    index (``<ref>.wgse-sa<interval>``, 0.8 GB for hs38 at 16), list the
    FASTA's files, or delete them. Needs wgse-bwa 0.11.0 or later on PATH;
    ``align``/``realign`` read the file only when asked
    (``dense_suffix_array``)."""

    COMMAND = "ref dense-sa"
    WRITES_BESIDE = "ref"

    ref: Path | None = _fasta_param()
    interval: int = param(
        16,
        help="Store the suffix-array position of every Nth row. A smaller N "
        "locates seeds faster but doubles the file each halving (0.8 GB for "
        "hs38 at 16, the default).",
        choices=(2, 4, 8, 16),
        metavar="N",
    )
    list_files: bool = param(
        False,
        help="List the FASTA's dense suffix-array files, their size, and the "
        "one wgse-bwa would read on this computer.",
        flags=("--list",),
        dest="list",
        action="store_true",
        exclusive="dense-sa-mode",
    )
    delete: bool = param(
        False,
        help="Delete the FASTA's dense suffix-array files.",
        action="store_true",
        deletes=True,
        exclusive="dense-sa-mode",
    )
    threads: int | None = threads_param()


@dataclass(frozen=True, kw_only=True)
class RefCountNsParams(Params):
    """Count the FASTA's N runs into ``<ref>_ncnt.csv`` and ``<ref>_nbin.csv``;
    sequence, length and N totals are metrics."""

    COMMAND = "ref count-ns"
    WRITES_BESIDE = "ref"

    ref: Path | None = _fasta_param()
    outdir: Path | None = param(
        None,
        doc="Folder for the N-count files (default: beside the FASTA; config "
        "output_directory is not used).",
        flags=("--outdir", "-o"),
        path="directory",
        shared=True,
    )


@dataclass(frozen=True, kw_only=True)
class RefVerifyParams(Params):
    """Check a FASTA's compression, its ``.fai``/``.gzi`` and ``.dict`` against
    the file, and its sequence signature against the library catalog. Writes
    a missing ``.fai``/``.gzi``; ``contigs`` and ``signature`` are metrics."""

    COMMAND = "ref verify"
    WRITES_BESIDE = "ref"

    ref: Path | None = param(
        None,
        doc="The reference FASTA, or a folder holding one (required; default: "
        "config reference_fasta).",
        flags=("--ref",),
        path="file_or_directory",
        shared=True,
        config_key="reference_fasta",
    )


@dataclass(frozen=True, kw_only=True)
class RefLibraryParams(_InLibrary, Params):
    """Install a catalog genome into the library's ``genomes`` folder, trying
    each mirror that hosts it."""

    COMMAND = "ref library"

    install: str | None = param(
        None,
        help="Non-interactively install a genome by its code (e.g., hs38) or "
        "index (e.g., 9).",
        doc="The genome's catalog code (e.g. hs38) or 1-based index (required).",
    )
    ref: Path | None = ref_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.install:
            raise UsageError(
                "Set install to a genome's code or index; `wgsextract ref "
                "library --list` lists them."
            )


def _library_ref_param() -> Any:
    return param(
        None,
        doc="Reference library folder, or a FASTA in it (default: config "
        "reference_fasta's library, else reference_library, else the default "
        "library).",
        flags=("--ref",),
        path="file_or_directory",
        shared=True,
        config_key="reference_fasta",
    )


@dataclass(frozen=True, kw_only=True)
class RefLibraryListParams(_ConfiguredLibrary):
    """List the catalog's genomes with their status in the library (installed,
    unindexed, incomplete or missing), size, N counts and VEP cache, then the
    gene maps and annotation data it holds."""

    # The server operation (ref/library-list) and MCP tool keep this name; the
    # CLI has no such subcommand and lists with 'ref library --list' (#352).
    COMMAND = "ref library-list"
    CLI_COMMAND = "ref library --list"

    ref: Path | None = _library_ref_param()
    remote_sizes: bool = param(
        False,
        help="Look up download sizes online (cached 30 days; at most ~8 s).",
        flags=("--remote-sizes",),
        action="store_true",
        doc="Look up the download size of each genome whose size isn't cached "
        "(HTTP HEAD or FTP SIZE, ~8 s at most) and save it in the library's "
        "cache for 30 days.",
        writes=True,
    )

    def write_folders(self) -> Iterator[Path]:
        # --remote-sizes caches the sizes it finds in the library.
        return iter([self.library()] if self.remote_sizes else [])


@dataclass(frozen=True, kw_only=True)
class RefLibraryDeleteParams(_ConfiguredLibrary):
    """Delete a catalog genome from the library's genomes folder: its FASTA,
    a partial download, its indexes and sequence dictionary. Its N counts,
    which never change, stay."""

    # As ref library-list: the CLI deletes with 'ref library --delete'.
    COMMAND = "ref library-delete"
    CLI_COMMAND = "ref library --delete"
    DELETES = True

    genome: str | None = param(
        None,
        help="Delete a catalog genome (code or file name) and its indexes.",
        flags=("--delete",),
        metavar="GENOME",
        dest="delete",
        doc="The genome's catalog code (e.g. hs38) or file name (e.g. "
        "hs38.fa.gz) (required).",
    )
    ref: Path | None = _library_ref_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.genome:
            raise UsageError(
                "Set genome to a catalog genome's code or file name; "
                "`wgsextract ref library --list` lists them."
            )


def _status_ref_param() -> Any:
    return param(
        None,
        doc="Reference library folder, or a FASTA in it (default: config "
        "reference_library, else reference_fasta's library, else the "
        "default library).",
        flags=("--ref",),
        path="file_or_directory",
        shared=True,
    )


def _mygenomes_library_param(help: str) -> Any:
    return param(
        None,
        help=help,
        doc="My Genomes library folder for the test genome (default: config "
        "mygenomes_library, else mygenomes/ in the users workspace).",
        path="directory",
        hidden_flags=("--dataset-library", "--genome-library"),
        former_names=("dataset_library",),
    )


@dataclass(frozen=True, kw_only=True)
class RefStatusParams(Params):
    """What the reference library holds, as the result's ``details``: genomes
    and their indexes, annotation data, gene maps, VEP cache, PharmCAT,
    ploidy and mappability files, and the test genome."""

    COMMAND = "ref status"

    ref: Path | None = _status_ref_param()
    input: Path | None = optional_input_param(
        "A BAM/CRAM or VCF whose build picks the annotation files to report."
    )
    mygenomes_library: Path | None = _mygenomes_library_param(
        "My Genomes library folder used for the test genome status."
    )
    annotation_vcf: Path | None = param(
        None,
        help="Custom annotation VCF path to include in readiness checks.",
        path="file",
    )

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        if self.ref is None:
            return
        yield "ref", _library_named(self.ref)


@dataclass(frozen=True, kw_only=True)
class RefTestGenomeParams(Params):
    """The test genome: the small hg19 benchmark dataset (1000 Genomes
    HG00096) in the My Genomes library. Reports whether it is installed;
    ``install`` downloads it (SHA-256 pinned) and ``delete`` deletes it."""

    COMMAND = "ref test-genome"

    ref: Path | None = _status_ref_param()
    mygenomes_library: Path | None = _mygenomes_library_param(
        "My Genomes library to install into (default: config "
        "mygenomes_library, else mygenomes/ in the users workspace)."
    )
    install: bool = param(
        False,
        help="Download the test genome (84 MB, SHA-256 checked) and unpack it.",
        action="store_true",
        writes=True,
        exclusive="test-genome-mode",
    )
    delete: bool = param(
        False,
        help="Delete the test genome and its download.",
        action="store_true",
        deletes=True,
        exclusive="test-genome-mode",
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.install and self.delete:
            raise UsageError("--install and --delete can't be used together.")

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        if self.ref is not None:
            yield "ref", _library_named(self.ref)
        if (self.install or self.delete) and self.mygenomes_library is None:
            # The folder written is then config's mygenomes_library or the
            # default one, which must be allowed too.
            yield "mygenomes_library", self._datasets()

    def _datasets(self) -> Path:
        from wgsextract_cli.commands.ref.status import mygenomes_library_for

        return mygenomes_library_for(self.mygenomes_library)

    def write_folders(self) -> Iterator[Path]:
        if self.install or self.delete:
            yield self._datasets()
