"""Annotation-resource parameter dataclasses re-exported by ``api.ref``."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._params import Params, param, with_config_defaults
from ._ref_library import _InLibrary
from ._shared import gtex_pairs_param, ref_param, threads_param

BUILDS = ("hg38", "hg19")
GNOMAD_RELEASES = ("2.1.1", "4.1")


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
class _Resource(_InLibrary, Params):
    """Annotation data downloaded into the library's ``ref`` folder."""

    ref: Path | None = ref_param()


@dataclass(frozen=True, kw_only=True)
class RefGtexParams(_InLibrary, Params):
    """Verify or explicitly download a GRCh38 GTEx v10 eQTL track."""

    COMMAND = "ref gtex"

    download: bool = param(
        False,
        help="Fetch the pinned archive from storage.googleapis.com "
        "(SuSiE: 174 MB; --pairs: 2.56 GB to install one tissue).",
        action="store_true",
        writes=True,
    )
    pairs: str | None = gtex_pairs_param()
    ref: Path | None = ref_param()

    def __post_init__(self) -> None:
        from wgsextract_cli.core.gtex_pairs import validate_tissue

        super().__post_init__()
        validate_tissue(self.pairs)

    def library(self) -> Path:
        from wgsextract_cli.core.ref_library.assets import resolve_reference_library
        from wgsextract_cli.core.ref_library.resolver import library_roots

        return Path(resolve_reference_library(library_roots(self.ref).root))

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        from wgsextract_cli.core import gtex_data

        filled = with_config_defaults(self, only={"ref"})
        yield from gtex_data.guarded_paths(filled.ref, filled.pairs)


@dataclass(frozen=True, kw_only=True)
class RefClinvarParams(_Resource):
    """Download the official ClinVar VCF for hg19 and hg38."""

    COMMAND = "ref clinvar"


@dataclass(frozen=True, kw_only=True)
class RefDbsnpParams(_Resource):
    """Download NCBI dbSNP b151 common human rsIDs (~37M SNPs, VCF + tabix +
    offline SQLite rsID search index) for hg38 and hg19, or delete them."""

    COMMAND = "ref dbsnp"

    build: str | None = param(
        None,
        help="Only this build (default: both hg38 and hg19).",
        choices=BUILDS,
    )
    delete: bool = _delete_param("dbSNP common rsID files")


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
    frequency table with ancestry-group maximum FAF95 and homozygote counts."""

    COMMAND = "ref gnomad"

    release: str = param(
        "2.1.1",
        help=(
            "2.1.1: exome sites VCFs for hg19 and hg38 (hg38 lifted over). "
            "4.1: native hg38 exomes+genomes, with allele frequency, maximum "
            "ancestry-group frequency, maximum ancestry-group FAF95 and homozygote counts; "
            "--threads sets parallel streams."
        ),
        choices=GNOMAD_RELEASES,
    )
    threads: int | None = threads_param()
    download: bool = param(
        False,
        help="Allow fetching the public sites VCFs for the selected release "
        "(4.1 streams ~900 GB, hours, to build or upgrade the table for FAF95).",
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
