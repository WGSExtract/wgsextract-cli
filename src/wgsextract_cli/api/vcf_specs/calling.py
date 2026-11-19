"""VCF calling parameter declarations and their field helpers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.api._params import Params, param
from wgsextract_cli.api._shared import (
    depth_param,
    dry_run_param,
    force_steps_param,
    input_param,
    no_gpu_memory_check_param,
    no_preflight_param,
    outdir_param,
    ref_param,
    restart_param,
    threads_param,
)
from wgsextract_cli.core.mimalloc import SETTINGS as MIMALLOC_SETTINGS
from wgsextract_cli.core.settings_schema import REFERENCE_EVIDENCE_MODES
from wgsextract_cli.core.utils import UsageError

from .shared import (
    _comma_items,
    _deepvariant_items,
    _public,
    _region_param,
    reference_evidence_param,
)


@dataclass(frozen=True, kw_only=True)
class _SmallVariantParams(Params):
    input: Path = input_param("BAM or CRAM to call.")
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param()
    threads: int | None = threads_param()
    ploidy_file: Path | None = param(
        None,
        help="File defining ploidy per chromosome (auto-resolved from --ref if possible)",
        path="file",
        exclusive="ploidy",
    )
    ploidy: str | None = param(
        None, help="Predefined ploidy name or value", exclusive="ploidy"
    )
    region: str | None = param(
        None,
        help="Chromosomal region (e.g. chrM, chrY:10000-20000)",
        flags=("-r", "--region"),
    )
    parallel: bool | None = param(
        None,
        help="Call contigs in parallel with up to --threads workers (default: on "
        "with --threads above 1). SNP calling recalls the stretches with sites "
        "deeper than 255 reads in one stream, because bcftools subsamples them "
        "with one random stream per process, so the calls match a single stream.",
        action="boolean_optional",
    )
    baq: bool | None = param(
        None,
        help="Enable heuristic base alignment quality (BAQ) in bcftools mpileup "
        "(slower; may reduce low-depth recall). Default: config baq, else off.",
        doc="Omit mpileup -B for both variant calls and optional reference "
        "evidence. This is heuristic BAQ, not full BAQ (-D) or redo BAQ (-E). "
        "Ultima input is rejected because its profile disables BAQ. "
        "Requires bcftools 1.13 or newer. None uses config baq, else off. "
        "See docs/variant-confidence.md.",
        action="boolean_optional",
        config_key="baq",
    )
    qc_min_depth: float | None = depth_param()
    reference_evidence: str | None = reference_evidence_param(
        "bcftools.g.vcf.gz", "a second, SNP+indel bcftools pass"
    )
    mimalloc: str | None = param(
        None,
        help="Apple Silicon: bcftools uses wgse-mimalloc if installed (auto) or "
        "not (off). Default: config mimalloc, else auto.",
        doc="auto: bcftools uses wgse-mimalloc on Apple Silicon if installed.",
        choices=MIMALLOC_SETTINGS,
        config_key="mimalloc",
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.ploidy is not None and self.ploidy_file is not None:
            raise UsageError("Use either --ploidy or --ploidy-file, not both.")


@dataclass(frozen=True, kw_only=True)
class VcfSnpParams(_SmallVariantParams):
    """Call SNPs from a BAM/CRAM into ``snps.vcf.gz``.

    Ploidy comes from ``ploidy`` or ``ploidy_file``, else the reference
    library's ploidy file, else bcftools' built-in GRCh37/GRCh38 table for
    the build of the input, or of a FASTA outside any library (its chr1
    length, ``.dict`` assembly, header description or file name).
    """

    COMMAND = "vcf snp"


@dataclass(frozen=True, kw_only=True)
class VcfIndelParams(_SmallVariantParams):
    """Call indels from a BAM/CRAM into ``indels.vcf.gz``; ploidy as for SNPs."""

    COMMAND = "vcf indel"


DEEPVARIANT_STEPS = (
    "stage_reads",
    "make_examples",
    "call_variants",
    "postprocess_variants",
)


DEEPVARIANT_MODELS = (
    "WGS",
    "WES",
    "PACBIO",
    "HYBRID_PACBIO_ILLUMINA",
    "ULTIMA",
    "ONT_R104",
)


SV_CALLERS = ("delly", "pbsv", "sniffles")


@dataclass(frozen=True, kw_only=True)
class _CallerParams(Params):
    input: Path = input_param("BAM or CRAM to call.")
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param()
    threads: int | None = threads_param()
    region: str | None = _region_param()


@dataclass(frozen=True, kw_only=True)
class VcfCnvParams(_CallerParams):
    """Call copy-number variants with delly into ``cnv.vcf.gz``."""

    COMMAND = "vcf cnv"

    map: str | None = param(
        None,
        help="Deprecated and ignored: Delly 2 no longer uses mappability maps.",
        flags=("-M", "--map"),
    )
    ploidy: int | None = param(None, help="Baseline ploidy for delly cnv (default: 2)")
    qc_min_depth: float | None = depth_param()


@dataclass(frozen=True, kw_only=True)
class VcfSvParams(_CallerParams):
    """Call structural variants into ``sv.vcf.gz`` (delly), ``pbsv.vcf.gz``
    or ``sniffles.vcf.gz``.

    ``caller`` picks the tool; otherwise Sniffles for ONT input (detected or
    ``ont``) and long reads, pbsv (else Sniffles) for PacBio input (detected
    or ``pacbio``), else delly. The ``caller_reason`` metric says why.
    """

    COMMAND = "vcf sv"

    caller: str | None = param(
        None,
        help="SV caller to use (default: sniffles for ONT or --long-read, "
        "pbsv/sniffles for PacBio, detected or flagged; else delly). "
        "pbsv is PacBio-only and is refused for ONT reads.",
        choices=SV_CALLERS,
    )
    pacbio: bool = param(
        False,
        help="PacBio HiFi/CCS data: use pbsv (or sniffles if pbsv is missing) "
        "with CCS thresholds. Detected PacBio gets this automatically, without "
        "CCS thresholds for subreads (CLR).",
        action="store_true",
    )
    ont: bool = param(
        False,
        help="Oxford Nanopore data: use Sniffles (detected ONT input selects it "
        "automatically).",
        action="store_true",
    )
    long_read: bool = param(
        False,
        help="Long-read data from any platform: use Sniffles unless --caller "
        "selects pbsv for PacBio reads.",
        action="store_true",
    )
    ccs: bool = param(
        False,
        help="Use pbsv CCS/HiFi thresholds when --caller pbsv is selected.",
        action="store_true",
    )
    tandem_repeats: Path | None = param(
        None,
        help="Optional tandem-repeat BED for pbsv discover or Sniffles "
        "(recommended for long-read SV calling).",
        path="file",
    )
    qc_min_depth: float | None = depth_param()


CALL_TYPES = ("snp", "indel", "sv", "cnv")


@_public
def call_types(value: str) -> list[str]:
    """``--types``' variant types, each once, in ``CALL_TYPES`` order."""
    given = {t.strip().lower() for t in value.split(",")} - {""}
    unknown = sorted(given - set(CALL_TYPES))
    if unknown or not given:
        raise UsageError(
            f"--types takes a comma-separated list of {', '.join(CALL_TYPES)}"
            + (f"; not {', '.join(unknown)}." if unknown else ".")
        )
    return [t for t in CALL_TYPES if t in given]


@_public
def _call_types_arg(text: str) -> str:
    """``--types`` checked as it is parsed, so a bad list is a usage error
    before the input is looked at."""
    import argparse

    try:
        return ",".join(call_types(text))
    except UsageError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


@dataclass(frozen=True, kw_only=True)
class VcfCallParams(_CallerParams):
    """Call the chosen variant types in one run, each into the file its own
    command writes: ``snps.vcf.gz`` (``vcf snp``), ``indels.vcf.gz``
    (``vcf indel``), the SV caller's VCF (``vcf sv``) and ``cnv.vcf.gz``
    (``vcf cnv``). With ``reference_evidence`` gvcf, SNPs and indels go into
    ``bcftools.g.vcf.gz`` alone. ``merge`` joins the parts, by position, into
    ``variants.vcf.gz`` (``variants.g.vcf.gz`` with the gVCF; output role
    ``merged_variants``) and removes them."""

    COMMAND = "vcf call"

    types: str = param(
        "snp,indel",
        help="Comma-separated variant types to call: snp and indel (bcftools), "
        "sv (as vcf sv picks the caller) and cnv (Delly). Default: snp,indel.",
        metavar="LIST",
        cli_type=_call_types_arg,
    )
    merge: bool = param(
        False,
        help="Merge the called types into one variants.vcf.gz (+ .tbi), or "
        "variants.g.vcf.gz with the gVCF, in place of each type's own VCF.",
        action="store_true",
    )
    reference_evidence: str | None = param(
        None,
        help="gvcf: write SNPs and indels as one gVCF, bcftools.g.vcf.gz "
        "(+ .tbi), with reference blocks, in place of snps.vcf.gz and "
        "indels.vcf.gz; none: the variant VCFs. Default: config "
        "reference_evidence, else none.",
        choices=REFERENCE_EVIDENCE_MODES,
        config_key="reference_evidence",
    )
    ploidy: str | None = param(
        None, help="SNP and indel ploidy name or value, as for vcf snp."
    )
    cnv_ploidy: int | None = param(
        None, help="CNV baseline ploidy, as vcf cnv's --ploidy (default: 2)."
    )
    sv_caller: str | None = param(
        None,
        help="SV caller (default: as vcf sv picks: sniffles for ONT or long "
        "reads, pbsv/sniffles for PacBio, else delly).",
        choices=SV_CALLERS,
    )
    pacbio: bool = param(
        False, help="PacBio HiFi/CCS data, as for vcf sv.", action="store_true"
    )
    ont: bool = param(
        False, help="Oxford Nanopore data, as for vcf sv.", action="store_true"
    )
    long_read: bool = param(
        False, help="Long-read data, as for vcf sv.", action="store_true"
    )
    ccs: bool = param(
        False, help="pbsv CCS/HiFi thresholds, as for vcf sv.", action="store_true"
    )
    tandem_repeats: Path | None = param(
        None,
        help="Tandem-repeat BED for pbsv or Sniffles, as for vcf sv.",
        path="file",
    )
    qc_min_depth: float | None = depth_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        call_types(self.types)


@dataclass(frozen=True, kw_only=True)
class VcfFreebayesParams(_CallerParams):
    """Call small variants with FreeBayes into ``freebayes.vcf.gz``."""

    COMMAND = "vcf freebayes"

    region: str | None = param(
        None,
        help="Chromosomal region (e.g. chrM, chrY:10000-20000)",
        flags=("-r", "--region"),
        splits=_comma_items,
    )
    qc_min_depth: float | None = depth_param()


@dataclass(frozen=True, kw_only=True)
class VcfGatkParams(_CallerParams):
    """Call small variants with GATK HaplotypeCaller into ``gatk.vcf.gz``
    (flow mode for Ultima reads), or with Parabricks on a GPU."""

    COMMAND = "vcf gatk"

    region: str | None = param(
        None,
        help="Chromosomal region (e.g. chrM)",
        flags=("-r", "--region"),
        doc="Comma-separated region(s) to call (e.g. chrM), or a BED file.",
        splits=_comma_items,
    )
    scatter: bool = param(
        False,
        help="Opt-in Ultima flow-mode interval scattering, bounded by --threads "
        "and available RAM (6 GiB per worker, 4 GiB Java heap). "
        "Can change genotypes/annotations away from interval boundaries; "
        "default: one unscattered process.",
        action="store_true",
    )
    gpu: bool = param(
        False,
        help="Call on an NVIDIA GPU with Parabricks (Linux, Docker; see README).",
        action="store_true",
    )
    pair_hmm_cuda: Path | None = param(
        None,
        help="Experimental Linux x86-64 CUDA PairHMM sidecar jar for GATK "
        "4.6.2.0, with GKL CPU fallback. Opt-in; not Parabricks. "
        "See native/pairhmm/README.md. Not with --gpu or --scatter.",
        path="file",
    )
    no_gpu_memory_check: bool = no_gpu_memory_check_param()
    qc_min_depth: float | None = depth_param()
    reference_evidence: str | None = reference_evidence_param(
        "gatk.g.vcf.gz",
        "little time; the VCF is then genotyped from it; not with --gpu or --scatter",
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.pair_hmm_cuda is not None and (self.gpu or self.scatter):
            raise UsageError(
                "--pair-hmm-cuda cannot be combined with --gpu or --scatter."
            )


@dataclass(frozen=True, kw_only=True)
class VcfDeepvariantParams(_CallerParams):
    """Call small variants with DeepVariant into ``deepvariant.vcf.gz``.

    The model follows ``ont``, ``pacbio``, ``model_type`` and ``wes``, else the
    reads (ONT_R104 for ONT, ULTIMA for flow-tagged Ultima reads, else WGS).
    DeepVariant's own pipeline runs in stages that a rerun resumes. ``gpu``
    runs Parabricks in one step, except for ULTIMA, which Parabricks lacks:
    Google's ULTIMA GPU image runs the stages with call_variants on the GPU.
    """

    COMMAND = "vcf deepvariant"

    region: str | None = param(
        None,
        help="Chromosomal region (e.g. chrM)",
        flags=("-r", "--region"),
        doc="Region(s) to call (e.g. chrM) or BED files of them, separated by "
        "spaces (commas with gpu).",
        splits=_deepvariant_items,
    )
    wes: bool = param(
        False, help="Set model type to WES (default: WGS)", action="store_true"
    )
    model_type: str | None = param(
        None,
        help="DeepVariant model type (default: ONT_R104 for detected ONT, "
        "ULTIMA for Ultima reads with their flow tags, else WGS). "
        "ONT_R104 supports R10.4.1 only and requires x86-64; "
        "Linux ARM64 supports WGS only. Use PACBIO for PacBio HiFi/CCS alignments.",
        choices=DEEPVARIANT_MODELS,
    )
    ont: bool = param(
        False,
        help="Alias for --model-type ONT_R104 (R10.4.1 only; x86-64).",
        action="store_true",
    )
    pacbio: bool = param(
        False, help="Alias for --model-type PACBIO.", action="store_true"
    )
    checkpoint: Path | None = param(
        None,
        help="Path to DeepVariant model checkpoint",
        doc="A custom model: its directory, or a checkpoint prefix.",
        path="file_or_directory",
    )
    batch_size: int | None = param(
        None,
        help="call_variants batch size (upstream default: 1024; not Parabricks). "
        "Use 256 to lower TensorFlow peak RAM without changing models or precision; "
        "--threads controls make_examples shards, not this batch.",
    )
    gpu: bool = param(
        False,
        help="NVIDIA GPU (x86-64 Linux): WGS/ONT via wgse-deepvariant if set up, "
        "else Parabricks; Ultima: Google's GPU image (README).",
        action="store_true",
    )
    no_gpu_memory_check: bool = no_gpu_memory_check_param()
    no_preflight: bool = no_preflight_param()
    qc_min_depth: float | None = depth_param()
    reference_evidence: str | None = reference_evidence_param(
        "deepvariant.g.vcf.gz", "~5 percent more time, ~25 with Parabricks"
    )
    restart: bool = restart_param()
    force_steps: tuple[str, ...] | None = force_steps_param(DEEPVARIANT_STEPS)
    dry_run: bool = dry_run_param()


@dataclass(frozen=True, kw_only=True)
class VcfClair3Params(_CallerParams):
    """Call small variants in Nanopore reads with Clair3 into
    ``clair3.vcf.gz``."""

    COMMAND = "vcf clair3"

    region: str | None = param(
        None,
        help="Chromosomal region (e.g. chr20:1-1000000)",
        flags=("-r", "--region"),
        splits=_comma_items,
    )
    model: str | None = param(
        None,
        help="Clair3 model directory, or the name of a bundled model, matching the "
        "reads' basecaller (e.g. r1041_e82_400bps_sup_v500). Overrides config "
        "clair3_model; default: chosen from the basecaller metadata.",
        metavar="NAME|DIR",
        may_name_file=True,
    )
    gpu: bool = param(
        False,
        help="Call on an NVIDIA GPU in HKU's Clair3 GPU image (x86-64 Linux, "
        "Docker; see docs/ont.md).",
        action="store_true",
    )
    no_preflight: bool = no_preflight_param()
    qc_min_depth: float | None = depth_param()
    reference_evidence: str | None = reference_evidence_param(
        "clair3.g.vcf.gz", "~2.4x the time on a CPU, ~4.3x on a GPU"
    )


PHASERS = ("whatshap", "longphase")


@dataclass(frozen=True, kw_only=True)
class VcfPhaseParams(_CallerParams):
    """Phase one sample's heterozygous calls with its reads into
    ``phased.vcf.gz``, with WhatsHap or LongPhase.

    ``input`` is the sample's BAM/CRAM and ``vcf_input`` its calls. The phase
    blocks go to ``phased.blocks.tsv``; ``haplotag`` also writes the reads with
    HP/PS tags to ``haplotagged.bam`` (or ``.cram``).
    """

    COMMAND = "vcf phase"

    input: Path = input_param("BAM or CRAM of the sample's reads.")
    vcf_input: Path | None = param(
        None,
        help="VCF of the sample's calls to phase (default: config default_input_vcf)",
        path="file",
        config_key="default_input_vcf",
        doc="The calls to phase, read with ``input`` (not instead of it).",
    )
    region: str | None = _region_param(
        "Phase only these regions (e.g. chr20 or chr20:1-1000000, comma-separated)"
    )
    phaser: str | None = param(
        None,
        help="Phasing tool (default: longphase for Nanopore and PacBio reads when "
        "it is installed, else whatshap). LongPhase is refused for other reads.",
        choices=PHASERS,
    )
    haplotag: bool = param(
        False,
        help="Also write the reads tagged with their haplotype (HP) and phase "
        "block (PS) to haplotagged.bam (.cram for CRAM input).",
        action="store_true",
    )
    qc_min_depth: float | None = depth_param()

    def write_folders(self) -> Iterator[Path]:
        # With no outdir, the outputs go beside the reads, not the VCF.
        if self.outdir is None:
            yield Path(self.input).expanduser().absolute().parent
