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

from wgsextract_cli.core.results import CommandResult

from ._params import Params, param, with_config_defaults
from ._ref_acmg import RefAcmgDataParams as RefAcmgDataParams
from ._ref_genomes import ALIGNER_INDEXES as ALIGNER_INDEXES
from ._ref_genomes import RefCountNsParams as RefCountNsParams
from ._ref_genomes import RefDenseSaParams as RefDenseSaParams
from ._ref_genomes import RefDownloadParams as RefDownloadParams
from ._ref_genomes import RefIndexParams as RefIndexParams
from ._ref_genomes import RefLibraryDeleteParams as RefLibraryDeleteParams
from ._ref_genomes import RefLibraryListParams as RefLibraryListParams
from ._ref_genomes import RefLibraryParams as RefLibraryParams
from ._ref_genomes import RefStatusParams as RefStatusParams
from ._ref_genomes import RefTestGenomeParams as RefTestGenomeParams
from ._ref_genomes import RefVerifyParams as RefVerifyParams
from ._ref_library import _InLibrary, _library_named
from ._ref_recheck import RefRecheckParams as RefRecheckParams
from ._ref_resources import (
    BUILDS as BUILDS,
)
from ._ref_resources import (
    GNOMAD_RELEASES as GNOMAD_RELEASES,
)
from ._ref_resources import (
    RefAlphamissenseParams as RefAlphamissenseParams,
)
from ._ref_resources import (
    RefCaddParams as RefCaddParams,
)
from ._ref_resources import (
    RefClinvarParams as RefClinvarParams,
)
from ._ref_resources import (
    RefDbsnpParams as RefDbsnpParams,
)
from ._ref_resources import (
    RefGerpParams as RefGerpParams,
)
from ._ref_resources import (
    RefGnomadParams as RefGnomadParams,
)
from ._ref_resources import (
    RefPharmgkbParams as RefPharmgkbParams,
)
from ._ref_resources import (
    RefPhylopParams as RefPhylopParams,
)
from ._ref_resources import (
    RefRevelParams as RefRevelParams,
)
from ._ref_resources import (
    RefSpliceaiParams as RefSpliceaiParams,
)
from ._ref_resources import (
    _build_param,
    _delete_param,
)
from ._ref_resources import (
    _Resource as _Resource,
)
from ._ref_ybrowse import RefYbrowseParams as RefYbrowseParams
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import ref_param, threads_param


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
class RefRegulatoryParams(_InLibrary, Params):
    """Check, download or import the GRCh38 SCREEN cCRE Registry v4 BED."""

    COMMAND = "ref regulatory"

    source: str = param("screen", help="Regulatory data source.", choices=("screen",))
    build: str = param("hg38", help="Assembly of the BED.", choices=("hg38",))
    download: bool = param(
        False,
        help="Fetch the pinned public BED (129 MB) from downloads.wenglab.org.",
        action="store_true",
        writes=True,
    )
    bed: Path | None = param(
        None,
        help="Import a sorted, six-column GRCh38 SCREEN v4 BED (may be gzip-compressed).",
        path="file",
        writes=True,
    )
    ref: Path | None = ref_param()

    def library(self) -> Path:
        from wgsextract_cli.core.ref_library.assets import resolve_reference_library
        from wgsextract_cli.core.ref_library.resolver import library_roots

        return Path(resolve_reference_library(library_roots(self.ref).root))

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        from wgsextract_cli.core import regulatory_data

        filled = with_config_defaults(self, only={"ref"})
        yield from regulatory_data.guarded_paths(filled.ref)


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
    templates, or delete them. Missing liftOver chains require ``download``.
    ``microarray`` builds a missing target itself."""

    COMMAND = "ref microarray-targets"

    download: bool = param(
        False,
        help="Fetch missing public liftOver chains from UCSC or its mirror; "
        "no sample data is sent.",
        action="store_true",
        writes=True,
    )
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


def regulatory(
    params: RefRegulatoryParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Check or install the track; output role ``regulatory_track``."""
    from wgsextract_cli.commands.ref.regulatory import run_regulatory

    return execute(
        params, run_regulatory, progress=progress, cancel=cancel, check=check
    )


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


def recheck(
    params: RefRecheckParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """``details``: wgsextract.recheck/1. Only ``online=True`` uses the network."""
    from wgsextract_cli.commands.ref.recheck import run_recheck

    return execute(params, run_recheck, progress=progress, cancel=cancel, check=check)


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


def dbsnp(
    params: RefDbsnpParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Download NCBI dbSNP b151 common rsIDs and build their offline SQLite
    lookup index (output role ``reference_resource``), or delete them."""
    from wgsextract_cli.commands.ref.library_commands import run_dbsnp

    return execute(params, run_dbsnp, progress=progress, cancel=cancel, check=check)


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
