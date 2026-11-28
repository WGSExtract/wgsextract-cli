from __future__ import annotations

import argparse
from collections.abc import Callable

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api._params import Params
from wgsextract_cli.api.vcf import (
    VcfAcmgParams,
    VcfAlphamissenseParams,
    VcfAnnotateParams,
    VcfCaddParams,
    VcfCallabilityParams,
    VcfCallParams,
    VcfChainAnnotateParams,
    VcfClair3Params,
    VcfClinvarParams,
    VcfCnvParams,
    VcfCompareParams,
    VcfDeepvariantParams,
    VcfFilterParams,
    VcfFreebayesParams,
    VcfGatkParams,
    VcfGerpParams,
    VcfGnomadParams,
    VcfGraniteQcParams,
    VcfImputeParams,
    VcfIndelParams,
    VcfInfoParams,
    VcfPharmcatParams,
    VcfPharmgkbParams,
    VcfPhaseParams,
    VcfPhylopParams,
    VcfPrepareParams,
    VcfRegulatoryParams,
    VcfRevelParams,
    VcfSnpParams,
    VcfSpliceaiParams,
    VcfSvFreqParams,
    VcfSvParams,
    VcfTableParams,
    VcfToFastaParams,
    VcfTrioParams,
    VcfVariantQcParams,
    _VcfFileParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, experimental, tested, validated
from wgsextract_cli.commands._command_needs import (
    _CALL,
    _VCF,
    HOURS,
    MINUTES,
    SECONDS,
    _n,
)
from wgsextract_cli.commands._messages.vcf import CLI_HELP
from wgsextract_cli.core.config import settings

_REVIEWED = "2026-11-25"
_TESTS = f"{REPO}/blob/main/tests"
_WINDOWS_30X = f"{REPO}/issues/1024#issuecomment-7046241211"
_ONT = f"{REPO}/blob/main/docs/ont.md"
CERTIFICATION = {
    "vcf snp": validated(
        _REVIEWED,
        "HG002 GRCh38 30× Windows qualification matched published counts; not per-genotype parity or all presets.",
        ("Windows",),
        _WINDOWS_30X,
        f"{_TESTS}/test_vcf_basic.py",
    ),
    "vcf indel": validated(
        _REVIEWED,
        "HG002 GRCh38 30× Windows qualification matched published counts; not per-genotype parity or all presets.",
        ("Windows",),
        _WINDOWS_30X,
        f"{_TESTS}/test_vcf_basic.py",
    ),
    "vcf call": tested(
        _REVIEWED,
        "test_vcf_call.py",
        "SNP/indel and gVCF orchestration checked on fixture alignments.",
    ),
    "vcf annotate": tested(
        _REVIEWED,
        "test_vcf_basic.py",
        "Annotation field copying checked with real bcftools.",
    ),
    "vcf filter": tested(
        _REVIEWED,
        "test_vcf_commands.py",
        "Filtering of fixture variants checked with bcftools.",
    ),
    "vcf table": tested(
        _REVIEWED,
        "test_vcf_table.py",
        "Tabular exports checked against fixture variant calls.",
    ),
    "vcf prepare": tested(
        _REVIEWED,
        "test_vcf_prepare.py",
        "Normalization and preparation checked on fixture VCFs.",
    ),
    "vcf compare": validated(
        _REVIEWED,
        "Normalized allele and gVCF comparison checked on a 12 kb HG002 slice; no whole-genome or inheritance claim.",
        ("Linux",),
        f"{REPO}/issues/1295#issuecomment-7160979567",
        f"{_TESTS}/test_vcf_compare.py",
    ),
    "vcf clinvar": tested(
        _REVIEWED,
        "test_clinvar_matching.py",
        "Allele matching checked against local ClinVar fixtures.",
    ),
    "vcf revel": tested(
        _REVIEWED,
        "test_vcf_annotations.py",
        "REVEL score annotation checked on fixture alleles.",
    ),
    "vcf gnomad": tested(
        _REVIEWED,
        "test_gnomad_af.py",
        "Frequency annotation checked on fixture alleles.",
    ),
    "vcf sv-freq": tested(
        _REVIEWED,
        "test_sv_frequency.py",
        "Structural-variant frequency matching checked on fixtures.",
    ),
    "vcf pharmgkb": tested(
        _REVIEWED,
        "test_pharmgkb.py",
        "PharmGKB annotations checked on fixture variants.",
    ),
    "vcf regulatory": tested(
        _REVIEWED, "test_regulatory.py", "Per-ALT SCREEN overlap checked on fixtures."
    ),
    "vcf info": tested(
        _REVIEWED, "test_vcf_info.py", "VCF statistics and build checks on fixtures."
    ),
    "vcf variantqc": validated(
        "2026-11-26",
        "Real DISCVRSeq 1.3.86 on Java 17 matched bcftools counts for HG002 GRCh38 "
        "calls on Windows; the same tool WGS Extract v5 used. Report content "
        "beyond variant counts is DISCVRSeq's own.",
        ("Windows",),
        "https://github.com/BimberLab/DISCVRSeq",
        f"{_TESTS}/test_vcf_info.py",
    ),
    "vcf granite-qc": experimental(
        _REVIEWED,
        "External GRANITE integration does not establish output correctness.",
        f"{_TESTS}/test_genotype_qc.py",
    ),
    "vcf callability": tested(
        _REVIEWED,
        "test_reference_evidence.py",
        "Reference and no-call evidence checked on fixture gVCFs.",
    ),
    "vcf to-fasta": tested(
        _REVIEWED,
        "test_vcf_consensus.py",
        "Consensus sequence checked on fixture variants and references.",
    ),
    "vcf trio": tested(
        _REVIEWED,
        "test_trio_vcfs.py",
        "Trio sample and inheritance states checked on fixture variants.",
    ),
    "vcf cnv": experimental(
        _REVIEWED,
        "Integration fixtures do not qualify CNV accuracy.",
        f"{_TESTS}/test_structural.py",
    ),
    "vcf sv": validated(
        _REVIEWED,
        "HG002 ONT Sniffles calls scored against GIAB SV truth; does not qualify Delly or other read types.",
        ("Linux",),
        f"{_ONT}#validation-on-giab-hg002",
        f"{_TESTS}/test_ont_hg002.py",
    ),
    "vcf freebayes": tested(
        _REVIEWED,
        "test_vcf_callers.py",
        "Real FreeBayes calls checked against a planted SNP.",
    ),
    "vcf gatk": tested(
        _REVIEWED,
        "test_vcf_callers.py",
        "Fixture GATK calls and CRAM/BAM handling checked.",
    ),
    "vcf deepvariant": validated(
        _REVIEWED,
        "GIAB-scored ONT chr20 and same-input Linux WGS ARM/x64 parity; not all DeepVariant modes.",
        ("Linux",),
        f"{_ONT}#validation-on-giab-hg002",
        f"{REPO}/blob/main/docs/deepvariant-arm64.md#completed-public-whole-genome-qualification",
        f"{_TESTS}/test_deepvariant_native.py",
    ),
    "vcf clair3": validated(
        _REVIEWED,
        "HG002 chr20–22 CPU/MPS checks against GIAB truth; no whole-genome or all-model guarantee.",
        ("Linux", "macOS"),
        f"{_ONT}#validation-on-giab-hg002",
        f"{REPO}/issues/1101#issuecomment-7175184245",
        f"{REPO}/issues/1124#issuecomment-7175184658",
        f"{_TESTS}/test_ont_hg002.py",
    ),
    "vcf phase": validated(
        _REVIEWED,
        "LongPhase and WhatsHap scored against GIAB phasing on HG002 chr20 ONT and a HiFi slice; no whole-genome claim.",
        ("Linux",),
        f"{_ONT}#phasing",
        f"{_TESTS}/test_phasing.py",
    ),
    "vcf impute": validated(
        _REVIEWED,
        "HG002 ~1× GLIMPSE2 chr21/22 pilot cross-checked against GIAB; not all depths, ancestries or imputation engines.",
        ("macOS",),
        f"{REPO}/issues/1118#issuecomment-7172655613",
        f"{_TESTS}/test_imputation.py",
    ),
    "vcf phylop": tested(
        _REVIEWED,
        "test_vcf_bigwig_scores.py",
        "phyloP annotation checked on fixture score tracks.",
    ),
    "vcf cadd": tested(
        _REVIEWED,
        "test_vcf_annotations.py",
        "CADD score annotation checked on fixture alleles.",
    ),
    "vcf gerp": tested(
        _REVIEWED,
        "test_vcf_bigwig_scores.py",
        "GERP annotation checked on fixture score tracks.",
    ),
    "vcf spliceai": tested(
        _REVIEWED,
        "test_vcf_annotations.py",
        "SpliceAI scores checked on fixture variants.",
    ),
    "vcf alphamissense": tested(
        _REVIEWED,
        "test_vcf_annotations.py",
        "AlphaMissense scores checked on fixture variants.",
    ),
    "vcf acmg": experimental(
        _REVIEWED,
        "Research-only interpretation; no clinical classification is certified.",
        f"{REPO}/blob/main/docs/acmg.md",
    ),
    "vcf pharmcat": experimental(
        _REVIEWED,
        "Research pharmacogenomics; fixture workflow tests do not qualify medical recommendations.",
        f"{_TESTS}/test_pharmcat.py",
    ),
    "vcf chain-annotate": tested(
        _REVIEWED,
        "test_vcf_chain.py",
        "Local annotation chains checked against fixture variants and artifacts.",
    ),
}

NEEDS = {
    "vcf snp": _n(HOURS, *_CALL),
    "vcf indel": _n(HOURS, *_CALL),
    "vcf call": _n(HOURS, *_CALL, optional="bgzip delly pbsv sambamba sniffles"),
    "vcf info": _n(SECONDS, "bcftools", optional="htsfile samtools tabix"),
    "vcf variantqc": _n(
        MINUTES, optional="bcftools bgzip discvrseq java17 samtools tabix"
    ),
    "vcf granite-qc": _n(MINUTES, "bcftools", optional="granite samtools"),
    "vcf annotate": _n(MINUTES, *_VCF, optional="bgzip samtools"),
    "vcf filter": _n(MINUTES, *_VCF, optional="bgzip samtools"),
    "vcf table": _n(MINUTES, "bcftools", optional="bgzip tabix"),
    "vcf compare": _n(MINUTES, "bcftools", optional="samtools tabix"),
    "vcf callability": _n(SECONDS, "bcftools", optional="curl"),
    "vcf clinvar": _n(MINUTES, *_VCF, optional="bgzip samtools"),
    "vcf revel": _n(MINUTES, *_VCF, optional="bgzip samtools"),
    "vcf gnomad": _n(MINUTES, *_VCF, optional="bgzip samtools"),
    "vcf sv-freq": _n(MINUTES, "bcftools", "bgzip", "tabix", optional="samtools"),
    "vcf pharmgkb": _n(MINUTES, *_VCF, optional="bgzip samtools"),
    "vcf regulatory": _n(MINUTES, *_VCF, "bgzip", optional="samtools"),
    "vcf prepare": _n(MINUTES, *_VCF),
    "vcf to-fasta": _n(MINUTES, "bcftools", "bgzip", "samtools", "tabix"),
    "vcf trio": _n(MINUTES, *_CALL, optional="bgzip"),
    "vcf cnv": _n(HOURS, "bcftools", "bgzip", "delly", "samtools", "tabix"),
    "vcf sv": _n(HOURS, *_CALL, "bgzip", optional="delly pbsv sambamba sniffles"),
    "vcf freebayes": _n(HOURS, "bcftools", "bgzip", "freebayes", "samtools", "tabix"),
    "vcf gatk": _n(HOURS, "bgzip", "gatk", "samtools", "tabix", optional="bcftools"),
    "vcf deepvariant": _n(
        HOURS,
        "bgzip",
        "samtools",
        "tabix",
        optional="bcftools docker run_deepvariant wgse-deepvariant sambamba",
    ),
    "vcf clair3": _n(
        HOURS,
        "bcftools",
        "bgzip",
        "samtools",
        "tabix",
        optional="docker run_clair3.sh sambamba wgse-clair3",
    ),
    "vcf impute": _n(
        HOURS,
        "bcftools",
        "bgzip",
        "samtools",
        "tabix",
        optional="GLIMPSE2_ligate GLIMPSE2_phase",
    ),  # fmt: skip
    "vcf phase": _n(
        HOURS, "bcftools", "samtools", "tabix", optional="longphase whatshap"
    ),
    "vcf phylop": _n(MINUTES, "bcftools", "bgzip", "tabix", optional="samtools"),
    "vcf cadd": _n(MINUTES, *_VCF, optional="bgzip samtools"),
    "vcf acmg": _n(
        MINUTES, "bcftools", "bgzip", "tabix", "fastvep", optional="samtools"
    ),
    "vcf gerp": _n(MINUTES, "bcftools", "bgzip", "tabix", optional="samtools"),
    "vcf spliceai": _n(MINUTES, *_VCF, optional="bgzip samtools"),
    "vcf alphamissense": _n(MINUTES, *_VCF, optional="bgzip samtools"),
    "vcf pharmcat": _n(
        MINUTES, "bcftools", "pharmcat", "samtools", "tabix", optional="bgzip curl"
    ),
    "vcf chain-annotate": _n(MINUTES, *_VCF, optional="bgzip samtools vep vep-rs"),
}


cmd_alphamissense = command_handler(
    "wgsextract_cli.commands.vcf.splicing", "cmd_alphamissense"
)
cmd_acmg = command_handler("wgsextract_cli.commands.vcf.acmg", "cmd_acmg")
cmd_annotate = command_handler("wgsextract_cli.commands.vcf.basic", "cmd_annotate")
cmd_cadd = command_handler("wgsextract_cli.commands.vcf.deleteriousness", "cmd_cadd")
cmd_chain_annotate = command_handler(
    "wgsextract_cli.commands.vcf.chain", "cmd_chain_annotate"
)
cmd_clair3 = command_handler("wgsextract_cli.commands.vcf.clair3", "cmd_clair3")
cmd_clinvar = command_handler(
    "wgsextract_cli.commands.vcf.clinvar_revel", "cmd_clinvar"
)
cmd_call = command_handler("wgsextract_cli.commands.vcf.call", "cmd_call")
cmd_callability = command_handler(
    "wgsextract_cli.commands.vcf.callability", "cmd_callability"
)
cmd_cnv = command_handler("wgsextract_cli.commands.vcf.structural", "cmd_cnv")
cmd_deepvariant = command_handler(
    "wgsextract_cli.commands.vcf.deepvariant", "cmd_deepvariant"
)
cmd_filter = command_handler("wgsextract_cli.commands.vcf.filter_trio", "cmd_filter")
cmd_compare = command_handler("wgsextract_cli.commands.vcf.compare", "cmd_compare")
cmd_freebayes = command_handler("wgsextract_cli.commands.vcf.callers", "cmd_freebayes")
cmd_gatk = command_handler("wgsextract_cli.commands.vcf.callers", "cmd_gatk")
cmd_gerp = command_handler("wgsextract_cli.commands.vcf.deleteriousness", "cmd_gerp")
cmd_gnomad = command_handler("wgsextract_cli.commands.vcf.population", "cmd_gnomad")
cmd_granite_qc = command_handler(
    "wgsextract_cli.commands.vcf.qc_tools", "cmd_granite_qc"
)
cmd_impute = command_handler("wgsextract_cli.commands.vcf.imputation", "cmd_impute")
cmd_indel = command_handler("wgsextract_cli.commands.vcf.basic", "cmd_indel")
cmd_info = command_handler("wgsextract_cli.commands.vcf.info", "cmd_vcf_info")
cmd_pharmcat = command_handler("wgsextract_cli.commands.vcf.pharmcat", "cmd_pharmcat")
cmd_prepare = command_handler("wgsextract_cli.commands.vcf.prepare", "cmd_prepare")
cmd_pharmgkb = command_handler("wgsextract_cli.commands.vcf.splicing", "cmd_pharmgkb")
cmd_regulatory = command_handler(
    "wgsextract_cli.commands.vcf.regulatory", "cmd_regulatory"
)
cmd_phase = command_handler("wgsextract_cli.commands.vcf.phasing", "cmd_phase")
cmd_phylop = command_handler("wgsextract_cli.commands.vcf.population", "cmd_phylop")
cmd_revel = command_handler("wgsextract_cli.commands.vcf.clinvar_revel", "cmd_revel")
cmd_snp = command_handler("wgsextract_cli.commands.vcf.basic", "cmd_snp")
cmd_spliceai = command_handler("wgsextract_cli.commands.vcf.splicing", "cmd_spliceai")
cmd_sv = command_handler("wgsextract_cli.commands.vcf.structural", "cmd_sv")
cmd_sv_freq = command_handler("wgsextract_cli.commands.vcf.sv_frequency", "cmd_sv_freq")
cmd_table = command_handler("wgsextract_cli.commands.vcf.table", "cmd_table")
cmd_to_fasta = command_handler("wgsextract_cli.commands.vcf.consensus", "cmd_to_fasta")
cmd_trio = command_handler("wgsextract_cli.commands.vcf.filter_trio", "cmd_trio")
cmd_variantqc = command_handler("wgsextract_cli.commands.vcf.qc_tools", "cmd_variantqc")


def _add_vcf_command(
    vcf_subs: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
    name: str,
    help_text: str,
    cls: type[Params],
    func: Callable[[argparse.Namespace], None],
) -> None:
    """A command on a VCF; --vcf-input defaults to config default_input_vcf.
    Those that read one VCF, --vcf-input or --input, are marked for dispatch
    (``_one_vcf_input``) to record only that one."""
    parser = vcf_subs.add_parser(name, parents=[base_parser], help=help_text)
    add_arguments(parser, cls, config_defaults=True)
    parser.set_defaults(func=func)
    if issubclass(cls, _VcfFileParams):
        parser.set_defaults(_one_vcf_input=True)
    for action in parser._actions:
        if action.dest == "vcf_input":
            action.default = settings.get("default_input_vcf")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser("vcf", help=CLI_HELP["cmd_vcf"])
    vcf_subs = parser.add_subparsers(dest="vcf_cmd", required=True)
    snp_parser = vcf_subs.add_parser(
        "snp", parents=[base_parser], help=CLI_HELP["cmd_snp"]
    )
    add_arguments(snp_parser, VcfSnpParams)
    snp_parser.set_defaults(func=cmd_snp)
    indel_parser = vcf_subs.add_parser(
        "indel", parents=[base_parser], help=CLI_HELP["cmd_indel"]
    )
    add_arguments(indel_parser, VcfIndelParams)
    indel_parser.set_defaults(func=cmd_indel)
    call_parser = vcf_subs.add_parser(
        "call", parents=[base_parser], help=CLI_HELP["cmd_call"]
    )
    add_arguments(call_parser, VcfCallParams)
    call_parser.set_defaults(func=cmd_call)
    _add_vcf_command(
        vcf_subs,
        base_parser,
        "annotate",
        CLI_HELP["cmd_annotate"],
        VcfAnnotateParams,
        cmd_annotate,
    )
    _add_vcf_command(
        vcf_subs,
        base_parser,
        "filter",
        CLI_HELP["cmd_filter"],
        VcfFilterParams,
        cmd_filter,
    )
    _add_vcf_command(
        vcf_subs,
        base_parser,
        "table",
        "Export one row per ALT to TSV, Parquet or SQLite, including reported protein positions, domains and gnomAD FAF95.",
        VcfTableParams,
        cmd_table,
    )
    _add_vcf_command(
        vcf_subs,
        base_parser,
        "callability",
        "Which coding exons of a gene list (ACMG SF by default) the sample's gVCF "
        "could read: callable, partial, not readable, specialist or not run.",
        VcfCallabilityParams,
        cmd_callability,
    )
    _add_vcf_command(
        vcf_subs,
        base_parser,
        "prepare",
        CLI_HELP["cmd_vcf-prepare"],
        VcfPrepareParams,
        cmd_prepare,
    )
    _add_vcf_command(
        vcf_subs,
        base_parser,
        "to-fasta",
        CLI_HELP["cmd_vcf-to-fasta"],
        VcfToFastaParams,
        cmd_to_fasta,
    )
    _add_vcf_command(
        vcf_subs, base_parser, "trio", CLI_HELP["cmd_trio"], VcfTrioParams, cmd_trio
    )
    _add_vcf_command(
        vcf_subs,
        base_parser,
        "compare",
        "Compare normalized small alleles across local callsets.",
        VcfCompareParams,
        cmd_compare,
    )
    caller_commands: list[
        tuple[str, type[Params], Callable[[argparse.Namespace], None]]
    ] = [
        ("cnv", VcfCnvParams, cmd_cnv),
        ("sv", VcfSvParams, cmd_sv),
        ("freebayes", VcfFreebayesParams, cmd_freebayes),
        ("gatk", VcfGatkParams, cmd_gatk),
        ("deepvariant", VcfDeepvariantParams, cmd_deepvariant),
        ("clair3", VcfClair3Params, cmd_clair3),
    ]
    for name, caller_cls, caller_func in caller_commands:
        caller_parser = vcf_subs.add_parser(
            name, parents=[base_parser], help=CLI_HELP[f"cmd_{name}"]
        )
        add_arguments(caller_parser, caller_cls)
        caller_parser.set_defaults(func=caller_func)
    _add_vcf_command(
        vcf_subs, base_parser, "phase", CLI_HELP["cmd_phase"], VcfPhaseParams, cmd_phase
    )
    _add_vcf_command(
        vcf_subs,
        base_parser,
        "impute",
        CLI_HELP["cmd_impute"],
        VcfImputeParams,
        cmd_impute,
    )
    for name, help_text, cls, func in (
        (
            "clinvar",
            "Annotate VCF with ClinVar pathogenicity data.",
            VcfClinvarParams,
            cmd_clinvar,
        ),
        (
            "revel",
            "Annotate VCF with REVEL pathogenicity scores.",
            VcfRevelParams,
            cmd_revel,
        ),
        (
            "phylop",
            "Annotate VCF with PhyloP conservation scores.",
            VcfPhylopParams,
            cmd_phylop,
        ),
        (
            "cadd",
            "Annotate VCF with CADD deleteriousness scores (CADD_RAW, CADD_PHRED).",
            VcfCaddParams,
            cmd_cadd,
        ),
        (
            "gerp",
            "Annotate VCF with GERP++ conservation scores (GERP).",
            VcfGerpParams,
            cmd_gerp,
        ),
        (
            "gnomad",
            "Annotate VCF with gnomAD population frequencies.",
            VcfGnomadParams,
            cmd_gnomad,
        ),
        (
            "sv-freq",
            "Annotate SVs with gnomAD-SV/gnomAD-LR frequencies.",
            VcfSvFreqParams,
            cmd_sv_freq,
        ),
        (
            "spliceai",
            "Annotate VCF with SpliceAI splicing scores.",
            VcfSpliceaiParams,
            cmd_spliceai,
        ),
        (
            "alphamissense",
            "Annotate VCF with AlphaMissense pathogenicity scores.",
            VcfAlphamissenseParams,
            cmd_alphamissense,
        ),
        (
            "acmg",
            "Research-only ACMG/AMP classes with fastVEP (opt-in).",
            VcfAcmgParams,
            cmd_acmg,
        ),
        (
            "pharmgkb",
            "Annotate VCF with PharmGKB drug metabolism data.",
            VcfPharmgkbParams,
            cmd_pharmgkb,
        ),
        (
            "regulatory",
            "Annotate GRCh38 variants with offline SCREEN cCRE overlap (research only).",
            VcfRegulatoryParams,
            cmd_regulatory,
        ),
        (
            "pharmcat",
            "Call star-allele diplotypes and drug-response phenotypes (CYP2C19, CYP2C9, DPYD, ...) with PharmCAT, and write its prescribing report.",
            VcfPharmcatParams,
            cmd_pharmcat,
        ),
        (
            "info",
            "Quick summary of a VCF, gVCF or BCF: reference build/model, samples, format, and record counts.",
            VcfInfoParams,
            cmd_info,
        ),
        (
            "variantqc",
            "Run DISCVRSeq VariantQC to generate an interactive HTML VCF quality report.",
            VcfVariantQcParams,
            cmd_variantqc,
        ),
        (
            "granite-qc",
            "Run granite qcVCF (dbmi-bgm/granite) for single-sample or family VCF quality metrics.",
            VcfGraniteQcParams,
            cmd_granite_qc,
        ),
        (
            "chain-annotate",
            "Sequentially apply multiple annotations to a single VCF.",
            VcfChainAnnotateParams,
            cmd_chain_annotate,
        ),
    ):
        _add_vcf_command(vcf_subs, base_parser, name, help_text, cls, func)
