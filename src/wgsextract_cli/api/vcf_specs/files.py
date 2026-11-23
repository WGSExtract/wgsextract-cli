"""VCF files parameter declarations and their field helpers."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.api._params import Params, param
from wgsextract_cli.api._shared import (
    check_confidence,
    confidence_depth_param,
    confidence_gq_param,
    memory_param,
    optional_input_param,
    outdir_param,
    ref_param,
    threads_param,
)
from wgsextract_cli.core.messages import CLI_HELP
from wgsextract_cli.core.panel_presets import PRESETS
from wgsextract_cli.core.paths import append_suffix
from wgsextract_cli.core.table_writer import FORMATS, table_format_type
from wgsextract_cli.core.utils import UsageError

from .shared import (
    _region_param,
    _resource_param,
    _vcf_input_param,
    _VcfFileParams,
)

HAPLOTYPES = ("iupac", "alt", "1", "2")


@dataclass(frozen=True, kw_only=True)
class VcfAnnotateParams(_VcfFileParams):
    """Copy columns from an annotation VCF or tab file into ``annotated.vcf.gz``."""

    COMMAND = "vcf annotate"

    vcf_input: Path | None = _vcf_input_param(CLI_HELP["arg_vcf_input"])
    ref: Path | None = ref_param()
    ann_vcf: Path | None = param(
        None,
        help="Annotation VCF file (auto-resolved from --ref if possible)",
        path="file",
    )
    cols: str | None = param(
        None,
        help="Columns to annotate (e.g. ID,INFO/HG)",
        doc="bcftools annotate -c columns; default: inferred from the "
        "annotation file's header.",
    )


@dataclass(frozen=True, kw_only=True)
class _VcfSliceParams(_VcfFileParams):
    ref: Path | None = ref_param()
    gene: str | None = param(
        None, help="Keep a gene by symbol (repeatable).", action="append"
    )
    genes: tuple[str, ...] = param(
        (), help="Comma-separated gene symbols (repeatable).", action="append"
    )
    gene_file: Path | None = param(
        None,
        help="File of gene symbols (whitespace or comma-separated; # starts a comment).",
        path="file",
        metavar="FILE",
    )
    presets: tuple[str, ...] = param(
        (),
        help="Gene panel preset (repeatable): " + ", ".join(PRESETS),
        flags=("--preset",),
        choices=tuple(PRESETS),
        action="append",
        metavar="NAME",
    )
    bed: Path | None = param(
        None,
        help="BED or BED.gz panel, unioned with the selected gene spans.",
        path="file",
        metavar="FILE",
    )
    pad_kb: float = param(
        0.0,
        help="Padding on each side of a gene span, in kb (default: 0).",
        minimum=0,
    )
    region: str | None = _region_param(
        "Chromosomal region(s); when a panel is given, restrict its union to these regions."
    )

    def __post_init__(self) -> None:
        # Repeated CLI --gene values and the existing scalar API field have
        # the same meaning as a comma-separated list.
        if isinstance(self.gene, tuple):
            object.__setattr__(self, "gene", ",".join(self.gene))
        for name in ("genes", "presets"):
            object.__setattr__(self, name, tuple(getattr(self, name) or ()))
        super().__post_init__()
        if not math.isfinite(self.pad_kb):
            raise UsageError("--pad-kb must be finite.")
        if self.pad_kb and not self.has_gene_panel:
            raise UsageError(
                "--pad-kb requires --gene, --genes, --gene-file or --preset."
            )

    @property
    def has_gene_panel(self) -> bool:
        return self.gene is not None or bool(
            self.genes or self.gene_file or self.presets
        )

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        if self.has_gene_panel and self.ref is not None and not self.ref.is_dir():
            yield "ref_index", append_suffix(self.ref, ".fai")


@dataclass(frozen=True, kw_only=True)
class VcfFilterParams(_VcfSliceParams):
    """Keep the calls in a region or panel, matching an expression, into
    ``filtered.vcf.gz``."""

    COMMAND = "vcf filter"

    vcf_input: Path | None = _vcf_input_param(CLI_HELP["arg_vcf_input"])
    expr: str | None = param(
        None, help="bcftools filter expression (e.g. 'QUAL>30')", expression=True
    )
    exclude_near_gaps: bool = param(
        False,
        help="Exclude variants in or near genomic gaps (requires Count Ns output)",
        action="store_true",
    )


@dataclass(frozen=True, kw_only=True)
class VcfTableParams(_VcfSliceParams):
    """One row per variant with its annotations, as ``<stem>_variants.tsv``
    (or ``.parquet``) and an SQLite database."""

    COMMAND = "vcf table"

    vcf_input: Path | None = _vcf_input_param(CLI_HELP["arg_vcf_input"])
    sample: str | None = param(
        None, help="Sample whose genotypes to use (default: the first)."
    )
    all_alleles: bool = param(
        False,
        help="Also export ALT alleles the sample doesn't carry (0/0 calls, "
        "no-calls, the other ALT of a 0/1 multi-allelic site).",
        action="store_true",
    )
    pharmcat_json: Path | None = param(
        None,
        help="PharmCAT's <stem>.phenotype.json (from 'vcf pharmcat'), to add its "
        "diplotype and phenotype to rows in those genes.",
        metavar="FILE",
        path="file",
    )
    no_sqlite: bool = param(
        False, help="Write only the table file.", action="store_true"
    )
    table_format: str = param(
        "tsv",
        help="Table file format: tsv, or parquet (needs pyarrow) (default: tsv)",
        flags=("--format",),
        choices=FORMATS,
        cli_type=table_format_type(),
    )


@dataclass(frozen=True, kw_only=True)
class VcfCallabilityParams(_VcfFileParams):
    """Which coding exons of a gene list the sample's reference evidence (a
    gVCF) could read, as ``exon_callability.tsv`` (one row per exon) and
    ``exon_callability.json`` (per gene, with the sources and thresholds).
    The input is a gVCF, or a VCF whose caller also wrote one
    (``--reference-evidence gvcf``). A technical result, not a clinical test:
    see docs/exon-callability.md."""

    COMMAND = "vcf callability"

    vcf_input: Path | None = _vcf_input_param(
        "Input gVCF, or a VCF whose caller wrote one (--reference-evidence gvcf)."
    )
    gene_list: str | None = param(
        None,
        help="Genes to assess: a built-in list (acmg-sf-3.3, acmg-sf-3.2), or a "
        "TSV (column gene) or BED file (gene symbol in column 4) (default: "
        "config report_gene_list, else acmg-sf-3.3)",
        metavar="LIST",
        config_key="report_gene_list",
        may_name_file=True,
    )
    exons: Path | None = param(
        None,
        help="Exon model TSV for other genes or builds (format: "
        "docs/exon-callability.md) (default: the bundled hg19/hg38 MANE or "
        "RefSeq Select coding exons of the built-in lists' genes)",
        metavar="FILE",
        path="file",
    )
    flank: int = param(
        2,
        help="Intronic bases assessed on each side of a coding exon, 0-50 "
        "(default 2: the splice-site dinucleotides)",
        minimum=0,
    )
    confidence_depth: tuple[float, float] | None = confidence_depth_param()
    confidence_gq: tuple[float, float] | None = confidence_gq_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not 0 <= self.flank <= 50:
            raise UsageError("--flank must be between 0 and 50.")
        check_confidence(self)


@dataclass(frozen=True, kw_only=True)
class VcfToFastaParams(_VcfFileParams):
    """The reference with the VCF's variants applied, as
    ``<stem>_consensus.fa.gz`` (and optionally a liftover chain)."""

    COMMAND = "vcf to-fasta"

    vcf_input: Path | None = _vcf_input_param(CLI_HELP["arg_vcf_input"])
    ref: Path | None = ref_param()
    threads: int | None = threads_param()
    region: str | None = _region_param(
        "Only build this region (e.g. chr21 or 21:30000000-31000000); "
        "default: the whole reference."
    )
    sample: str | None = param(None, help="Sample to use from a multi-sample VCF.")
    haplotype: str = param(
        "iupac",
        help="How genotypes are applied: iupac (default) writes heterozygous "
        "SNVs as IUPAC codes (e.g. R for A/G) and applies homozygous ALTs; alt "
        "applies every ALT; 1 or 2 applies that haplotype of a phased VCF (for "
        "unphased genotypes, the first or second allele, so 1 keeps the "
        "reference at heterozygous sites).",
        choices=HAPLOTYPES,
    )
    uncalled: str = param(
        "auto",
        help="What to write where the VCF has no passing call: n (an N), ref "
        "(the reference base), or auto (default): n for a gVCF, whose reference "
        "blocks say which positions were called, ref for a variant-only VCF.",
        choices=("auto", "n", "ref"),
    )
    include_filtered: bool = param(
        False,
        help="Also apply variants whose FILTER is not PASS.",
        action="store_true",
    )
    mask: Path | None = param(
        None,
        help="BED file of regions to replace with N (e.g. low coverage).",
        path="file",
    )
    chain: bool = param(
        False,
        help="Also write a chain file mapping reference coordinates to the "
        "consensus (indels shift positions).",
        action="store_true",
    )


@dataclass(frozen=True, kw_only=True)
class VcfTrioParams(_VcfFileParams):
    """Calls matching an inheritance mode in a child and both parents, as
    ``trio_<mode>.vcf.gz``. Three VCFs are merged; three BAM/CRAMs are first
    genotyped jointly from the reads (``trio_joint.vcf.gz``), which de novo
    calls need. VCF de novo and comphet classification requires complete
    parental genotypes with GQ >=20 and no failed FORMAT/FT filter; missing,
    filtered or weak evidence is written to
    ``trio_<mode>_inconclusive.vcf.gz``, preserving the parental genotypes.
    Sparse variant-only VCFs do not establish parental reference calls.
    Covering gVCF reference blocks are assessed when a matching --ref is given."""

    COMMAND = "vcf trio"

    vcf_input: Path | None = _vcf_input_param(CLI_HELP["arg_vcf_input"])
    ref: Path | None = ref_param()
    threads: int | None = threads_param()
    mother: Path | None = _resource_param(CLI_HELP["arg_mother"], "mother_vcf_path")
    father: Path | None = _resource_param(CLI_HELP["arg_father"], "father_vcf_path")
    proband: Path | None = param(
        None,
        help="The child's VCF or BAM/CRAM; overrides --vcf-input/--input and "
        "configured defaults. With the parents' BAM/CRAMs, genotyped jointly "
        "from the reads, which de novo calls need.",
        path="file",
    )
    mode: str = param(
        "denovo",
        help="Inheritance mode to filter for",
        choices=("denovo", "recessive", "comphet", "all"),
    )
    min_depth: int | None = param(
        None,
        help="BAM/CRAM input: minimum read depth in each person at a reported site "
        "(default 15)",
    )
    proband_sex: str | None = param(
        None,
        help="BAM/CRAM input: the child's sex, for chrX ploidy (default: inferred "
        "from chrX/chrY coverage)",
        choices=("male", "female"),
    )
    region: str | None = _region_param()


@dataclass(frozen=True, kw_only=True)
class VcfImputeParams(Params):
    """Impute one GRCh38 sample's genotypes from the 1000 Genomes 30x panel
    into ``imputed.vcf.gz``: low-coverage reads (``input``) with GLIMPSE2, or
    a genotype-only VCF such as microarray calls (``vcf_input``) with Beagle 5.

    Every imputed record carries INFO/IMP, the header a
    ``##wgsextract_imputation`` line, and records whose most likely genotype
    has GP below ``min_gp`` (GLIMPSE2: 0.9, Beagle: 0.95) are FILTERed
    ``ImpLowConf``. Site INFO/DR2 is not a single-sample confidence measure:
    ``min_info`` is an explicit opt-in site filter, meaningful only for
    multi-sample cohorts. Install panel chromosomes with ``ref imputation``.

    GLIMPSE2 runs ``threads`` chunks at once, one thread each, largest
    first, as many as fit the memory available (or ``memory`` per chunk);
    the output doesn't depend on how many.
    """

    COMMAND = "vcf impute"

    input: Path | None = optional_input_param(
        "Low-coverage BAM or CRAM to impute with GLIMPSE2 (about 0.5-8x)."
    )
    vcf_input: Path | None = param(
        None,
        help="Genotype-only VCF (e.g. chip calls) to impute with Beagle instead.",
        path="file",
    )
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param()
    threads: int | None = threads_param()
    memory: str | None = memory_param()
    chromosomes: str | None = param(
        None,
        help="Chromosomes, e.g. chr20 or chr1-22 (default: every installed one).",
    )
    min_gp: float | None = param(
        None,
        help="Minimum max GP (default: GLIMPSE2 0.9; Beagle 0.95).",
        minimum=0.0,
    )
    min_info: float | None = param(
        None,
        help="Opt-in INFO/DR2 site filter; meaningful only for multi-sample cohorts.",
        minimum=0.0,
    )

    @property
    def gp_threshold(self) -> float:
        """Effective per-genotype threshold, including explicit zero."""
        if self.min_gp is not None:
            return self.min_gp
        return 0.9 if self.input is not None else 0.95

    def __post_init__(self) -> None:
        super().__post_init__()
        if (self.input is None) == (self.vcf_input is None):
            raise UsageError(
                "Give either --input (low-coverage reads) or --vcf-input "
                "(genotype-only VCF) to impute."
            )

    def write_folders(self) -> Iterator[Path]:
        if self.outdir is None:
            source = self.input or self.vcf_input
            assert source is not None
            yield Path(source).expanduser().absolute().parent

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        from wgsextract_cli.api._params import with_config_defaults
        from wgsextract_cli.api._ref_library import _library_named
        from wgsextract_cli.core import imputation_panel
        from wgsextract_cli.core.reference_assets import (
            resolve_reference_library,
        )

        ref = with_config_defaults(self).ref
        if ref is not None:
            yield "ref", ref.expanduser()
        library = (
            _library_named(ref)
            if ref is not None
            else Path(resolve_reference_library())
        )
        yield "ref", library
        for path in imputation_panel.guarded_paths(str(library), self.chromosomes):
            yield "ref", path
