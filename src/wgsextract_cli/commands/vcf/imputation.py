"""Genotype imputation from the 1000 Genomes 30x panel (optional).

``vcf impute`` imputes one GRCh38 sample, chromosome by chromosome, against
the panel ``ref imputation`` installs: GLIMPSE2 for low-coverage reads (it
computes genotype likelihoods from the reads at the panel's sites), Beagle 5
for a genotype-only VCF such as microarray calls. Both come from the opt-in
``imputation`` Pixi environment, checked and never installed.

The output is labelled so nothing downstream mistakes it for direct calls:
every imputed record has INFO/IMP, the header names the tool and panel in a
``##wgsextract_imputation`` line, and imputed records below the per-genotype
GP threshold are FILTERed ``ImpLowConf``. Site INFO/DR2 filtering is opt-in.
"""

import hashlib
import logging
import platform
import sys
import tempfile
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.vcf import VcfImputeParams
from wgsextract_cli.core import imputation_panel
from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.atomic_output import safe_output
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.genotype_qc import AUTOSOMES, target_depths
from wgsextract_cli.core.imputed_vcf import HEADER_KEY
from wgsextract_cli.core.imputed_vcf import LOW_CONFIDENCE_FILTER as LOW_CONFIDENCE
from wgsextract_cli.core.pixi_tools import pixi_env_prefix
from wgsextract_cli.core.reference_assets import reference_library_of
from wgsextract_cli.core.reference_models import sequence_lengths
from wgsextract_cli.core.reference_resolver import ReferenceLibrary
from wgsextract_cli.core.resource_estimates import (
    estimate_imputation,
    preflight_resources,
)
from wgsextract_cli.core.results import Scalar, current_result, warn
from wgsextract_cli.core.samtools_commands import alignment_index, parse_memory
from wgsextract_cli.core.ultima import read_groups
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    get_resource_defaults,
    run_command,
    run_pipeline,
)
from wgsextract_cli.core.variant_files import (
    calculate_bam_md5,
    ensure_vcf_indexed,
    verify_paths_exist,
)

ENV = "imputation"
OUTPUT = "imputed.vcf.gz"
# Beagle 5.5 (27Feb25.75f), as Bioconda's beagle package ships it and as
# https://faculty.washington.edu/browning/beagle/beagle.27Feb25.75f.jar serves it.
BEAGLE_JAR_SHA256 = "7319f4af9638be05c18dcc1bfb8fb41a58a09293507ebf0d54617d0e40df5a70"
# Mean autosomal depth above which calling the reads directly beats imputing.
DIRECT_CALLING_DEPTH = 8.0
UNSUPPORTED = (
    "Imputation needs GLIMPSE2 and Beagle from the `imputation` Pixi "
    "environment, which Bioconda builds for Linux and Intel macOS only. Run "
    "`vcf impute` on Linux (for example under WSL2) or an Intel Mac."
)


def require_supported_platform() -> None:
    apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
    if sys.platform == "win32" or apple_silicon:
        raise WGSExtractError(UNSUPPORTED)


def _library(ref: Path | None) -> str | None:
    return reference_library_of(ref) if ref else None


def _chromosomes(text: str | None, reflib: str | None) -> list[str]:
    if text:
        return imputation_panel.chromosomes(text)
    found = imputation_panel.installed_chromosomes(reflib)
    if not found:
        raise UsageError(
            "No imputation panel is installed. Install the chromosomes to impute, "
            "e.g. 'wgsextract ref imputation --install --chromosomes chr20' "
            "(or --chromosomes all, about 30 GB to download)."
        )
    return found


def _panel_length(panel: Path, chrom: str) -> int | None:
    header = run_command(["bcftools", "view", "-h", panel], capture_output=True)
    return sequence_lengths(header.stdout).get(chrom)


def _check_contigs(
    lengths: dict[str, int], panels: list[imputation_panel.ChromosomePanel], what: str
) -> None:
    """Each chromosome is in ``what`` under the panel's name and GRCh38 length."""
    for panel in panels:
        expected = _panel_length(panel.panel, panel.chrom)
        found = lengths.get(panel.chrom)
        if found is None:
            raise UsageError(
                f"{what} has no {panel.chrom}: the imputation panel is GRCh38 with "
                "contigs named chr1, chr2...; realign or rename to match."
            )
        if expected is not None and found != expected:
            raise UsageError(
                f"{what}'s {panel.chrom} is {found:,} bp, not GRCh38's {expected:,}: "
                "the imputation panel is GRCh38 only."
            )


def _reads_sample(header: str, reads: str) -> str:
    names = {g["SM"] for g in read_groups(header) if g.get("SM")}
    if len(names) > 1:
        raise UsageError(
            f"The reads hold several samples ({', '.join(sorted(names))}); "
            "imputation needs one sample's reads."
        )
    return names.pop() if names else Path(reads).name.split(".")[0]


def _warn_depth(reads: str, outdir: str, ref: str | None) -> float | None:
    found = target_depths(reads, outdir, ref)
    depth = found[0].get(AUTOSOMES) if found else None
    if depth is not None and depth > DIRECT_CALLING_DEPTH:
        warn(
            "high_depth_imputation",
            f"The reads average {depth:.1f}x on the autosomes; above "
            f"{DIRECT_CALLING_DEPTH:g}x, calling them directly (`vcf snp`) is as "
            "accurate as imputing and keeps rare variants.",
            depth=round(depth, 2),
        )
    return depth


def _glimpse(
    params: VcfImputeParams,
    panels: list[imputation_panel.ChromosomePanel],
    threads: str,
    outdir: str,
    work: Path,
) -> tuple[list[Path], dict[str, Scalar]]:
    reads = str(params.input)
    verify_paths_exist({"--input": reads})
    current_result().add_input(reads, "input")
    if not alignment_index(reads):
        raise UsageError(
            f"{reads} has no index; GLIMPSE2 reads it by region. Index it with "
            f"`samtools index {reads}`."
        )
    lib = ReferenceLibrary(
        str(params.ref) if params.ref else None,
        calculate_bam_md5(reads, None),
        input_path=reads,
    )
    fasta = lib.fasta if lib.fasta and Path(lib.fasta).is_file() else None
    if reads.lower().endswith(".cram") and not fasta:
        raise UsageError("Reading a CRAM needs its reference: pass --ref.")
    if fasta:
        current_result().set_reference(fasta, lib.build)
    header = get_bam_header(reads, fasta)
    _check_contigs(sequence_lengths(header), panels, reads)
    sample = _reads_sample(header, reads)
    depth = _warn_depth(reads, outdir, fasta)
    parts = []
    for panel in panels:
        chunks = []
        for i, (_in_region, out_region, binary) in enumerate(panel.chunks):
            out = work / f"{panel.chrom}.{i}.bcf"
            logging.info(
                f"GLIMPSE2: {panel.chrom} chunk {i + 1} of {len(panel.chunks)} "
                f"({out_region})"
            )
            run_command(
                ["GLIMPSE2_phase", "--bam-file", reads, "--ind-name", sample,
                 "--reference", binary, "--output", out, "--threads", threads,
                 *(["--fasta", fasta] if fasta else []),
                 "--log", work / f"{panel.chrom}.{i}.log"]
            )  # fmt: skip
            chunks.append(out)
        listing = work / f"{panel.chrom}.chunks.txt"
        listing.write_text("".join(f"{c}\n" for c in chunks), newline="\n")
        ligated = work / f"{panel.chrom}.bcf"
        run_command(
            ["GLIMPSE2_ligate", "--input", listing, "--output", ligated,
             "--threads", threads]
        )  # fmt: skip
        if not ligated.is_file():
            raise WGSExtractError(f"GLIMPSE2_ligate wrote no {panel.chrom} output.")
        parts.append(ligated)
    metrics: dict[str, Scalar] = {"sample": sample}
    if depth is not None:
        metrics["autosomal_depth"] = round(depth, 2)
    return parts, metrics


def beagle_command(prefix: Path) -> list[str]:
    """``java -jar beagle.jar`` from the imputation environment, the jar checked
    against its pinned digest."""
    jars = sorted(prefix.glob("share/beagle-*/beagle.jar"))
    java = prefix / "bin" / "java"
    if not jars or not java.is_file():
        raise WGSExtractError(
            f"Beagle is not installed in the {ENV} environment. Install it with "
            f"`pixi install -e {ENV}`."
        )
    digest = hashlib.sha256(jars[-1].read_bytes()).hexdigest()
    if digest != BEAGLE_JAR_SHA256:
        raise WGSExtractError(
            f"{jars[-1]} is not the pinned Beagle 5.5 (27Feb25.75f) jar."
        )
    return [str(java), "-jar", str(jars[-1])]


def _vcf_sample(vcf: str) -> str:
    out = run_command(["bcftools", "query", "-l", vcf], capture_output=True).stdout
    samples = [s for s in out.splitlines() if s]
    if len(samples) != 1:
        raise UsageError(
            f"{vcf} has {len(samples)} samples; imputation takes one sample's VCF."
        )
    return samples[0]


def _beagle(
    params: VcfImputeParams,
    panels: list[imputation_panel.ChromosomePanel],
    threads: str,
    memory: str,
    work: Path,
) -> tuple[list[Path], dict[str, Scalar]]:
    vcf = str(params.vcf_input)
    verify_paths_exist({"--vcf-input": vcf})
    current_result().add_input(vcf, "vcf_input")
    prefix = pixi_env_prefix(ENV)
    if prefix is None:
        raise WGSExtractError(
            f"The {ENV} environment is not installed. Install it with "
            f"`pixi install -e {ENV}`."
        )
    beagle = beagle_command(prefix)
    sample = _vcf_sample(vcf)
    header = run_command(["bcftools", "view", "-h", vcf], capture_output=True).stdout
    lengths = sequence_lengths(header)
    rename = None
    if not any(c.startswith("chr") for c in lengths):
        # Chip VCFs often name contigs 1, 2...; the panel's are chr1, chr2...
        rename = work / "rename.txt"
        rename.write_text("".join(f"{c}\tchr{c}\n" for c in lengths), newline="\n")
        lengths = {f"chr{c}": n for c, n in lengths.items()}
    _check_contigs(lengths, panels, vcf)
    try:
        amount, unit = parse_memory(memory)
    except ValueError as e:
        raise UsageError(f"--memory: {e}") from e
    # Beagle's heap: the per-thread memory times the threads, in megabytes.
    heap = int(amount * (1024 if unit == "G" else 1) * int(threads))
    parts = []
    for panel in panels:
        bare = panel.chrom.removeprefix("chr") if rename else panel.chrom
        gt = work / f"{panel.chrom}.gt.vcf.gz"
        # Beagle wants one sample's biallelic genotypes and nothing else.
        run_pipeline(
            [
                ["bcftools", "view", "-t", bare, "-s", sample, "-Ou", vcf],
                *([["bcftools", "annotate", "--rename-chrs", str(rename), "-Ou"]]
                  if rename else []),
                ["bcftools", "norm", "-m", "-any", "-Ou"],
                # Missing probes must become absent markers, so Beagle marks
                # their replacement genotypes IMP rather than as chip calls.
                ["bcftools", "view", "-m2", "-M2", "-v", "snps",
                 "-e", 'GT="mis"', "-Ou"],
                ["bcftools", "annotate", "-x", "ID,INFO,^FORMAT/GT", "-Oz",
                 "-o", str(gt)],
            ],
            description=f"Preparing {panel.chrom} genotypes for Beagle",
        )  # fmt: skip
        out = work / f"{panel.chrom}.beagle"
        logging.info(f"Beagle: imputing {panel.chrom}")
        run_command(
            [beagle[0], f"-Xmx{heap}m", *beagle[1:], f"gt={gt}",
             f"ref={panel.panel}", f"map={panel.plink_map}", f"out={out}",
             f"chrom={panel.chrom}", f"nthreads={threads}", "gp=true"]
        )  # fmt: skip
        # Beagle writes no ##contig lines, which bcftools needs.
        contig = work / f"{panel.chrom}.contig.txt"
        length = _panel_length(panel.panel, panel.chrom)
        contig.write_text(
            f"##contig=<ID={panel.chrom},length={length}>\n", newline="\n"
        )
        ensure_vcf_indexed(f"{out}.vcf.gz")
        part = work / f"{panel.chrom}.vcf.gz"
        run_command(
            ["bcftools", "annotate", "-h", contig,
             "-a", panel.panel, "-c", "INFO/AC,INFO/AN", "-Oz", "-o", part,
             f"{out}.vcf.gz"]
        )  # fmt: skip
        parts.append(part)
    return parts, {"sample": sample}


def _header_lines(tool: str, version: str, params: VcfImputeParams) -> str:
    site_filter = f"MinINFO={params.min_info:g}," if params.min_info is not None else ""
    return (
        '##INFO=<ID=IMP,Number=0,Type=Flag,Description="Imputed genotype">\n'
        f'##{HEADER_KEY}=<Tool={tool},Version="{version}",'
        f'Panel="{imputation_panel.PANEL_LABEL}",Build=GRCh38,'
        f"MinGP={params.gp_threshold:g},{site_filter}"
        f"Filter={LOW_CONFIDENCE}>\n"
    )


def _label(
    parts: list[Path],
    tool: str,
    version: str,
    params: VcfImputeParams,
    work: Path,
    out: str,
) -> None:
    """Concatenate the chromosomes, mark imputed records INFO/IMP, and FILTER
    the low-confidence ones."""
    header = work / "header.txt"
    header.write_text(_header_lines(tool, version, params), newline="\n")
    concat = ["bcftools", "concat", "-Ou", *map(str, parts)]
    if tool == "GLIMPSE2":
        # Every GLIMPSE2 genotype is imputed: mark each chromosome's records.
        ranges = work / "imp.tsv"
        ranges.write_text(
            "".join(f"{p.stem}\t1\t536870911\t1\n" for p in parts), newline="\n"
        )
        run_command(["bgzip", "-f", ranges])
        run_command(["tabix", "-f", "-s1", "-b2", "-e3", f"{ranges}.gz"])
        annotate = ["bcftools", "annotate", "-h", str(header), "-a",
                    f"{ranges}.gz", "-c", "CHROM,FROM,TO,INFO/IMP", "-Ou"]  # fmt: skip
        score = "INFO"
    else:
        # Beagle marks imputed markers IMP itself; the chip's own are not.
        lines = header.read_text().splitlines(keepends=True)[1:]
        header.write_text("".join(lines), newline="\n")
        annotate = ["bcftools", "annotate", "-h", str(header), "-Ou"]
        score = "DR2"
    criteria = [f"MAX(FMT/GP)<{params.gp_threshold:g}"]
    if params.min_info is not None:
        criteria.append(f"INFO/{score}<{params.min_info:g}")
    low = f"INFO/IMP=1 && ({' || '.join(criteria)})"
    run_pipeline(
        [
            concat,
            annotate,
            ["bcftools", "filter", "-m", "+", "-s", LOW_CONFIDENCE, "-e", low,
             "-Oz", "-o", out],
        ],
        description="Labelling the imputed genotypes",
    )  # fmt: skip


def _summary(vcf: str) -> dict[str, Scalar]:
    out = run_command(
        ["bcftools", "query", "-i", "INFO/IMP=1", "-f", "%FILTER\t[%GT]\n", vcf],
        capture_output=True,
    ).stdout
    imputed = low = nonref = 0
    for line in out.splitlines():
        filt, gt = line.split("\t")
        imputed += 1
        if LOW_CONFIDENCE in filt.split(";"):
            low += 1
        elif "1" in gt:
            nonref += 1
    return {
        "imputed_sites": imputed,
        "low_confidence_sites": low,
        "confident_nonref_genotypes": nonref,
    }


def run_impute(params: VcfImputeParams) -> None:
    """``vcf impute``: impute a sample into ``imputed.vcf.gz``."""
    require_supported_platform()
    reads_mode = params.input is not None
    verify_dependencies(["bcftools", "bgzip", "tabix"])
    if reads_mode:
        verify_dependencies(
            ["GLIMPSE2_phase", "GLIMPSE2_ligate"], ["GLIMPSE2_phase", "GLIMPSE2_ligate"]
        )
        log_dependency_info(["GLIMPSE2_phase"])
    reflib = _library(params.ref)
    chroms = _chromosomes(params.chromosomes, reflib)
    version = imputation_panel.glimpse_version() if reads_mode else "5.5 (27Feb25.75f)"
    panels = [
        imputation_panel.require(c, reflib, version if reads_mode else None)
        for c in chroms
    ]
    threads, memory = get_resource_defaults(params.threads, params.memory)
    source = params.input or params.vcf_input
    assert source is not None
    outdir = str(params.outdir or Path(source).expanduser().absolute().parent)
    Path(outdir).mkdir(parents=True, exist_ok=True)
    out_vcf = str(Path(outdir, OUTPUT))
    tool = "GLIMPSE2" if reads_mode else "Beagle"
    preflight_resources(
        lambda: estimate_imputation(
            reads=reads_mode,
            threads=int(threads),
            bases=sum(_panel_length(p.panel, p.chrom) or 0 for p in panels),
        )
    )
    logging.info(f"Imputing {', '.join(chroms)} with {tool} into {out_vcf}")
    with tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_impute_") as tmp:
        work = Path(tmp)
        if reads_mode:
            parts, metrics = _glimpse(params, panels, threads, outdir, work)
        else:
            parts, metrics = _beagle(params, panels, threads, memory, work)
        with safe_output(out_vcf) as staged:
            _label(parts, tool, version, params, work, staged)
    ensure_vcf_indexed(out_vcf)
    metrics.update(
        tool=tool,
        tool_version=version,
        panel=imputation_panel.PANEL_LABEL,
        chromosomes=",".join(chroms),
        min_gp=params.gp_threshold,
        min_info=params.min_info,
        **_summary(out_vcf),
    )
    result = current_result()
    result.add_output(out_vcf, "imputed_variants", metrics=metrics)
    result.add_metrics(**metrics)
    site_filter = (
        f" or INFO < {params.min_info:g}" if params.min_info is not None else ""
    )
    logging.info(
        f"Imputed {metrics['imputed_sites']:,} sites; "
        f"{metrics['low_confidence_sites']:,} are {LOW_CONFIDENCE} "
        f"(max GP < {params.gp_threshold:g}{site_filter})."
    )


cmd_impute = cli_handler(VcfImputeParams, run_impute)
