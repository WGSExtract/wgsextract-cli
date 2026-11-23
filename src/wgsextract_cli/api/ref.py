"""``ref``: the reference library: genomes, their indexes and N counts, and
the annotation data and panels other commands read.

``ref`` names a FASTA (``index``, ``count_ns``, ``verify``) or the reference
library folder, or a FASTA inside it; unset, it is config.toml's
``reference_fasta``, else the configured or default library. Installers write
into the library and record what they wrote as the result's outputs::

    api.ref.verify(api.RefVerifyParams(ref=Path("lib/genomes/hs38DH.fa.gz")))
    api.ref.status(api.RefStatusParams()).details["genomes"]
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.utils import UsageError

from ._network import DownloadParams
from ._params import Params, param
from ._ref_library import _InLibrary, _library_named
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import optional_input_param, ref_param, threads_param

BUILDS = ("hg38", "hg19")
GNOMAD_RELEASES = ("2.1.1", "4.1")
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


def _build_param(help: str) -> Any:
    return param("hg38", help=help, choices=BUILDS)


def _delete_param(what: str) -> Any:
    return param(
        False,
        help=f"Delete the installed {what}",
        action="store_true",
        deletes=True,
    )


@dataclass(frozen=True, kw_only=True)
class RefAadrParams(_InLibrary, Params):
    """Install the hash-pinned CC0 AADR v66.p1 2M SNP panel into the library,
    or delete it. ``ref bootstrap`` does not install it: the Harvard Dataverse
    server is slow, so the panel is only downloaded on request."""

    COMMAND = "ref aadr"

    delete: bool = _delete_param("AADR 2M SNP panel")
    ref: Path | None = ref_param()


@dataclass(frozen=True, kw_only=True)
class RefSomalierParams(_InLibrary, Params):
    """Install somalier's hash-pinned sites files (MIT) and the labelled
    1000 Genomes sketches (CC BY 4.0, Zenodo 3479773) that ``qc relate``
    uses, about 85 MB, or delete them."""

    COMMAND = "ref somalier"

    delete: bool = _delete_param("somalier sites and 1000 Genomes sketches")
    ref: Path | None = ref_param()


@dataclass(frozen=True, kw_only=True)
class RefImputationParams(_InLibrary, Params):
    """Install SHA-256-pinned GRCh38 1000 Genomes 30x panel chromosomes
    and prepare GLIMPSE2 binary references, or delete installed chromosomes."""

    COMMAND = "ref imputation"

    delete: bool = _delete_param("imputation panel chromosomes")
    chromosomes: str | None = param(
        None,
        help="Chromosomes: chr20, chr1-22 or all (autosomes only).",
    )
    ref: Path | None = ref_param()
    threads: int | None = threads_param()

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        from wgsextract_cli.core import imputation_panel

        from ._params import with_config_defaults

        filled = with_config_defaults(self)
        if filled.ref is not None:
            yield "ref", filled.ref.expanduser()
        library = (
            _library_named(filled.ref) if filled.ref is not None else filled.library()
        )
        yield "ref", library
        for path in imputation_panel.guarded_paths(
            os.fspath(library), self.chromosomes
        ):
            yield "ref", path


@dataclass(frozen=True, kw_only=True)
class RefAncestryParams(_InLibrary, Params):
    """Install the hash-pinned ancestry reference of ``qc ancestry``, or
    delete it."""

    COMMAND = "ref ancestry"

    delete: bool = _delete_param("ancestry reference")
    ref: Path | None = ref_param()

    def write_folders(self) -> Iterator[Path]:
        from wgsextract_cli.core import ancestry_reference

        yield from super().write_folders()
        yield ancestry_reference.folder(os.fspath(self.library()))


@dataclass(frozen=True, kw_only=True)
class RefGnomadSvParams(_InLibrary, Params):
    """Install the gnomAD structural-variant sites tables that ``vcf sv-freq``
    matches SV calls against, or delete them. Each sites VCF is checked
    against a pinned SHA-256, reduced to a small table and deleted. gnomAD
    data have no use restriction.

    ``sr``: gnomAD-SV v4.1 for GRCh38 (1.7 GB download) and v2.1 for GRCh37
    (141 MB). ``lr``: the gnomAD-LR 0.1 beta long-read SVs, GRCh38 only,
    streamed out of 53 GB of sites VCFs (one chromosome, up to 4.3 GB, at
    a time)."""

    COMMAND = "ref gnomad-sv"

    source: str = param(
        "sr",
        help="sr: gnomAD-SV (GRCh38, GRCh37). lr: gnomAD-LR (GRCh38, 53 GB).",
        choices=("sr", "lr"),
    )
    build: str | None = param(
        None,
        help="Only this build (default: all).",
        choices=("hg38", "hg19"),
    )
    delete: bool = _delete_param("gnomAD SV tables")
    ref: Path | None = ref_param()


@dataclass(frozen=True, kw_only=True)
class RefYbrowseParams(_InLibrary, Params):
    """Install a hash-pinned, sites-only GRCh38 yBrowse snapshot into the
    library, or delete the installed snapshots."""

    COMMAND = "ref ybrowse"

    delete: bool = _delete_param("yBrowse snapshots")

    snapshot: Path | None = param(
        None,
        help="Use a local GRCh38 VCF(.gz), verifying its header instead of the "
        "upstream pin.",
        path="file",
        cli_type=Path,
    )
    ref: Path | None = ref_param()


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


class _ConfiguredLibrary(_InLibrary, Params):
    """An operation on the library ``ref`` names, else the configured FASTA's."""

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        from ._params import with_config_defaults

        # Unset, ref is the configured FASTA's library, else reference_library.
        filled = with_config_defaults(self)
        if filled.ref is not None:
            if self.ref is None:
                yield "ref", filled.ref.expanduser()
            yield "ref", _library_named(filled.ref)
        else:
            yield "ref", self.library()


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


@dataclass(frozen=True, kw_only=True)
class RefCytobandsParams(_InLibrary, Params):
    """Validate installed UCSC cytobands; fetch a missing table only with
    ``--download``. Installs primary-chromosome hg19/hg38 ideograms for the
    offline viewer; no sample data is sent."""

    COMMAND = "ref cytobands"

    build: str = _build_param("Cytoband assembly (default: hg38).")
    download: bool = param(
        False,
        help="Download missing UCSC cytoBand data from hgdownload.soe.ucsc.edu; "
        "only the public assembly URL is requested, never sample data.",
        action="store_true",
        writes=True,
    )
    ref: Path | None = param(
        None,
        doc="Reference library folder (default: config reference_library); an explicit FASTA resolves its library.",
        flags=("--ref",),
        path="file_or_directory",
        shared=True,
    )


@dataclass(frozen=True, kw_only=True)
class RefGeneMapParams(_InLibrary, Params):
    """Download the hg19 and hg38 gene maps into the library, or delete them."""

    COMMAND = "ref gene-map"

    delete: bool = param(
        False,
        help="Delete gene maps instead of downloading",
        action="store_true",
        deletes=True,
    )
    ref: Path | None = ref_param()


@dataclass(frozen=True, kw_only=True)
class _Resource(_InLibrary, Params):
    """Annotation data downloaded into the library's ``ref`` folder."""

    ref: Path | None = ref_param()


@dataclass(frozen=True, kw_only=True)
class RefClinvarParams(_Resource):
    """Download the official ClinVar VCF for hg19 and hg38."""

    COMMAND = "ref clinvar"


@dataclass(frozen=True, kw_only=True)
class RefRevelParams(_Resource):
    """Download REVEL pathogenicity scores for hg19 and hg38."""

    COMMAND = "ref revel"


@dataclass(frozen=True, kw_only=True)
class RefPhylopParams(_Resource):
    """Download UCSC's PhyloP 100-way conservation scores (~10 GB bigWig)."""

    COMMAND = "ref phylop"

    build: str = _build_param("Genome build to download (default: hg38).")


@dataclass(frozen=True, kw_only=True)
class RefGnomadParams(_Resource):
    """Check or download the gnomAD 2.1.1 exome sites VCFs or the gnomAD 4.1
    frequency and homozygote-count table."""

    COMMAND = "ref gnomad"

    release: str = param(
        "2.1.1",
        help=(
            "2.1.1: exome sites VCFs for hg19 and hg38 (hg38 lifted over). "
            "4.1: native hg38 exomes+genomes, with allele frequency, maximum "
            "ancestry-group frequency and homozygote counts; --threads sets parallel streams."
        ),
        choices=GNOMAD_RELEASES,
    )
    threads: int | None = threads_param()
    download: bool = param(
        False,
        help="Allow fetching the public sites VCFs for the selected release "
        "(4.1 streams ~900 GB, hours, to build or rebuild the table).",
        action="store_true",
        writes=True,
    )


@dataclass(frozen=True, kw_only=True)
class RefSpliceaiParams(_Resource):
    """Download SpliceAI scores for hg38 (Ensembl, MANE SNVs), or index a
    manual hg19 download; or delete the installed score files."""

    COMMAND = "ref spliceai"

    delete: bool = _delete_param("SpliceAI score files")


@dataclass(frozen=True, kw_only=True)
class RefAlphamissenseParams(_Resource):
    """Download AlphaMissense scores for hg19 and hg38, or delete them."""

    COMMAND = "ref alphamissense"

    delete: bool = _delete_param("AlphaMissense score files")


@dataclass(frozen=True, kw_only=True)
class RefPharmgkbParams(_Resource):
    """Download PharmGKB annotations, or delete them."""

    COMMAND = "ref pharmgkb"

    delete: bool = _delete_param("PharmGKB annotation files")


@dataclass(frozen=True, kw_only=True)
class RefCaddParams(_Resource):
    """Download CADD v1.7 deleteriousness scores (~85 GB per build)."""

    COMMAND = "ref cadd"

    build: str = _build_param(
        "Genome build to download (default: hg38). Each is about 85 GB."
    )


@dataclass(frozen=True, kw_only=True)
class RefGerpParams(_Resource):
    """Download GERP++ conservation scores as a bigWig."""

    COMMAND = "ref gerp"

    build: str = _build_param("Genome build to download (default: hg38).")


@dataclass(frozen=True, kw_only=True)
class RefAcmgDataParams(_ConfiguredLibrary):
    """Download the eight sources of ``vcf acmg`` (ClinVar and its protein
    index, gnomAD v4.1 and its gene constraint, ClinGen, REVEL, SpliceAI,
    phyloP) and the Ensembl GFF3 from their publishers, check them, and build
    fastVEP's databases. Shows each source's terms; reruns resume."""

    COMMAND = "ref acmg-data"

    chromosomes: str | None = param(
        None,
        help="Chromosomes for gnomAD, SpliceAI, REVEL and phyloP: chr22, "
        "chr1-22,chrX or all (default).",
    )
    sa_dir: Path | None = param(
        None,
        help="Folder to build the databases in (default: downloads/acmg/sa in the library).",
        path="directory",
        config_key="acmg_sa_dir",
    )
    spliceai_file: Path | None = param(
        None,
        help="A GRCh38 SpliceAI VCF to use, such as Illumina's (default: the "
        "library's, downloaded from Ensembl when missing).",
        path="file",
    )
    ensembl_release: str = param(
        "116", help="Ensembl release of the GFF3; match your vep-rs cache."
    )
    site_data: str | None = param(
        None,
        help="per-run: don't build gnomAD and SpliceAI (877 GB streamed, 29 GB "
        "downloaded for a genome); vcf acmg reads each input's own sites of "
        "them. library builds them. Default: as the folder was built, else library.",
        choices=("library", "per-run"),
    )
    dry_run: bool = param(
        False,
        help="Show the sources, their terms, sizes and the disk needed; "
        "download nothing.",
        action="store_true",
    )
    threads: int | None = threads_param()
    ref: Path | None = ref_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        from wgsextract_cli.core.acmg_data import parse_chromosomes

        parse_chromosomes(self.chromosomes)
        if not self.ensembl_release.isdigit():
            raise UsageError("--ensembl-release is a release number, such as 116.")

    def databases(self) -> Path:
        """The folder the databases go in."""
        from wgsextract_cli.core.acmg_data import default_sa_dir

        from ._params import with_config_defaults

        filled = with_config_defaults(self)
        sa_dir = filled.sa_dir
        return sa_dir.expanduser() if sa_dir else default_sa_dir(filled.library())

    def _destinations(self) -> tuple[list[Path], list[Path]]:
        """The folders the run writes in and the files it names there, from
        the library and ``sa_dir`` it resolves, config included."""
        from wgsextract_cli.core.acmg_data import default_sa_dir, destinations

        from ._params import with_config_defaults

        filled = with_config_defaults(self)
        # Lexically, as the guard checks ref, so a Windows request is checked
        # as such; links are resolved when each path is checked.
        library = (
            _library_named(filled.ref) if filled.ref is not None else self.library()
        )
        sa_dir = filled.sa_dir.expanduser() if filled.sa_dir else None
        return destinations(
            library,
            sa_dir or default_sa_dir(library),
            filled.ensembl_release,
        )

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        # The library (configured when ref is omitted), then everything written
        # in it and in sa_dir: a server checks each with links resolved, so a
        # linked downloads/acmg or sa folder can't lead outside the allowed ones.
        yield from super().implied_paths()
        folders, files = self._destinations()
        databases = folders[3]  # destinations(): acmg, sources, downloads, sa_dir, ...
        for path in (*folders, *files):
            yield "sa_dir" if path.is_relative_to(databases) else "ref", path

    def write_folders(self) -> Iterator[Path]:
        yield from super().write_folders()
        yield from self._destinations()[0]


@dataclass(frozen=True, kw_only=True)
class RefBootstrapParams(_InLibrary, Params):
    """Download the reference library bootstrap (liftover chains, chip
    templates, N counts and other support files). The AADR 2M SNP panel is a
    separate download (``ref aadr --install``). Saves the library's path as config.toml's ``reference_library`` when that
    is unset."""

    COMMAND = "ref bootstrap"
    # It may save config.toml, which a server request must not change.
    LOCAL_ONLY = True

    ref: Path | None = ref_param()


@dataclass(frozen=True, kw_only=True)
class RefMicroarrayTargetsParams(_InLibrary, Params):
    """Build the genome-wide hg19 and hg38 microarray targets
    (``microarray/All_SNPs_<build>_ref.tab.gz``) from the bootstrap's chip
    templates, or delete them. ``microarray`` builds a missing one itself."""

    COMMAND = "ref microarray-targets"

    delete: bool = _delete_param("microarray targets")
    ref: Path | None = ref_param()


def aadr(
    params: RefAadrParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Install the panel; output role ``reference_resource``."""
    from wgsextract_cli.commands.ref.library_commands import run_aadr

    return execute(params, run_aadr, progress=progress, cancel=cancel, check=check)


def somalier(
    params: RefSomalierParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Install the resources; output role ``reference_resource``."""
    from wgsextract_cli.commands.ref.library_commands import run_somalier

    return execute(params, run_somalier, progress=progress, cancel=cancel, check=check)


def imputation(
    params: RefImputationParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Install or delete panel chromosomes; output role ``reference_resource``."""
    from wgsextract_cli.commands.ref.library_commands import run_imputation

    return execute(
        params, run_imputation, progress=progress, cancel=cancel, check=check
    )


def ancestry(
    params: RefAncestryParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Install the resources; output role ``reference_resource``."""
    from wgsextract_cli.commands.ref.library_commands import run_ancestry_reference

    return execute(
        params, run_ancestry_reference, progress=progress, cancel=cancel, check=check
    )


def gnomad_sv(
    params: RefGnomadSvParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Install the tables; output role ``reference_resource``."""
    from wgsextract_cli.commands.ref.library_commands import run_gnomad_sv

    return execute(params, run_gnomad_sv, progress=progress, cancel=cancel, check=check)


def ybrowse(
    params: RefYbrowseParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Install the snapshot; output role ``reference_resource``."""
    from wgsextract_cli.commands.ref.library_commands import run_ybrowse

    return execute(params, run_ybrowse, progress=progress, cancel=cancel, check=check)


def download(
    params: RefDownloadParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download the file; output role ``download``. Not offered by the server."""
    from wgsextract_cli.commands.ref.core_commands import run_download

    return execute(params, run_download, progress=progress, cancel=cancel, check=check)


def index(
    params: RefIndexParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Index the FASTA; output role ``reference_index`` for each file written."""
    from wgsextract_cli.commands.ref.core_commands import run_index

    return execute(params, run_index, progress=progress, cancel=cancel, check=check)


def dense_sa(
    params: RefDenseSaParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Build (output role ``reference_index``), list or delete the FASTA's
    dense suffix-array files."""
    from wgsextract_cli.commands.ref.dense_sa import run_dense_sa

    return execute(params, run_dense_sa, progress=progress, cancel=cancel, check=check)


def count_ns(
    params: RefCountNsParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Count the Ns; output roles ``n_counts`` and ``n_bins``."""
    from wgsextract_cli.commands.ref.core_commands import run_count_ns

    return execute(params, run_count_ns, progress=progress, cancel=cancel, check=check)


def verify(
    params: RefVerifyParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Verify the FASTA; a mismatch fails, a missing ``.dict`` is a warning."""
    from wgsextract_cli.commands.ref.core_commands import run_verify

    return execute(params, run_verify, progress=progress, cancel=cancel, check=check)


def library(
    params: RefLibraryParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Install ``params.install``; output roles ``reference`` (the FASTA) and
    ``reference_sidecar``. The CLI's menu isn't offered: ``library_list``
    lists the catalog's genomes and ``status`` reports what is installed."""
    from wgsextract_cli.commands.ref.library_commands import run_library

    return execute(params, run_library, progress=progress, cancel=cancel, check=check)


def library_list(
    params: RefLibraryListParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """List the library. ``details`` has ``library``, ``genomes_dir`` (the
    folder its genomes are in), ``genomes`` and ``resources``.

    Each of ``genomes``, in catalog order, has the catalog's ``code``,
    ``label``, ``source`` (the mirror), ``file`` (the name in ``genomes_dir``),
    ``url``, ``description``, ``sn_count`` (sequences in the FASTA) and
    ``build`` (``hg19``, ``hg38``, ``t2t`` or null), then its ``status``
    (``installed`` with its ``.fai``, ``unindexed``, ``incomplete`` (a
    ``.partial`` download) or ``missing``), ``size_bytes`` (the FASTA or
    partial download with its ``.fai``, ``.gzi`` and ``.dict``; 0 when none)
    and ``size`` (the same, as text), ``remote_bytes`` (the download size, or
    null when not known: cached, or looked up with ``remote_sizes``), and
    whether its ``n_counts`` and a ``vep_cache`` for its assembly are in the
    library. ``resources`` has ``gene_maps``, then per build ``clinvar``,
    ``revel``, ``phylop`` and ``gnomad``, each true when installed. ``text``
    is the CLI's table."""
    from wgsextract_cli.commands.ref.core_commands import run_library_list

    return execute(
        params, run_library_list, progress=progress, cancel=cancel, check=check
    )


def library_delete(
    params: RefLibraryDeleteParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Delete ``params.genome``; nothing installed is no error. ``details``
    has ``library``, ``genomes_dir``, the genome's ``code`` and ``file``,
    ``deleted`` (the paths deleted, the FASTA first) and ``bytes_freed``."""
    from wgsextract_cli.commands.ref.library_commands import run_library_delete

    return execute(
        params, run_library_delete, progress=progress, cancel=cancel, check=check
    )


def status(
    params: RefStatusParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Report the library; the report is the result's ``details``."""
    from wgsextract_cli.commands.ref.status import run_status

    return execute(params, run_status, progress=progress, cancel=cancel, check=check)


def test_genome(
    params: RefTestGenomeParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Report, install or delete the test genome. ``details`` has the dataset
    ``library``, ``installed``, ``status`` (``installed``, ``incomplete``
    while a download is partial, else ``missing``) and ``path`` (its
    folder), as afterwards; ``install`` adds ``changed`` (false when it was
    already complete), ``delete`` adds ``deleted`` (the paths deleted) and
    ``bytes_freed``."""
    from wgsextract_cli.commands.ref.mini_genome import run_test_genome

    return execute(
        params, run_test_genome, progress=progress, cancel=cancel, check=check
    )


def cytobands(
    params: RefCytobandsParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Validate/install UCSC cytobands (output role ``reference_resource``).
    Missing or invalid tables require explicit ``download=True``."""
    from wgsextract_cli.commands.ref.cytobands import run_cytobands

    return execute(params, run_cytobands, progress=progress, cancel=cancel, check=check)


def gene_map(
    params: RefGeneMapParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download the gene maps (output role ``reference_resource``) or delete them."""
    from wgsextract_cli.commands.ref.library_commands import run_gene_map

    return execute(params, run_gene_map, progress=progress, cancel=cancel, check=check)


def clinvar(
    params: RefClinvarParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download ClinVar; output role ``reference_resource`` for each file."""
    from wgsextract_cli.commands.ref.library_commands import run_clinvar

    return execute(params, run_clinvar, progress=progress, cancel=cancel, check=check)


def revel(
    params: RefRevelParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download REVEL; output role ``reference_resource`` for each file."""
    from wgsextract_cli.commands.ref.library_commands import run_revel

    return execute(params, run_revel, progress=progress, cancel=cancel, check=check)


def phylop(
    params: RefPhylopParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download PhyloP; output role ``reference_resource``."""
    from wgsextract_cli.commands.ref.library_commands import run_phylop

    return execute(params, run_phylop, progress=progress, cancel=cancel, check=check)


def gnomad(
    params: RefGnomadParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download or build gnomAD; output role ``reference_resource`` for each file."""
    from wgsextract_cli.commands.ref.library_commands import run_gnomad

    return execute(params, run_gnomad, progress=progress, cancel=cancel, check=check)


def spliceai(
    params: RefSpliceaiParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download SpliceAI; output role ``reference_resource`` for each file."""
    from wgsextract_cli.commands.ref.library_commands import run_spliceai

    return execute(params, run_spliceai, progress=progress, cancel=cancel, check=check)


def alphamissense(
    params: RefAlphamissenseParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download AlphaMissense; output role ``reference_resource`` for each file."""
    from wgsextract_cli.commands.ref.library_commands import run_alphamissense

    return execute(
        params, run_alphamissense, progress=progress, cancel=cancel, check=check
    )


def pharmgkb(
    params: RefPharmgkbParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download PharmGKB; output role ``reference_resource`` for each file."""
    from wgsextract_cli.commands.ref.library_commands import run_pharmgkb

    return execute(params, run_pharmgkb, progress=progress, cancel=cancel, check=check)


def cadd(
    params: RefCaddParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download CADD; output role ``reference_resource`` for each file."""
    from wgsextract_cli.commands.ref.library_commands import run_cadd

    return execute(params, run_cadd, progress=progress, cancel=cancel, check=check)


def gerp(
    params: RefGerpParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download GERP; output role ``reference_resource``."""
    from wgsextract_cli.commands.ref.library_commands import run_gerp

    return execute(params, run_gerp, progress=progress, cancel=cancel, check=check)


def acmg_data(
    params: RefAcmgDataParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Build the databases (output role ``reference_resource`` for each file,
    the GFF3 and ``acmg_data.json``); with ``dry_run``, only the plan in
    ``details``."""
    from wgsextract_cli.commands.ref.acmg_data import run_acmg_data

    return execute(params, run_acmg_data, progress=progress, cancel=cancel, check=check)


def bootstrap(
    params: RefBootstrapParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Install the bootstrap; output role ``reference_resource`` for each file.
    Not offered by the server."""
    from wgsextract_cli.commands.ref.library_commands import run_bootstrap

    return execute(params, run_bootstrap, progress=progress, cancel=cancel, check=check)


def microarray_targets(
    params: RefMicroarrayTargetsParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Build (output role ``reference_resource``) or delete the targets."""
    from wgsextract_cli.commands.ref.library_commands import run_microarray_targets

    return execute(
        params, run_microarray_targets, progress=progress, cancel=cancel, check=check
    )
