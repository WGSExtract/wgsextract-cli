from __future__ import annotations

import argparse
from collections.abc import Callable
from functools import wraps

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api._params import Params
from wgsextract_cli.api.ref import (
    RefAadrParams,
    RefAlphamissenseParams,
    RefAncestryParams,
    RefBootstrapParams,
    RefCaddParams,
    RefClinvarParams,
    RefCountNsParams,
    RefDenseSaParams,
    RefDownloadParams,
    RefGeneMapParams,
    RefGerpParams,
    RefGnomadParams,
    RefGnomadSvParams,
    RefImputationParams,
    RefIndexParams,
    RefLibraryParams,
    RefMicroarrayTargetsParams,
    RefPharmgkbParams,
    RefPhylopParams,
    RefRevelParams,
    RefSomalierParams,
    RefSpliceaiParams,
    RefStatusParams,
    RefVerifyParams,
    RefYbrowseParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP
from wgsextract_cli.core.utils import UsageError

cmd_aadr = command_handler("wgsextract_cli.commands.ref.library_commands", "cmd_aadr")
cmd_ancestry_reference = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_ancestry_reference"
)
cmd_bootstrap = command_handler(
    "wgsextract_cli.commands.ref.library_commands", "cmd_bootstrap"
)
cmd_count_ns = command_handler(
    "wgsextract_cli.commands.ref.core_commands", "cmd_count_ns"
)
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
        "Explicitly install a hash-pinned, sites-only GRCh38 yBrowse snapshot.",
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
    needs = add(
        "needs",
        None,
        cmd_ref_needs,
        "Report the reference genome or annotation data a command line needs that isn't installed, and how to install it.",
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
        "microarray-targets",
        RefMicroarrayTargetsParams,
        cmd_microarray_targets,
        "Build the genome-wide hg19 and hg38 microarray targets from the chip templates, or delete them.",
    )
