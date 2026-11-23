"""``vcf``: call variants, and annotate, filter, tabulate and analyse VCFs.

``snp`` and ``indel`` call small variants from a BAM/CRAM with bcftools;
``freebayes``, ``gatk``, ``deepvariant`` and ``clair3`` with those tools, and
``cnv`` and ``sv`` call copy-number and structural variants, and ``phase``
phases a sample's calls with its reads. The other operations read a VCF:
``vcf_input``, else ``input``. Their outputs go to ``outdir``, else beside
that VCF. Annotators take their data file from the
field, else config.toml, else the reference library (``ref``); see
``wgsextract ref`` to download it."""

from __future__ import annotations

# Keep facade declarations ordered for stable public API documentation.
# ruff: noqa: E402
from collections.abc import Iterator as Iterator
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
from typing import Any as Any

from wgsextract_cli.core.messages import CLI_HELP as CLI_HELP
from wgsextract_cli.core.mimalloc import SETTINGS as MIMALLOC_SETTINGS  # noqa: F401
from wgsextract_cli.core.results import CommandResult as CommandResult
from wgsextract_cli.core.settings_schema import (
    REFERENCE_EVIDENCE_MODES as REFERENCE_EVIDENCE_MODES,
)
from wgsextract_cli.core.table_writer import FORMATS as FORMATS
from wgsextract_cli.core.table_writer import table_format_type as table_format_type
from wgsextract_cli.core.utils import UsageError as UsageError

from ._network import DownloadParams as DownloadParams
from ._params import Params as Params
from ._params import param as param
from ._runtime import CancelToken as CancelToken
from ._runtime import ProgressCallback as ProgressCallback
from ._runtime import execute as execute
from ._shared import check_confidence as check_confidence
from ._shared import confidence_depth_param as confidence_depth_param
from ._shared import confidence_gq_param as confidence_gq_param
from ._shared import depth_param as depth_param
from ._shared import dry_run_param as dry_run_param
from ._shared import force_steps_param as force_steps_param
from ._shared import input_param as input_param
from ._shared import memory_param as memory_param
from ._shared import no_gpu_memory_check_param as no_gpu_memory_check_param
from ._shared import no_preflight_param as no_preflight_param
from ._shared import optional_input_param as optional_input_param
from ._shared import outdir_param as outdir_param
from ._shared import ref_param as ref_param
from ._shared import restart_param as restart_param
from ._shared import threads_param as threads_param
from .vcf_specs.calling import VcfIndelParams as VcfIndelParams
from .vcf_specs.calling import VcfSnpParams as VcfSnpParams
from .vcf_specs.calling import _SmallVariantParams as _SmallVariantParams
from .vcf_specs.shared import reference_evidence_param as reference_evidence_param


def snp(
    params: VcfSnpParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Call SNPs; the ``snps`` output carries the record count in its metrics."""
    from wgsextract_cli.commands.vcf.basic import run_small_variants

    return execute(
        params, run_small_variants, progress=progress, cancel=cancel, check=check
    )


def indel(
    params: VcfIndelParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Call indels; the ``indels`` output carries the record count in its metrics."""
    from wgsextract_cli.commands.vcf.basic import run_small_variants

    return execute(
        params, run_small_variants, progress=progress, cancel=cancel, check=check
    )


from .vcf_specs.annotations import VcfAcmgParams as VcfAcmgParams
from .vcf_specs.annotations import VcfAlphamissenseParams as VcfAlphamissenseParams
from .vcf_specs.annotations import VcfCaddParams as VcfCaddParams
from .vcf_specs.annotations import VcfChainAnnotateParams as VcfChainAnnotateParams
from .vcf_specs.annotations import VcfClinvarParams as VcfClinvarParams
from .vcf_specs.annotations import VcfGerpParams as VcfGerpParams
from .vcf_specs.annotations import VcfGnomadParams as VcfGnomadParams
from .vcf_specs.annotations import VcfPharmcatParams as VcfPharmcatParams
from .vcf_specs.annotations import VcfPharmgkbParams as VcfPharmgkbParams
from .vcf_specs.annotations import VcfPhylopParams as VcfPhylopParams
from .vcf_specs.annotations import VcfRevelParams as VcfRevelParams
from .vcf_specs.annotations import VcfSpliceaiParams as VcfSpliceaiParams
from .vcf_specs.annotations import VcfSvFreqParams as VcfSvFreqParams
from .vcf_specs.compare import VcfCompareParams as VcfCompareParams
from .vcf_specs.files import HAPLOTYPES as HAPLOTYPES
from .vcf_specs.files import VcfAnnotateParams as VcfAnnotateParams
from .vcf_specs.files import VcfFilterParams as VcfFilterParams
from .vcf_specs.files import VcfTableParams as VcfTableParams
from .vcf_specs.files import VcfToFastaParams as VcfToFastaParams
from .vcf_specs.files import VcfTrioParams as VcfTrioParams
from .vcf_specs.shared import _OVERRIDE_HELP as _OVERRIDE_HELP
from .vcf_specs.shared import _AnnotatorParams as _AnnotatorParams
from .vcf_specs.shared import _comma_items as _comma_items
from .vcf_specs.shared import _deepvariant_items as _deepvariant_items
from .vcf_specs.shared import _min_score_param as _min_score_param
from .vcf_specs.shared import _region_param as _region_param
from .vcf_specs.shared import _resource_param as _resource_param
from .vcf_specs.shared import _vcf_input_param as _vcf_input_param
from .vcf_specs.shared import _VcfFileParams as _VcfFileParams


def annotate(
    params: VcfAnnotateParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate; output role ``annotated``, with the record count in its metrics."""
    from wgsextract_cli.commands.vcf.basic import run_annotate

    return execute(params, run_annotate, progress=progress, cancel=cancel, check=check)


def filter(
    params: VcfFilterParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Filter; output role ``filtered_vcf``, with the record count in its metrics."""
    from wgsextract_cli.commands.vcf.filter_trio import run_filter

    return execute(params, run_filter, progress=progress, cancel=cancel, check=check)


def table(
    params: VcfTableParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Write the table (role ``variant_table``) and database (``variant_database``);
    ``rows`` is a metric."""
    from wgsextract_cli.commands.vcf.table import run_table

    return execute(params, run_table, progress=progress, cancel=cancel, check=check)


def compare(
    params: VcfCompareParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Write local allele concordance tables and optionally an indexed anchor VCF."""
    from wgsextract_cli.commands.vcf.compare import run_compare

    return execute(params, run_compare, progress=progress, cancel=cancel, check=check)


def to_fasta(
    params: VcfToFastaParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Build the consensus (role ``consensus_fasta``, and ``liftover_chain`` with
    ``chain``)."""
    from wgsextract_cli.commands.vcf.consensus import run_to_fasta

    return execute(params, run_to_fasta, progress=progress, cancel=cancel, check=check)


def trio(
    params: VcfTrioParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Filter the trio; output roles ``trio_<mode>`` and, for VCF de novo/comphet,
    ``trio_<mode>_inconclusive`` (and ``trio_joint``,
    ``trio_denovo_isolated`` from BAM/CRAMs)."""
    from wgsextract_cli.commands.vcf.filter_trio import run_trio

    return execute(params, run_trio, progress=progress, cancel=cancel, check=check)


def clinvar(
    params: VcfClinvarParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate with ClinVar; output roles ``annotated_vcf`` and
    ``clinvar_pathogenic``."""
    from wgsextract_cli.commands.vcf.clinvar_revel import run_clinvar

    return execute(params, run_clinvar, progress=progress, cancel=cancel, check=check)


def revel(
    params: VcfRevelParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate with REVEL; output roles ``annotated_vcf`` and, with ``min_score``,
    ``filtered_vcf``."""
    from wgsextract_cli.commands.vcf.clinvar_revel import run_revel

    return execute(params, run_revel, progress=progress, cancel=cancel, check=check)


def phylop(
    params: VcfPhylopParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate with PhyloP; output roles ``annotated_vcf`` and, with ``min_score``,
    ``filtered_vcf``."""
    from wgsextract_cli.commands.vcf.population import run_phylop

    return execute(params, run_phylop, progress=progress, cancel=cancel, check=check)


def cadd(
    params: VcfCaddParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate with CADD; output roles ``annotated_vcf`` and, with ``min_score``,
    ``filtered_vcf``."""
    from wgsextract_cli.commands.vcf.deleteriousness import run_cadd

    return execute(params, run_cadd, progress=progress, cancel=cancel, check=check)


def gerp(
    params: VcfGerpParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate with GERP++; output roles ``annotated_vcf`` and, with ``min_score``,
    ``filtered_vcf``."""
    from wgsextract_cli.commands.vcf.deleteriousness import run_gerp

    return execute(params, run_gerp, progress=progress, cancel=cancel, check=check)


def gnomad(
    params: VcfGnomadParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate with gnomAD; output roles ``annotated_vcf`` and, with ``max_af``,
    ``rare_variants``."""
    from wgsextract_cli.commands.vcf.population import run_gnomad

    return execute(params, run_gnomad, progress=progress, cancel=cancel, check=check)


def sv_freq(
    params: VcfSvFreqParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate SVs with gnomAD-SV/gnomAD-LR frequencies; output roles
    ``annotated_vcf`` and ``sv_frequencies`` (the table), with the counts per
    frequency class in the metrics."""
    from wgsextract_cli.commands.vcf.sv_frequency import run_sv_freq

    return execute(params, run_sv_freq, progress=progress, cancel=cancel, check=check)


def spliceai(
    params: VcfSpliceaiParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate with SpliceAI; output role ``annotated_vcf``."""
    from wgsextract_cli.commands.vcf.splicing import run_spliceai

    return execute(params, run_spliceai, progress=progress, cancel=cancel, check=check)


def acmg(
    params: VcfAcmgParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Classify with fastVEP (research only); output roles ``annotated_vcf``
    and ``acmg_summary``, with the class counts in the metrics."""
    from wgsextract_cli.commands.vcf.acmg import run_acmg

    return execute(params, run_acmg, progress=progress, cancel=cancel, check=check)


def alphamissense(
    params: VcfAlphamissenseParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate with AlphaMissense; output roles ``annotated_vcf`` and, with
    ``min_score``, ``filtered_vcf``."""
    from wgsextract_cli.commands.vcf.splicing import run_alphamissense

    return execute(
        params, run_alphamissense, progress=progress, cancel=cancel, check=check
    )


def pharmgkb(
    params: VcfPharmgkbParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Annotate with PharmGKB; output role ``annotated_vcf``."""
    from wgsextract_cli.commands.vcf.splicing import run_pharmgkb

    return execute(params, run_pharmgkb, progress=progress, cancel=cancel, check=check)


def pharmcat(
    params: VcfPharmcatParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Run PharmCAT; output roles ``pharmcat_report``, ``pharmcat_report_json`` and
    ``pharmcat_phenotypes``."""
    from wgsextract_cli.commands.vcf.pharmcat import run_pharmcat

    return execute(params, run_pharmcat, progress=progress, cancel=cancel, check=check)


def chain_annotate(
    params: VcfChainAnnotateParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Run the chain; output role ``annotated_vcf``; ``annotations_applied`` is a
    metric."""
    from wgsextract_cli.commands.vcf.chain import run_chain_annotate

    return execute(
        params, run_chain_annotate, progress=progress, cancel=cancel, check=check
    )


__all__ = [
    "HAPLOTYPES",
    "VcfAcmgParams",
    "VcfAlphamissenseParams",
    "VcfAnnotateParams",
    "VcfCaddParams",
    "VcfChainAnnotateParams",
    "VcfClinvarParams",
    "VcfFilterParams",
    "VcfGerpParams",
    "VcfGnomadParams",
    "VcfIndelParams",
    "VcfPharmcatParams",
    "VcfPharmgkbParams",
    "VcfPhylopParams",
    "VcfRevelParams",
    "VcfSnpParams",
    "VcfSpliceaiParams",
    "VcfTableParams",
    "VcfCompareParams",
    "VcfToFastaParams",
    "VcfTrioParams",
    "acmg",
    "alphamissense",
    "annotate",
    "cadd",
    "chain_annotate",
    "clinvar",
    "filter",
    "gerp",
    "gnomad",
    "indel",
    "pharmcat",
    "pharmgkb",
    "phylop",
    "revel",
    "snp",
    "spliceai",
    "table",
    "compare",
    "to_fasta",
    "trio",
]


# The stages of a CPU DeepVariant run, and of its step journal.
from .vcf_specs.calling import CALL_TYPES as CALL_TYPES
from .vcf_specs.calling import DEEPVARIANT_MODELS as DEEPVARIANT_MODELS
from .vcf_specs.calling import DEEPVARIANT_STEPS as DEEPVARIANT_STEPS
from .vcf_specs.calling import PHASERS as PHASERS
from .vcf_specs.calling import SV_CALLERS as SV_CALLERS
from .vcf_specs.calling import VcfCallParams as VcfCallParams
from .vcf_specs.calling import VcfClair3Params as VcfClair3Params
from .vcf_specs.calling import VcfCnvParams as VcfCnvParams
from .vcf_specs.calling import VcfDeepvariantParams as VcfDeepvariantParams
from .vcf_specs.calling import VcfFreebayesParams as VcfFreebayesParams
from .vcf_specs.calling import VcfGatkParams as VcfGatkParams
from .vcf_specs.calling import VcfPhaseParams as VcfPhaseParams
from .vcf_specs.calling import VcfSvParams as VcfSvParams
from .vcf_specs.calling import _call_types_arg as _call_types_arg
from .vcf_specs.calling import _CallerParams as _CallerParams
from .vcf_specs.calling import call_types as call_types
from .vcf_specs.files import VcfImputeParams as VcfImputeParams


def cnv(
    params: VcfCnvParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Call CNVs; output role ``copy_number_variants``."""
    from wgsextract_cli.commands.vcf.structural import run_cnv

    return execute(params, run_cnv, progress=progress, cancel=cancel, check=check)


def sv(
    params: VcfSvParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Call SVs; output role ``structural_variants``, the ``caller`` metric
    names the tool chosen and ``caller_reason`` why."""
    from wgsextract_cli.commands.vcf.structural import run_sv

    return execute(params, run_sv, progress=progress, cancel=cancel, check=check)


def call(
    params: VcfCallParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Call the chosen types; each type's output carries its own role, and
    ``merge`` adds ``merged_variants``."""
    from wgsextract_cli.commands.vcf.call import run_call

    return execute(params, run_call, progress=progress, cancel=cancel, check=check)


def freebayes(
    params: VcfFreebayesParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Call with FreeBayes; output role ``variants``."""
    from wgsextract_cli.commands.vcf.callers import run_freebayes

    return execute(params, run_freebayes, progress=progress, cancel=cancel, check=check)


def gatk(
    params: VcfGatkParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Call with GATK; output role ``variants``."""
    from wgsextract_cli.commands.vcf.callers import run_gatk

    return execute(params, run_gatk, progress=progress, cancel=cancel, check=check)


def deepvariant(
    params: VcfDeepvariantParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Call with DeepVariant; output role ``variants``."""
    from wgsextract_cli.commands.vcf.deepvariant import run_deepvariant

    return execute(
        params, run_deepvariant, progress=progress, cancel=cancel, check=check
    )


def clair3(
    params: VcfClair3Params,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Call with Clair3; output role ``variants``."""
    from wgsextract_cli.commands.vcf.clair3 import run_clair3

    return execute(params, run_clair3, progress=progress, cancel=cancel, check=check)


def phase(
    params: VcfPhaseParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Phase a VCF with its reads; output roles ``phased_variants``,
    ``phase_blocks`` and, with ``haplotag``, ``haplotagged_alignments``."""
    from wgsextract_cli.commands.vcf.phasing import run_phase

    return execute(params, run_phase, progress=progress, cancel=cancel, check=check)


def impute(
    params: VcfImputeParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Impute a sample's genotypes; output role ``imputed_variants``."""
    from wgsextract_cli.commands.vcf.imputation import run_impute

    return execute(params, run_impute, progress=progress, cancel=cancel, check=check)
