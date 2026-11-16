"""VCF annotations parameter declarations and their field helpers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.api._network import DownloadParams
from wgsextract_cli.api._params import param
from wgsextract_cli.api._shared import (
    check_confidence,
    confidence_depth_param,
    confidence_gq_param,
    ref_param,
)
from wgsextract_cli.core.utils import UsageError

from .shared import (
    _AnnotatorParams,
    _min_score_param,
    _resource_param,
    _VcfFileParams,
)


@dataclass(frozen=True, kw_only=True)
class VcfClinvarParams(_AnnotatorParams):
    """ClinVar significance and disease (CLNSIG, CLNDN) as
    ``clinvar_annotated.vcf.gz``; the pathogenic and likely pathogenic calls,
    with sequencing confidence labels, as ``clinvar_pathogenic.vcf.gz``.
    Matching requires an indexed reference FASTA (``ref``, else the library).
    Both copies are reference-normalized; phase-supported SNVs can match MNVs.
    Review status, stars and ClinVar identity remain separate from sequencing
    confidence. Conflicting assertions go to ``clinvar_conflicts.vcf.gz`` and
    their own confidence table, not the consensus pathogenic subset."""

    COMMAND = "vcf clinvar"

    clinvar_file: Path | None = _resource_param(
        "Path to ClinVar VCF data file.", "clinvar_vcf_path"
    )
    confidence_depth: tuple[float, float] | None = confidence_depth_param()
    confidence_gq: tuple[float, float] | None = confidence_gq_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        check_confidence(self)


@dataclass(frozen=True, kw_only=True)
class VcfRevelParams(_AnnotatorParams):
    """REVEL missense pathogenicity scores as ``revel_annotated.vcf.gz``."""

    COMMAND = "vcf revel"

    revel_file: Path | None = _resource_param(
        "Path to REVEL TSV or VCF data file.", "revel_tsv_path"
    )
    min_score: float | None = _min_score_param("REVEL", "0.5")


@dataclass(frozen=True, kw_only=True)
class VcfPhylopParams(_AnnotatorParams):
    """PhyloP conservation scores as ``phylop_annotated.vcf.gz``."""

    COMMAND = "vcf phylop"

    phylop_file: Path | None = _resource_param(
        "Path to a PhyloP bigWig (phylop_<build>.bw), or a TSV/VCF of scores.",
        "phylop_tsv_path",
    )
    min_score: float | None = _min_score_param("PhyloP", "2.0")


@dataclass(frozen=True, kw_only=True)
class VcfCaddParams(_AnnotatorParams):
    """CADD deleteriousness scores (CADD_RAW, CADD_PHRED) as
    ``cadd_annotated.vcf.gz``."""

    COMMAND = "vcf cadd"

    cadd_file: Path | None = _resource_param(
        "Path to the CADD whole-genome SNV TSV (cadd_<build>.tsv.gz).",
        "cadd_tsv_path",
    )
    cadd_indel_file: Path | None = _resource_param(
        "Path to a CADD indel TSV (cadd_indel_<build>.tsv.gz); used when present.",
        "cadd_indel_tsv_path",
    )
    min_score: float | None = _min_score_param("CADD PHRED", "20")


@dataclass(frozen=True, kw_only=True)
class VcfGerpParams(_AnnotatorParams):
    """GERP++ conservation scores as ``gerp_annotated.vcf.gz``."""

    COMMAND = "vcf gerp"

    gerp_file: Path | None = _resource_param(
        "Path to a GERP++ RS bigWig (gerp_<build>.bw).", "gerp_bigwig_path"
    )
    min_score: float | None = _min_score_param("GERP", "2.0")


@dataclass(frozen=True, kw_only=True)
class VcfGnomadParams(_AnnotatorParams):
    """gnomAD allele frequencies (GNOMAD_AF) as ``gnomad_annotated.vcf.gz``."""

    COMMAND = "vcf gnomad"

    gnomad_file: Path | None = _resource_param(
        "Path to gnomAD data: a sites VCF, or the allele-frequency table "
        "from 'ref gnomad --release 4.1'.",
        "gnomad_vcf_path",
    )
    max_af: float | None = param(
        None,
        help="Maximum Allele Frequency to filter for (e.g., 0.01 for 1%%).",
        doc="Also write the calls rarer than this gnomAD allele frequency "
        "(or absent from gnomAD), with confidence labels.",
    )
    confidence_depth: tuple[float, float] | None = confidence_depth_param()
    confidence_gq: tuple[float, float] | None = confidence_gq_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        check_confidence(self)


@dataclass(frozen=True, kw_only=True)
class VcfSvFreqParams(_AnnotatorParams):
    """Population frequencies of structural variants (SV_AF, SV_POPMAX_AF,
    SV_FREQ, SV_COMMON, ...) from gnomAD-SV and gnomAD-LR as
    ``sv_freq_annotated.vcf.gz``, and one row per SV in ``sv_freq.tsv``.

    For the SV calls of ``vcf sv`` (Delly, Sniffles, pbsv) and ``vcf cnv``,
    or any VCF of SVs. A call matches a gnomAD SV of the same type by
    reciprocal overlap (deletions, duplications, inversions) or by
    breakpoint distance and size similarity (insertions). Nondirectional
    CNV carrier frequencies and cross-type INS/DUP matches are excluded."""

    COMMAND = "vcf sv-freq"

    gnomad_sv_file: Path | None = _resource_param(
        "gnomAD-SV table (default: the library's, from 'ref gnomad-sv').",
        "gnomad_sv_path",
    )
    gnomad_lr_file: Path | None = _resource_param(
        "gnomAD-LR table (default: the library's; GRCh38).",
        "gnomad_lr_sv_path",
    )
    min_overlap: float = param(
        0.5,
        help="Minimum reciprocal overlap for same-type DEL/DUP/INV (default 0.5).",
    )
    max_distance: int = param(
        500,
        help="Maximum insertion breakpoint distance, bp (default 500).",
    )
    min_size_similarity: float = param(
        0.5,
        help="Minimum insertion size similarity (default 0.5).",
    )
    common_af: float = param(
        0.01,
        help="AF from which an SV is common (default 0.01).",
    )
    rare_af: float = param(
        0.001,
        help="AF below which an SV is rare (default 0.001).",
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if not 0 < self.min_overlap <= 1:
            raise UsageError("--min-overlap must be above 0 and at most 1.")
        if not 0 <= self.min_size_similarity <= 1:
            raise UsageError("--min-size-similarity must be between 0 and 1.")
        if self.max_distance < 0:
            raise UsageError("--max-distance must be at least 0.")
        if not 0 < self.rare_af <= self.common_af <= 1:
            raise UsageError(
                "--rare-af and --common-af must satisfy 0 < rare <= common <= 1."
            )


@dataclass(frozen=True, kw_only=True)
class VcfSpliceaiParams(_AnnotatorParams):
    """SpliceAI splicing scores as ``spliceai_annotated.vcf.gz``."""

    COMMAND = "vcf spliceai"

    spliceai_file: Path | None = _resource_param(
        "Path to SpliceAI VCF data file.", "spliceai_vcf_path"
    )


@dataclass(frozen=True, kw_only=True)
class VcfAcmgParams(_AnnotatorParams):
    """Research-only ACMG/AMP classes from fastVEP for a ``vep run`` (vep-rs)
    VCF, as ``acmg_annotated.vcf.gz`` (INFO ACMG_CLASS, ACMG_CRITERIA,
    ACMG_TRANSCRIPT) and ``acmg_summary.json``. Not a diagnosis. Refused
    unless ``research_acmg`` is set; GRCh38 only."""

    COMMAND = "vcf acmg"

    research_acmg: bool = param(
        False,
        help="Required: confirm these automated classes are for research "
        "only, not a diagnosis.",
        action="store_true",
    )
    sa_dir: Path | None = param(
        None,
        help="Folder of fastVEP databases (fastvep sa-build): ClinVar, its "
        "protein index, gnomAD, gene constraint, ClinGen, REVEL, SpliceAI, phyloP.",
        path="directory",
        config_key="acmg_sa_dir",
    )
    gff3: Path | None = _resource_param(
        "Ensembl GRCh38 GFF3 (Homo_sapiens.GRCh38.116.gff3.gz).", "acmg_gff3"
    )
    site_data: str = param(
        "auto",
        help="Where gnomAD and SpliceAI come from: library (the --sa-dir "
        "databases), per-run (only the input's sites, read from gnomAD's and "
        "Ensembl's indexed files and cached in the folder's sites/), or auto "
        "(default): the library's, per-run for chromosomes it lacks.",
        choices=("auto", "library", "per-run"),
    )

    def preflight(self) -> None:
        from wgsextract_cli.commands.vcf.acmg import require_opt_in

        require_opt_in(self.research_acmg)

    def _acmg_inputs(self) -> Iterator[tuple[str, Path]]:
        """The databases folder and GFF3 the run reads, configured or found
        through the library as the command finds them: the folder first, so
        a server refuses it before the manifest in it is read."""
        from wgsextract_cli.api._params import with_config_defaults
        from wgsextract_cli.core import acmg_data
        from wgsextract_cli.core.paths import expand_path
        from wgsextract_cli.core.reference_resolver import library_roots
        from wgsextract_cli.core.utils import WGSExtractError

        filled = with_config_defaults(self)
        sa_dir = acmg_data.acmg_sa_dir(library_roots(filled.ref).root, filled.sa_dir)
        if sa_dir is not None:
            yield "sa_dir", sa_dir
        if filled.gff3 is not None:
            yield "gff3", Path(expand_path(filled.gff3))
        elif sa_dir is not None:
            try:
                gff3 = acmg_data.recorded_gff3(sa_dir)
            except WGSExtractError:
                gff3 = None  # the run reports the unreadable manifest
            if gff3 is not None:
                yield "gff3", gff3

    def _site_data_paths(self) -> tuple[Path | None, Path | None]:
        """With gnomAD and SpliceAI read per run: the databases folder's
        sites/ cache, which the run writes, and the library's SpliceAI VCF,
        which it reads."""
        from wgsextract_cli.core import acmg_data
        from wgsextract_cli.core.reference_resolver import library_roots

        from ._params import with_config_defaults

        if self.site_data == "library":
            return None, None
        filled = with_config_defaults(self)
        root = library_roots(filled.ref).root
        sa_dir = acmg_data.acmg_sa_dir(root, filled.sa_dir)
        return (
            sa_dir / acmg_data.SITES_CACHE if sa_dir is not None else None,
            acmg_data.library_spliceai_path(root) if root else None,
        )

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        yield from super().implied_paths()
        for name, path in self._acmg_inputs():
            if getattr(self, name) is None:
                yield name, path
        for site_path in self._site_data_paths():
            if site_path is not None:
                yield "site_data", site_path

    def write_folders(self) -> Iterator[Path]:
        # fastVEP saves its transcript cache beside the GFF3.
        for name, path in self._acmg_inputs():
            if name == "gff3":
                yield path.parent
        cache, _ = self._site_data_paths()
        if cache is not None:
            yield cache


@dataclass(frozen=True, kw_only=True)
class VcfAlphamissenseParams(_AnnotatorParams):
    """AlphaMissense pathogenicity scores as ``alphamissense_annotated.vcf.gz``."""

    COMMAND = "vcf alphamissense"

    am_file: Path | None = _resource_param(
        "Path to AlphaMissense VCF data file.", "alphamissense_vcf_path"
    )
    min_score: float | None = _min_score_param("AlphaMissense", "0.5")


@dataclass(frozen=True, kw_only=True)
class VcfPharmgkbParams(_AnnotatorParams):
    """PharmGKB drug-response annotations as ``pharmgkb_annotated.vcf.gz``
    (GRCh38 only)."""

    COMMAND = "vcf pharmgkb"

    pharmgkb_file: Path | None = _resource_param(
        "Path to PharmGKB VCF or data file.", "pharmgkb_vcf_path"
    )


@dataclass(frozen=True, kw_only=True)
class VcfPharmcatParams(_VcfFileParams, DownloadParams):
    """Star-allele diplotypes and drug-response phenotypes from a GRCh38 VCF
    with PharmCAT: ``<stem>.report.html``, ``.report.json`` and
    ``.phenotype.json``. Missing PharmCAT reference FASTA needs ``download``;
    no sample data is sent to its public reference publisher."""

    COMMAND = "vcf pharmcat"

    # Local only: <ref>/pharmcat holds the pipeline it runs.
    ref: Path | None = ref_param(local_only=True)
    pharmcat_dir: Path | None = param(
        None,
        help="Extracted PharmCAT pipeline release (default: <reference "
        "library>/pharmcat).",
        path="directory",
        config_key="pharmcat_directory",
        local_only=True,
    )
    absent_to_ref: bool = param(
        False,
        help="Treat positions absent from the VCF as reference. Only for a "
        "whole-genome VCF that lists variants only (not a gVCF).",
        action="store_true",
    )
    outside_call: tuple[Path, ...] | None = param(
        None,
        help="PharmCAT outside-call file (gene<TAB>diplotype), e.g. from "
        "`typing cyp2d6` or `typing hla`. Repeatable; one call per gene.",
        metavar="FILE",
        path="file",
        action="append",
    )
    confidence_depth: tuple[float, float] | None = confidence_depth_param()
    confidence_gq: tuple[float, float] | None = confidence_gq_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        check_confidence(self)


@dataclass(frozen=True, kw_only=True)
class VcfChainAnnotateParams(_VcfFileParams):
    """Several annotations applied in turn, as ``chain_annotated.vcf.gz``;
    steps whose tool or data is missing are skipped with a warning."""

    COMMAND = "vcf chain-annotate"

    ref: Path | None = ref_param()
    annotations: str = param(
        "clinvar,revel,phylop,gnomad,vep",
        help="Comma-separated list of annotations to apply in order (default: "
        "clinvar,revel,phylop,gnomad,vep).",
    )
    keep_intermediates: bool = param(
        False,
        help="Keep intermediate VCF files generated during the chain process.",
        action="store_true",
    )
