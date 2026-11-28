from __future__ import annotations

import argparse
from collections.abc import Callable
from functools import wraps

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api._params import Params
from wgsextract_cli.api.ref import (
    RefAadrParams,
    RefAcmgDataParams,
    RefAlphamissenseParams,
    RefAncestryParams,
    RefBootstrapParams,
    RefCaddParams,
    RefClinvarParams,
    RefCountNsParams,
    RefCytobandsParams,
    RefDbsnpParams,
    RefDenseSaParams,
    RefDownloadParams,
    RefGeneMapParams,
    RefGerpParams,
    RefGnomadParams,
    RefGnomadSvParams,
    RefGtexParams,
    RefImputationParams,
    RefIndexParams,
    RefLibraryDeleteParams,
    RefLibraryListParams,
    RefLibraryParams,
    RefMicroarrayTargetsParams,
    RefPharmgkbParams,
    RefPhylopParams,
    RefRecheckParams,
    RefRegulatoryParams,
    RefRevelParams,
    RefSomalierParams,
    RefSpliceaiParams,
    RefStatusParams,
    RefTestGenomeParams,
    RefVerifyParams,
    RefYbrowseParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, experimental, tested, validated
from wgsextract_cli.commands._command_needs import (
    _SAM,
    DOWNLOAD,
    HOURS,
    MINUTES,
    REFERENCE,
    SECONDS,
    _n,
)
from wgsextract_cli.commands._messages.ref import CLI_HELP
from wgsextract_cli.core.utils import UsageError

_REVIEWED = "2026-11-25"
_TESTS = f"{REPO}/blob/main/tests"
CERTIFICATION = {
    "ref aadr": experimental(
        _REVIEWED,
        "Resource acquisition fixtures do not qualify upstream AADR releases.",
        f"{REPO}/blob/main/docs/aadr.md",
    ),
    "ref ybrowse": tested(
        _REVIEWED,
        "test_ybrowse.py",
        "YBrowse resource preparation checked on fixtures.",
    ),
    "ref somalier": tested(
        _REVIEWED,
        "test_somalier_relate.py",
        "Resource install, validation and removal checked with fixture archives.",
    ),
    "ref ancestry": tested(
        _REVIEWED,
        "test_qc_ancestry.py",
        "Reference panel installation and reuse checked on fixtures.",
    ),
    "ref gnomad-sv": tested(
        _REVIEWED,
        "test_sv_frequency.py",
        "Structural-variant resource preparation checked on fixtures.",
    ),
    "ref imputation": tested(
        _REVIEWED,
        "test_imputation.py",
        "Panel installation and reuse checked with fixture GLIMPSE2 resources.",
    ),
    "ref download": tested(
        _REVIEWED,
        "test_download_file_http.py",
        "The shared downloader's checksum verification and local publication are tested against a local HTTP server.",
    ),
    "ref index": validated(
        _REVIEWED,
        "Canonical hs38 BWA index used in the HG002 30× Windows qualification; other indexes were not part of that run.",
        ("Windows",),
        f"{REPO}/issues/1024#issuecomment-7046241211",
        f"{_TESTS}/test_ref_commands.py",
    ),
    "ref dense-sa": tested(
        _REVIEWED,
        "test_dense_suffix_array.py",
        "Dense suffix array artifacts checked on fixture FASTAs.",
    ),
    "ref count-ns": tested(
        _REVIEWED, "test_ref_commands.py", "Known N runs counted in fixture FASTAs."
    ),
    "ref verify": tested(
        _REVIEWED,
        "test_ref_commands.py",
        "Reference checks exercised with real faidx and fixture FASTAs.",
    ),
    "ref library": tested(
        _REVIEWED,
        "test_ref_commands.py",
        "Local reference inventory and resource state checked.",
    ),
    "ref status": tested(
        _REVIEWED,
        "test_ref_status.py",
        "Resource inventory checked in a local fixture library.",
    ),
    "ref recheck": experimental(
        _REVIEWED,
        "Release comparison fixtures do not establish real-data correctness.",
        f"{_TESTS}/test_release_recheck_delta.py",
    ),
    "ref test-genome": tested(
        _REVIEWED,
        "test_ref_test_genome.py",
        "Build identification checked on fixture references and alignments.",
    ),
    "ref needs": tested(
        _REVIEWED,
        "test_ref_needs.py",
        "Offline readiness checked against fixture resource states.",
    ),
    "ref gene-map": tested(
        _REVIEWED,
        "test_gene_map.py",
        "Gene maps and aliases checked on fixture annotations.",
    ),
    "ref cytobands": tested(
        _REVIEWED,
        "test_cytobands.py",
        "Cytoband normalization checked on fixture resources.",
    ),
    "ref regulatory": tested(
        _REVIEWED, "test_regulatory.py", "SCREEN track acquisition checked on fixtures."
    ),
    "ref gtex": tested(
        _REVIEWED,
        "test_gtex.py",
        "Pinned GTEx archive installation checked on fixtures.",
    ),
    "ref clinvar": tested(
        _REVIEWED,
        "test_annotation_downloads.py",
        "ClinVar resource preparation checked on fixtures.",
    ),
    "ref dbsnp": tested(
        _REVIEWED, "test_dbsnp.py", "dbSNP resource checks on fixture variants."
    ),
    "ref revel": tested(
        _REVIEWED,
        "test_annotation_downloads.py",
        "REVEL resource preparation checked on fixtures.",
    ),
    "ref phylop": tested(
        _REVIEWED,
        "test_annotation_downloads.py",
        "phyloP resource preparation checked on fixtures.",
    ),
    "ref gnomad": tested(
        _REVIEWED,
        "test_annotation_downloads.py",
        "gnomAD resource preparation checked on fixtures.",
    ),
    "ref spliceai": tested(
        _REVIEWED,
        "test_annotation_downloads.py",
        "SpliceAI resource preparation checked on fixtures.",
    ),
    "ref alphamissense": tested(
        _REVIEWED,
        "test_annotation_downloads.py",
        "AlphaMissense resource preparation checked on fixtures.",
    ),
    "ref pharmgkb": tested(
        _REVIEWED,
        "test_pharmgkb.py",
        "PharmGKB resource preparation checked on fixtures.",
    ),
    "ref cadd": tested(
        _REVIEWED,
        "test_annotation_downloads.py",
        "CADD resource preparation checked on fixtures.",
    ),
    "ref gerp": tested(
        _REVIEWED,
        "test_annotation_downloads.py",
        "GERP resource preparation checked on fixtures.",
    ),
    "ref bootstrap": tested(
        _REVIEWED,
        "test_ref_bootstrap_config.py",
        "Local resource bundle setup checked on fixtures.",
    ),
    "ref acmg-data": experimental(
        _REVIEWED,
        "Research resource preparation checked on fixtures; not a clinical resource qualification.",
        f"{_TESTS}/test_ref_acmg_data.py",
    ),
    "ref microarray-targets": tested(
        _REVIEWED,
        "test_microarray_targets.py",
        "Microarray target files and validation checked on fixtures.",
    ),
}

NEEDS = {
    "ref download": _n(DOWNLOAD, optional="curl"),
    "ref index": _n(HOURS, *_SAM, optional="bwa bwa-mem2 curl", scale=REFERENCE),
    "ref count-ns": _n(MINUTES, scale=REFERENCE),
    "ref dense-sa": _n(MINUTES, scale=REFERENCE),
    "ref verify": _n(MINUTES, *_SAM, scale=REFERENCE),
    "ref library": _n(
        DOWNLOAD, "bcftools", "bgzip", "curl", "htsfile", "samtools", "tabix"
    ),
    "ref status": _n(SECONDS, scale=""),
    "ref needs": _n(SECONDS, optional="bcftools tabix", scale=""),
    "ref recheck": _n(SECONDS, scale=""),
    "ref test-genome": _n(DOWNLOAD, optional="curl"),
    "ref bootstrap": _n(DOWNLOAD, optional="curl"),
    "ref aadr": _n(DOWNLOAD, optional="curl"),
    "ref acmg-data": _n(DOWNLOAD, "bcftools", "bgzip", "fastvep", "tabix"),
    "ref somalier": _n(DOWNLOAD, optional="curl"),
    "ref ancestry": _n(DOWNLOAD, optional="curl"),
    "ref gnomad-sv": _n(DOWNLOAD, "bcftools", "bgzip", "tabix", optional="curl"),
    "ref imputation": _n(
        DOWNLOAD,
        "bcftools",
        "GLIMPSE2_chunk",
        "GLIMPSE2_phase",
        "GLIMPSE2_split_reference",
        "tabix",
    ),  # fmt: skip
    "ref ybrowse": _n(DOWNLOAD, "bgzip", "tabix", optional="bcftools samtools"),
    "ref microarray-targets": _n(MINUTES),
    "ref gene-map": _n(DOWNLOAD, optional="bgzip curl tabix"),
    "ref cytobands": _n(DOWNLOAD, optional="curl"),
    "ref regulatory": _n(DOWNLOAD, optional="bgzip tabix curl"),
    "ref gtex": _n(DOWNLOAD, optional="curl"),
    "ref clinvar": _n(DOWNLOAD, optional="bgzip curl tabix"),
    "ref dbsnp": _n(DOWNLOAD, optional="bgzip curl tabix"),
    "ref revel": _n(DOWNLOAD, optional="bgzip curl tabix"),
    "ref phylop": _n(DOWNLOAD, optional="curl"),
    "ref gnomad": _n(DOWNLOAD, optional="bcftools bgzip curl tabix"),
    "ref spliceai": _n(DOWNLOAD, optional="bgzip curl tabix"),
    "ref alphamissense": _n(DOWNLOAD, optional="bgzip curl tabix"),
    "ref pharmgkb": _n(DOWNLOAD, optional="bgzip curl tabix"),
    "ref cadd": _n(DOWNLOAD, optional="curl"),
    "ref gerp": _n(DOWNLOAD, optional="curl"),
}


cmd_aadr = command_handler("wgsextract_cli.commands.ref.library_commands", "cmd_aadr")
cmd_acmg_data = command_handler(
    "wgsextract_cli.commands.ref.acmg_data", "cmd_acmg_data"
)
cmd_ancestry_reference = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_ancestry_reference"
)
cmd_bootstrap = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_bootstrap"
)
cmd_count_ns = command_handler(
    "wgsextract_cli.commands.ref.core_commands", "cmd_count_ns"
)
cmd_cytobands = command_handler(
    "wgsextract_cli.commands.ref.cytobands", "cmd_cytobands"
)
cmd_regulatory = command_handler(
    "wgsextract_cli.commands.ref.regulatory", "cmd_regulatory"
)
cmd_gtex = command_handler("wgsextract_cli.commands.ref.gtex", "cmd_gtex")
cmd_dense_sa = command_handler("wgsextract_cli.commands.ref.dense_sa", "cmd_dense_sa")
cmd_download = command_handler(
    "wgsextract_cli.commands.ref.core_commands", "cmd_download"
)
cmd_gene_map = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_gene_map"
)
cmd_gnomad_sv = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_gnomad_sv"
)
cmd_imputation = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_imputation"
)
cmd_index = command_handler("wgsextract_cli.commands.ref.core_commands", "cmd_index")
cmd_library = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_library"
)
cmd_microarray_targets = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_microarray_targets"
)
cmd_ref_needs = command_handler("wgsextract_cli.commands.ref.needs", "cmd_ref_needs")
cmd_ref_status = command_handler("wgsextract_cli.commands.ref.status", "cmd_ref_status")
cmd_recheck = command_handler(
    "wgsextract_cli.commands.ref.recheck", "run_recheck", RefRecheckParams
)
cmd_test_genome = command_handler(
    "wgsextract_cli.commands.ref.mini_genome", "cmd_test_genome"
)
cmd_ref_verify = command_handler(
    "wgsextract_cli.commands.ref.core_commands", "cmd_ref_verify"
)
cmd_somalier = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_somalier"
)
cmd_ybrowse = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_ybrowse"
)
RESOURCE_COMMANDS = (
    (
        RefClinvarParams,
        command_handler(
            "wgsextract_cli.commands.ref.library_commands",
            "run_clinvar",
            RefClinvarParams,
        ),
    ),
    (
        RefDbsnpParams,
        command_handler(
            "wgsextract_cli.commands.ref.library_commands",
            "run_dbsnp",
            RefDbsnpParams,
        ),
    ),
    (
        RefRevelParams,
        command_handler(
            "wgsextract_cli.commands.ref.library_commands", "run_revel", RefRevelParams
        ),
    ),
    (
        RefPhylopParams,
        command_handler(
            "wgsextract_cli.commands.ref.library_commands",
            "run_phylop",
            RefPhylopParams,
        ),
    ),
    (
        RefGnomadParams,
        command_handler(
            "wgsextract_cli.commands.ref.library_commands",
            "run_gnomad",
            RefGnomadParams,
        ),
    ),
    (
        RefSpliceaiParams,
        command_handler(
            "wgsextract_cli.commands.ref.library_commands",
            "run_spliceai",
            RefSpliceaiParams,
        ),
    ),
    (
        RefAlphamissenseParams,
        command_handler(
            "wgsextract_cli.commands.ref.library_commands",
            "run_alphamissense",
            RefAlphamissenseParams,
        ),
    ),
    (
        RefPharmgkbParams,
        command_handler(
            "wgsextract_cli.commands.ref.library_commands",
            "run_pharmgkb",
            RefPharmgkbParams,
        ),
    ),
    (
        RefCaddParams,
        command_handler(
            "wgsextract_cli.commands.ref.library_commands", "run_cadd", RefCaddParams
        ),
    ),
    (
        RefGerpParams,
        command_handler(
            "wgsextract_cli.commands.ref.library_commands", "run_gerp", RefGerpParams
        ),
    ),
)
Handler = Callable[[argparse.Namespace], None]


def _install_or_delete(func: Handler) -> Handler:
    """These downloads are asked for explicitly: --install, or --delete."""

    @wraps(func)
    def handler(args: argparse.Namespace) -> None:
        if args.install == args.delete:
            raise UsageError("Give one of --install or --delete.")
        func(args)

    return handler


_RESOURCE_HELP = {
    "clinvar": "Download official ClinVar VCF for hg19 and hg38.",
    "dbsnp": "Download NCBI dbSNP b151 common human rsIDs (~37M SNPs, VCF + offline rsID search index) for hg38 and hg19, or delete them.",
    "revel": "Download REVEL pathogenicity scores for hg19 and hg38.",
    "phylop": "Download UCSC's PhyloP 100-way conservation scores (~10 GB bigWig).",
    "gnomad": "Download gnomAD population allele frequencies.",
    "spliceai": "Download SpliceAI scores for hg38 (Ensembl, MANE SNVs); index a manual hg19 download.",
    "alphamissense": "Download AlphaMissense scores for hg19 and hg38.",
    "pharmgkb": "Download PharmGKB annotations.",
    "cadd": CLI_HELP["cmd_ref-cadd"],
    "gerp": CLI_HELP["cmd_ref-gerp"],
}


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser("ref", help="Reference Data Management commands.")
    ref_subs = parser.add_subparsers(dest="ref_cmd", required=True)

    def add(
        name: str, cls: type[Params] | None, func: Handler, help: str
    ) -> argparse.ArgumentParser:
        sub = ref_subs.add_parser(name, parents=[base_parser], help=help)
        if name in (
            "aadr",
            "ybrowse",
            "somalier",
            "ancestry",
            "gnomad-sv",
            "imputation",
        ):
            sub.add_argument("--install", action="store_true", help="Install it.")
            func = _install_or_delete(func)
        if cls is not None:
            add_arguments(sub, cls)
        sub.set_defaults(func=func)
        return sub

    add(
        "aadr",
        RefAadrParams,
        cmd_aadr,
        "Explicitly fetch the hash-pinned CC0 AADR v66.p1 2M SNP panel.",
    )
    add(
        "ybrowse",
        RefYbrowseParams,
        cmd_ybrowse,
        "Explicitly install a verified, sites-only GRCh38 yBrowse snapshot.",
    )
    add(
        "somalier",
        RefSomalierParams,
        cmd_somalier,
        "Explicitly fetch somalier's hash-pinned sites and 1000 Genomes sketches for qc relate.",
    )
    add(
        "ancestry",
        RefAncestryParams,
        cmd_ancestry_reference,
        "Fetch the hash-pinned ancestry reference for qc ancestry.",
    )
    add(
        "gnomad-sv",
        RefGnomadSvParams,
        cmd_gnomad_sv,
        "Explicitly fetch hash-pinned gnomAD SV tables for vcf sv-freq.",
    )
    add(
        "imputation",
        RefImputationParams,
        cmd_imputation,
        "Explicitly install the hash-pinned GRCh38 imputation panel, per chromosome.",
    )
    add("download", RefDownloadParams, cmd_download, CLI_HELP["cmd_ref-download"])
    add("index", RefIndexParams, cmd_index, CLI_HELP["cmd_ref-index"])
    add(
        "dense-sa",
        RefDenseSaParams,
        cmd_dense_sa,
        "Build wgse-bwa's optional dense suffix-array file for a FASTA (0.8 GB for hs38; align reads it with --dense-suffix-array), list or delete it.",
    )
    add("count-ns", RefCountNsParams, cmd_count_ns, CLI_HELP["cmd_ref-count-ns"])
    add("verify", RefVerifyParams, cmd_ref_verify, CLI_HELP["cmd_ref-verify"])
    library = add("library", RefLibraryParams, cmd_library, CLI_HELP["cmd_ref-library"])
    library.add_argument(
        "--list",
        action="store_true",
        help="Non-interactively list all available genomes and their status.",
    )
    add_arguments(library, RefLibraryListParams)
    add_arguments(library, RefLibraryDeleteParams)
    status = add(
        "status",
        RefStatusParams,
        cmd_ref_status,
        "Report reference library asset status.",
    )
    status.add_argument(
        "--values",
        action="store_true",
        help="Emit a compact values JSON object for GUI data-source consumers.",
    )
    add(
        "recheck",
        RefRecheckParams,
        cmd_recheck,
        "Compare the resource releases result manifests recorded with the newest: from --answers FILE, or ask NCBI with --online. Downloads and reruns nothing.",
    )
    add(
        "test-genome",
        RefTestGenomeParams,
        cmd_test_genome,
        "Report, install (--install) or delete (--delete) the small hg19 test genome in the dataset library.",
    )
    needs = add(
        "needs",
        None,
        cmd_ref_needs,
        "Report the reference genome, annotation data or optional tools a command line needs that aren't installed, and how to install them.",
    )
    needs.add_argument(
        "--report-json",
        action="store_true",
        help='Print only {"missing": [...]} as JSON, for the desktop app.',
    )
    needs.add_argument(
        "command_line",
        nargs=argparse.REMAINDER,
        metavar="-- COMMAND ...",
        help="The wgsextract command line to check, after --.",
    )
    add("gene-map", RefGeneMapParams, cmd_gene_map, CLI_HELP["cmd_ref-gene-map"])
    add(
        "cytobands",
        RefCytobandsParams,
        cmd_cytobands,
        "Validate installed hg19/hg38 UCSC cytobands; download missing data only with --download.",
    )
    add(
        "regulatory",
        RefRegulatoryParams,
        cmd_regulatory,
        "Check, download or import the GRCh38 SCREEN cCRE track.",
    )
    add("gtex", RefGtexParams, cmd_gtex, "Check or download the GTEx v10 eQTL track.")
    for cls, run in RESOURCE_COMMANDS:
        name = cls.COMMAND.removeprefix("ref ")
        add(name, cls, run, _RESOURCE_HELP[name])
    add(
        "bootstrap",
        RefBootstrapParams,
        cmd_bootstrap,
        "Download and initialize the reference library bootstrap (liftover chains, chip templates, etc.).",
    )
    add(
        "acmg-data",
        RefAcmgDataParams,
        cmd_acmg_data,
        "Prepare fastVEP's ACMG databases, region tracks and Ensembl GFF3; "
        "fetch public sources only with --download.",
    )
    add(
        "microarray-targets",
        RefMicroarrayTargetsParams,
        cmd_microarray_targets,
        "Build hg19 and hg38 microarray targets from the chip templates; "
        "fetch missing liftOver chains only with --download, or delete the targets.",
    )
