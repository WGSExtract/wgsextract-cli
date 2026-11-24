"""Parameter type and path authorization for ACMG reference data."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.utils import UsageError

from ._params import param, with_config_defaults
from ._ref_library import _ConfiguredLibrary, _library_named
from ._shared import ref_param, threads_param


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

        filled = with_config_defaults(self)
        sa_dir = filled.sa_dir
        return sa_dir.expanduser() if sa_dir else default_sa_dir(filled.library())

    def _destinations(self) -> tuple[list[Path], list[Path]]:
        """The folders the run writes in and the files it names there, from
        the library and ``sa_dir`` it resolves, config included."""
        from wgsextract_cli.core.acmg_data import default_sa_dir, destinations

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
