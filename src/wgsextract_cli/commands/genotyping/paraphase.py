"""Run Paraphase on long-read whole-genome alignments."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.genotyping import TypingParaphaseParams
from wgsextract_cli.core.alignment.samtools_commands import alignment_index
from wgsextract_cli.core.alignment.sequencing import read_group_platforms
from wgsextract_cli.core.atomic_output import publication_lock, publish_output_set
from wgsextract_cli.core.calling_reference import validate_calling_reference
from wgsextract_cli.core.download_progress import redact_urls
from wgsextract_cli.core.paraphase_document import (
    SCHEMA_ID,
    LocusCall,
    ParaphaseDocument,
)
from wgsextract_cli.core.pixi_tools import pixi_env_prefix
from wgsextract_cli.core.ref_library.assets import read_fai_lengths
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.table_writer import Column, write_table
from wgsextract_cli.core.temp_directory import move_file, scratch_directory
from wgsextract_cli.core.ultima import read_groups
from wgsextract_cli.core.utils import (
    MissingDependencyError,
    UsageError,
    WGSExtractError,
    run_command,
)
from wgsextract_cli.core.variant_files import get_bam_header

from .common import Alignment, open_alignment

Genome = Literal["19", "37", "38", "chm13"]
Platform = Literal["pacbio", "ont"]
EngineLicense = Literal["BSD-3-Clause-Clear", "PacBio Software License Agreement"]
BSD_LICENSE: EngineLicense = "BSD-3-Clause-Clear"
PACBIO_LICENSE: EngineLicense = "PacBio Software License Agreement"
PINNED_VERSION = "3.3.1"
INSTALL = "pixi install --locked -e paraphase"
BUILD_37_LOCI = "smn1 pms2 rccx strc cfc1 ikbkg ncf1 neb f8 opn1lw hba".split()
# Region identifiers from Paraphase v3.3.1's data/38/config.yaml.
BUILD_38_LOCI = (
    BUILD_37_LOCI
    + """
TNXB SSX2 SSX4 CENPVL2 DMRTC1 XAGE1A TRIM49D1 CXorf51A DDT H3C14 MBD3L2 DEFA1 CCZ1
CSAG2 OR4M2 RIMBP3B AMY2A AGAP9 ANXA8 C21orf140 CDY1 CDY2A HSFY1 PRY BPY2 CHRNA7
CNTNAP3 CTAGE6 CTAGE8 CXorf49 DEFB109B PRR23D1 DEFB107A DEFB105A DEFB106A DEFB104A
SPAG11A DEFB103A DEFB4A DEFB130A ZNF705D FAM86B1 DHX40 EIF3C ETDA FAM156A FAM246A
FCGR2C FRG2 FRMPD2 TRAPPC10 PWP2 GATD3 GOLGA6L24 GOLGA8A GOLGA8F GOLGA8K GPAT2
HSF2BP HSFX1 IQCK LGALS9B LIMS3 MAGEA9 MAGEH1 MALL MRPL23 NOTCH2 NPIPA2 NPY4R NUDT4B
NXF2 OCLN OR1D5 OR2A1 OR2T5 OTOA PABPC1L2A PDPK1 POTEE POTEI PPIAL4C PPIP5K1
PRAMEF10 PRAMEF7 PRAMEF25 PRAMEF13 PRAMEF18 POTED PRODH DGCR6 PTPN20 PWWP4 RBPMS RGPD1
PLGLB1 RGPD3 RHOXF2 RMND5A RSPH10B SERF1A SIK1 SMIM11 SMIM34 SYT15 TCAF1 TCP11X1
THOC3 TMEM191B TMLHE TRIM49 TRIM64 TRIM73 U2AF1 UPK3BL1 POLR2J2 ZNF595 FOXD4L4 FOXD4L3
GTF2IRD2B NOTCH2NLA ICOSLG MAGED4 CBS CRYAA KCNE1 ARHGEF5 CASTOR2 CLEC18C GTF2H2 GTF2I
HIC2 NBPF4 PPIAL4D RGPD5 FAM72A GOLGA8N POTEB NUTM2A ANKRD20A1 GRAPL ARL17A NSF
AMY1A CTAG1A BOLA2 CYP2D6 GBA CYP11B1 CFH CFHR3
""".split()
)
GENE_FIELDS = (
    "gene_cn", "smn1_cn", "smn2_cn", "smn_del78_cn", "genotype", "final_haplotypes",
    "alleles_final", "annotated_alleles", "fusions_called", "deletion_haplotypes",
    "deletion_hap", "phasing_success", "sample_sex", "sv_called", "opn1lw_cn", "opn1mw_cn",
)  # fmt: skip
PRIMARY_CONTIG = re.compile(r"(?:chr)?(?:[1-9]|1[0-9]|2[0-2]|X|Y|M|MT)")
TABLE = (
    Column("gene", "TEXT"),
    Column("total_cn", "INTEGER", "Paralog-family copy number; blank if not reported."),
    Column("call_status", "TEXT"),
    Column("experimental", "TEXT", "true for experimental, unvalidated ONT calls."),
    Column("gene_fields", "TEXT", "JSON object of the locus-specific call fields."),
)


def _platform(header: str, licensed: bool) -> Platform:
    groups = read_groups(header)
    platforms = read_group_platforms(header)
    if licensed and (
        not groups
        or platforms != ["pacbio"]
        or any(
            not group.get("ID") or group.get("PL", "").upper() != "PACBIO"
            for group in groups
        )
    ):
        raise UsageError(
            f"The {PACBIO_LICENSE} requires PL:PACBIO on every input read group."
        )
    if len({group["SM"] for group in groups if group.get("SM")}) > 1:
        raise UsageError("Paraphase needs an alignment for a single sample.")
    if not groups or any(not group.get("PL") for group in groups):
        raise UsageError("Paraphase needs @RG platform (PL) tags on all read groups.")
    if platforms == ["pacbio"]:
        return "pacbio"
    if platforms == ["ont"]:
        return "ont"
    raise UsageError(
        "Paraphase needs unmixed PacBio HiFi or ONT reads; "
        f"found PL values {', '.join(platforms)}."
    )


def _require_pacbio_reads(aln: Alignment, header: str, work: Path) -> None:
    # Header declarations alone don't identify records with missing or unknown RG.
    groups = work / "pacbio-read-groups.txt"
    groups.write_text(
        "".join(f"{rg['ID']}\n" for rg in read_groups(header)), encoding="utf-8"
    )
    counts = work / "read-group-counts.json"
    run_command(
        [
            "samtools", "view", "-@", aln.threads, "-c", "-D", f"RG:{groups}",
            "--save-counts", counts, *aln.ref_opt, aln.path,
        ],
        capture_output=True,
    )  # fmt: skip
    unassigned = json.loads(counts.read_text(encoding="utf-8"))[
        "records_filter_rejected"
    ]
    if unassigned:
        raise UsageError(
            f"The {PACBIO_LICENSE} requires PacBio reads: {unassigned} records lack "
            "a declared PL:PACBIO read group."
        )


def _genome(aln: Alignment) -> Genome:
    if any(name.lower().endswith("_alt") for name in aln.lengths):
        raise UsageError(
            "Paraphase needs reads aligned to a reference without ALT contigs."
        )
    if aln.build == "hg19":
        return "19" if aln.chr_prefix else "37"
    if aln.chr_prefix and "1" not in aln.lengths:
        if aln.build == "hg38":
            return "38"
        if aln.build == "t2t":
            return "chm13"
    raise UsageError(
        "Paraphase supports hg19/GRCh37, and GRCh38 or T2T-CHM13 with chr-prefixed contigs; "
        f"detected {aln.build or 'unknown'}."
    )


def _select_loci(
    genes: str | None, genome: Genome, version: tuple[int, int, int]
) -> list[str] | None:
    if genome == "chm13":
        available = ["smn1", "pms2"] if version >= (4, 0, 0) else ["smn1"]
    else:
        available = BUILD_38_LOCI if genome == "38" else BUILD_37_LOCI
    if genes is None:
        return None
    names = {name.casefold(): name for name in available}
    chosen = [gene.strip() for gene in genes.split(",")]
    invalid = [gene or "(empty)" for gene in chosen if gene.casefold() not in names]
    if invalid:
        raise UsageError(
            f"Unsupported Paraphase loci for --genome {genome}: {', '.join(invalid)}. "
            f"Choose from: {', '.join(available)}."
        )
    return list(dict.fromkeys(names[gene.casefold()] for gene in chosen))


def _engine(
    executable: Path | None,
) -> tuple[Path, Path, str, tuple[int, int, int], EngineLicense]:
    if executable is None:
        prefix = pixi_env_prefix("paraphase")
        if prefix is None or not (prefix / "conda-meta/pixi").is_file():
            raise MissingDependencyError(
                f"Paraphase {PINNED_VERSION} needs its optional Pixi environment "
                f"(Linux and macOS): {INSTALL}"
            )
        bindir = prefix / "bin"
        executable = bindir / "paraphase"
        license_name = BSD_LICENSE
    else:
        executable = executable.absolute()
        bindir = executable.parent
        license_name = PACBIO_LICENSE
    if not executable.is_file() or not os.access(executable, os.X_OK):
        hint = (
            f"; repair the environment with {INSTALL}"
            if license_name == BSD_LICENSE
            else ""
        )
        raise MissingDependencyError(
            f"Paraphase executable is missing or not executable: {executable}{hint}"
        )

    probe = run_command([executable, "--version"], capture_output=True, check=False)
    version_text = (probe.stdout or probe.stderr or "").strip()
    match = re.fullmatch(r"(?:paraphase\s+)?((\d+)\.(\d+)\.(\d+))", version_text, re.I)
    if probe.returncode or match is None:
        diagnostic = (probe.stderr or probe.stdout or "").strip()
        excerpt = " ".join(redact_urls(diagnostic).split())[:240]
        raise WGSExtractError(
            f"Could not verify the Paraphase version at {executable} "
            f"(--version exit {probe.returncode}): {excerpt or 'no output'}."
        )
    version = match[1]
    numbers = (int(match[2]), int(match[3]), int(match[4]))
    if license_name == BSD_LICENSE and version != PINNED_VERSION:
        raise MissingDependencyError(
            f"Expected Paraphase {PINNED_VERSION} ({BSD_LICENSE}), found {version}; {INSTALL}."
        )
    if license_name == PACBIO_LICENSE and not (3, 3, 2) <= numbers < (5, 0, 0):
        raise UsageError(
            f"The {PACBIO_LICENSE} opt-in supports Paraphase 3.3.2 through 4.x."
        )
    return executable, bindir, version, numbers, license_name


def _stage_reference(aln: Alignment, work: Path) -> str:
    reference = Path(aln.require_reference()).absolute()
    lengths = read_fai_lengths(reference)
    if not lengths:
        raise UsageError(f"Index the reference FASTA first: samtools faidx {reference}")
    for chrom, length in aln.lengths.items():
        # Paraphase only consults primary chromosomes. BAM already stores
        # its sequences, so its unused decoys need not be in the FASTA.
        if not aln.is_cram and not PRIMARY_CONTIG.fullmatch(chrom):
            continue
        if lengths.get(chrom) != length:
            raise UsageError(
                f"Reference FASTA and alignment disagree on {chrom} (name or length); "
                "supply the matching FASTA via --ref."
            )
    validate_calling_reference(
        aln.path, str(reference), allow_reference_subset=not aln.is_cram
    )
    with reference.open("rb") as stream:
        compressed = stream.read(2) == b"\x1f\x8b"
    name = "reference.fa.gz" if compressed else "reference.fa"
    if compressed:
        index = Path(f"{reference}.gzi")
        if not index.is_file():
            raise UsageError(
                f"Missing BGZF reference index {index}; run samtools faidx {reference}"
            )
        (work / f"{name}.gzi").symlink_to(index)
    (work / f"{name}.fai").symlink_to(Path(f"{reference}.fai"))
    (work / name).symlink_to(reference)
    return name


def _stage_alignment(aln: Alignment, work: Path) -> str:
    source = Path(aln.path).absolute()
    suffix = ".crai" if aln.is_cram else ".bai"
    name = f"reads{source.suffix.lower()}"
    index = alignment_index(source)
    if index is None or not Path(index).is_file():
        raise UsageError(f"Missing alignment index for {source}.")
    (work / name).symlink_to(source)
    # Paraphase v3 checks the .bai name, but HTSlib recognizes CSI by magic.
    (work / f"{name}{suffix}").symlink_to(Path(index))
    return name


def _legacy_tools(work: Path, bindir: Path, licensed: bool) -> list[str]:
    flags = []
    for tool in ("samtools", "minimap2"):
        path = shutil.which(str(bindir / tool))
        if path is None and licensed:
            path = shutil.which(tool)
        if path is None:
            hint = "put it on PATH" if licensed else INSTALL
            raise MissingDependencyError(f"Paraphase needs {tool}; {hint}.")
        (work / tool).symlink_to(Path(path).absolute())
        flags.extend([f"--{tool}", f"./{tool}"])
    return flags


def _locus_calls(
    raw: Path, chosen: list[str] | None, experimental: bool
) -> list[LocusCall]:
    try:
        report: object = json.loads(raw.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise WGSExtractError(
            f"Paraphase did not produce readable locus JSON: {error}"
        ) from error
    if not isinstance(report, dict) or not report:
        raise WGSExtractError("Paraphase produced no locus results.")
    if chosen and (missing := set(chosen) - report.keys()):
        raise WGSExtractError(
            f"Paraphase did not report requested loci: {', '.join(sorted(missing))}."
        )
    calls: list[LocusCall] = []
    for gene, value in sorted(report.items()):
        if not isinstance(value, dict):
            raise WGSExtractError(
                f"Invalid Paraphase result for {gene}: expected an object."
            )
        cn = value.get("total_cn")
        if cn is not None and (type(cn) is not int or cn < 0):
            raise WGSExtractError(
                f"Invalid Paraphase total_cn for {gene}: expected a nonnegative integer or null."
            )
        # SMN's caller reports distinct SMN1/SMN2 CN, not family total_cn.
        status: Literal["called", "partial", "no_call"] = "no_call"
        if cn is not None:
            status = "called"
        elif gene == "smn1":
            determined = sum(value.get(k) is not None for k in ("smn1_cn", "smn2_cn"))
            status = ("no_call", "partial", "called")[determined]
        calls.append(
            LocusCall(
                gene=gene,
                total_cn=cn,
                call_status=status,
                experimental=experimental,
                gene_fields={key: value[key] for key in GENE_FIELDS if key in value},
            )  # fmt: skip
        )
    return calls


def _publish(prefix: Path, raw_dir: Path, document: ParaphaseDocument) -> None:
    result = current_result()
    raw_files = {
        raw_dir / "sample.paraphase.json": ("raw.json", "paraphase_raw_json"),
        raw_dir / "sample.paraphase.bam": ("bam", "paraphase_bam"),
        raw_dir / "sample.paraphase.bam.bai": ("bam.bai", "paraphase_bam_index"),
    }
    for path in sorted((raw_dir / "sample_paraphase_vcfs").glob("*.vcf")):
        name = path.name.removeprefix("sample_")
        raw_files[path] = (f"raw.{name}", "paraphase_vcf")
    with tempfile.TemporaryDirectory(
        dir=prefix.parent, prefix=".paraphase-output-"
    ) as work:
        stage = Path(work)
        summary = stage / "calls.json"
        summary.write_text(
            json.dumps(document, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        table = stage / "calls.tsv"
        write_table(
            table, TABLE, (
                (call["gene"], call["total_cn"], call["call_status"], json.dumps(call["experimental"]),
                 json.dumps(call["gene_fields"], separators=(",", ":"), sort_keys=True))
                for call in document["loci"]
            ), "tsv",
        )  # fmt: skip
        staged = {}
        for i, (src, (suffix, role)) in enumerate(raw_files.items()):
            if not src.is_file():
                raise WGSExtractError(f"Paraphase did not produce {src.name}.")
            dst = stage / str(i)
            move_file(src, dst)
            staged[dst] = (Path(f"{prefix}.{suffix}"), role)
        staged[table] = (Path(f"{prefix}.tsv"), "paraphase_calls_tsv")
        staged[summary] = (Path(f"{prefix}.json"), "paraphase_calls_json")
        vcf_prefix = f"{prefix.name}.raw."
        with publication_lock(prefix):
            old_vcfs = [
                path
                for path in prefix.parent.iterdir()
                if path.name.startswith(vcf_prefix) and path.suffix == ".vcf"
            ]
            publish_output_set(
                {src: dst for src, (dst, _) in staged.items()}, retire=old_vcfs
            )
        for path, role in reversed(staged.values()):
            result.add_output(path, role)


def run_paraphase(params: TypingParaphaseParams) -> None:
    aln = open_alignment(params, purpose="typing paraphase", any_build=True)
    licensed = params.pacbio_licensed_executable is not None
    header = get_bam_header(aln.path, aln.reference)
    platform = _platform(header, licensed)
    genome = _genome(aln)
    experimental = platform == "ont"
    result = current_result()
    result.add_input(aln.require_reference(), "reference_fasta")
    result.set_reference(aln.reference, aln.build)
    prefix = Path(aln.outdir, f"{aln.stem}.paraphase")
    result.expect_output(Path(f"{prefix}.json"))
    with scratch_directory(aln.outdir, ".paraphase-") as directory:
        work = Path(directory)
        # Paraphase v3 interpolates these names into shell pipelines. Only
        # fixed, shell-safe aliases and catalog-checked loci reach those paths.
        reference = _stage_reference(aln, work)
        reads = _stage_alignment(aln, work)
        if licensed:
            _require_pacbio_reads(aln, header, work)
        executable, bindir, version, numbers, license_name = _engine(
            params.pacbio_licensed_executable
        )
        selected = _select_loci(params.genes, genome, numbers)
        result.record_tool_version("paraphase", version)
        result.add_metrics(
            paraphase_engine_version=version, paraphase_engine_license=license_name,
            paraphase_platform=platform, paraphase_experimental=experimental,
        )  # fmt: skip
        if experimental:
            warn(
                "paraphase_ont_experimental",
                "Paraphase on ONT reads is experimental and unvalidated.",
            )
        command = [
            str(executable), "-b", reads, "-r", reference, "-o", "raw",
            "-p", "sample", "--genome", genome, "-t", aln.threads,
        ]  # fmt: skip
        if selected is not None:
            command.extend(["-g", ",".join(selected)])
        if numbers[0] == 3:
            command.extend(_legacy_tools(work, bindir, licensed))
        run_command(command, cwd=work)
        raw_dir = work / "raw"
        calls = _locus_calls(raw_dir / "sample.paraphase.json", selected, experimental)
        document = ParaphaseDocument(
            schema=SCHEMA_ID, genome=genome, platform=platform, experimental=experimental,
            engine_version=version, engine_license=license_name, loci=calls,
        )  # fmt: skip
        _publish(prefix, raw_dir, document)
    result.set_details({"paraphase": document})
    called = sum(call["call_status"] == "called" for call in calls)
    human_output(f"Paraphase: {called}/{len(calls)} loci called. {prefix}.tsv")


cmd_paraphase = cli_handler(TypingParaphaseParams, run_paraphase)
