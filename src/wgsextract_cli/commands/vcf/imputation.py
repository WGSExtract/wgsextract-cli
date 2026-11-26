"""Genotype imputation from the 1000 Genomes 30x panel (optional).

``vcf impute`` imputes one GRCh38 sample, chromosome by chromosome, against
the panel ``ref imputation`` installs: GLIMPSE2 for low-coverage reads (it
computes genotype likelihoods from the reads at the panel's sites), Beagle 5
for a genotype-only VCF such as microarray calls. Both come from the opt-in
``imputation`` Pixi environment, checked and never installed.

GLIMPSE2's threads only split work between samples, and there is one, so its
chunks run side by side instead, one thread each (``core.job_pool``): up to
``--threads`` at once, largest first, within a memory budget estimated from
each chunk's binary reference. Each chunk's output is the same however many
run, so the result matches running them one at a time.

The output is labelled so nothing downstream mistakes it for direct calls:
every imputed record has INFO/IMP, the header names the tool and panel in a
``##wgsextract_imputation`` line, and imputed records below the per-genotype
GP threshold are FILTERed ``ImpLowConf``. Site INFO/DR2 filtering is opt-in.
"""

import functools
import hashlib
import itertools
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.vcf import VcfImputeParams
from wgsextract_cli.core import imputation_panel
from wgsextract_cli.core.alignment.memory import available_memory
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.alignment.samtools_commands import (
    alignment_index,
    parse_memory,
)
from wgsextract_cli.core.artifact_facts import index_paths
from wgsextract_cli.core.atomic_output import safe_output
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.genotype_qc import AUTOSOMES, target_depths
from wgsextract_cli.core.imputation_panel import (
    require_supported_platform as require_supported_platform,
)
from wgsextract_cli.core.imputed_vcf import HEADER_KEY
from wgsextract_cli.core.imputed_vcf import LOW_CONFIDENCE_FILTER as LOW_CONFIDENCE
from wgsextract_cli.core.job_pool import Job, run_jobs
from wgsextract_cli.core.pixi_tools import pixi_env_prefix
from wgsextract_cli.core.ref_library.assets import reference_library_of
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary
from wgsextract_cli.core.resource_estimates import (
    GIB,
    estimate_imputation,
    preflight_resources,
)
from wgsextract_cli.core.results import Scalar, current_result, warn
from wgsextract_cli.core.temp_directory import scratch_parent
from wgsextract_cli.core.ultima import read_groups
from wgsextract_cli.core.utils import (
    ToolExecutionError,
    UsageError,
    WGSExtractError,
    get_resource_defaults,
    get_resource_threads,
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
# GLIMPSE2 2.0.1 phase at one thread peaked at about 18 bytes of RSS per byte
# of its chunk's binary reference plus 0.6 GB on HG002 1x chr20 (#1118):
# 1.2 GB for a typical chunk, 3.0 GB for the centromeric one. With a margin:
CHUNK_RSS_PER_BIN_BYTE = 20
CHUNK_RSS_BASE = 768 * 1024**2
# The share of the memory available at the start the chunks may use together.
MEMORY_SHARE = 0.8


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


def chunk_memory(binary: Path) -> int:
    """GLIMPSE2_phase's estimated peak RSS for one chunk at one thread."""
    return CHUNK_RSS_PER_BIN_BYTE * binary.stat().st_size + CHUNK_RSS_BASE


def glimpse_memory_budget(memory: str | None, jobs: int) -> int:
    """The memory the chunks running at once may use: ``--memory`` per job,
    or a share of the memory available now."""
    if memory is None or memory.strip().lower() == "auto":
        return int(available_memory() * MEMORY_SHARE)
    try:
        amount, unit = parse_memory(memory)
    except ValueError as e:
        raise UsageError(f"--memory: {e}") from e
    return int(amount * (GIB if unit == "G" else 1024**2) * jobs)


def _glimpse_tool(
    cmd: list[object], log: Path, what: str, *, report_to_log: bool = False
) -> None:
    """Run a GLIMPSE2 tool whose report goes to ``log``, not the terminal
    (chunks run side by side); a failure quotes the log's end."""
    try:
        with open(log if report_to_log else os.devnull, "w", newline="\n") as report:
            run_command(cmd, stdout=report)
    except ToolExecutionError as e:
        tail = ""
        if log.is_file():
            lines = log.read_text(errors="replace").strip().splitlines()
            errors = [i for i, line in enumerate(lines) if line.startswith("ERROR")]
            tail = "\n".join(lines[errors[-1] :] if errors else lines[-10:])
        raise WGSExtractError(
            f"{Path(str(cmd[0])).name} failed on {what} (exit {e.returncode}):"
            f"\n{tail or e}"
        ) from e


def _glimpse(
    params: VcfImputeParams,
    panels: list[imputation_panel.ChromosomePanel],
    jobs: int,
    memory_budget: int,
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
    total = sum(len(panel.chunks) for panel in panels)
    finished = itertools.count(1)

    def phase(chrom: str, i: int, region: str, binary: Path) -> None:
        log = work / f"{chrom}.{i}.log"
        # One thread: GLIMPSE2 shares threads among samples, and there is one.
        _glimpse_tool(
            ["GLIMPSE2_phase", "--bam-file", reads, "--ind-name", sample,
             "--reference", binary, "--output", work / f"{chrom}.{i}.bcf",
             "--threads", "1", *(["--fasta", fasta] if fasta else []),
             "--log", log],
            log, f"{chrom} chunk {i + 1} ({region})",
        )  # fmt: skip
        logging.info(
            f"GLIMPSE2: phased {chrom} chunk {i + 1} ({region}), "
            f"{next(finished)} of {total} chunks"
        )

    def ligate(panel: imputation_panel.ChromosomePanel) -> None:
        listing = work / f"{panel.chrom}.chunks.txt"
        listing.write_text(
            "".join(
                f"{work / f'{panel.chrom}.{i}.bcf'}\n" for i in range(len(panel.chunks))
            ),
            newline="\n",
        )
        ligated = work / f"{panel.chrom}.bcf"
        # GLIMPSE2_ligate has no --log: its report goes there instead.
        _glimpse_tool(
            ["GLIMPSE2_ligate", "--input", listing, "--output", ligated,
             "--threads", "1"],
            work / f"{panel.chrom}.ligate.log", panel.chrom, report_to_log=True,
        )  # fmt: skip
        if not ligated.is_file():
            raise WGSExtractError(f"GLIMPSE2_ligate wrote no {panel.chrom} output.")

    chunks = [
        Job(
            f"{panel.chrom} chunk {i + 1}",
            functools.partial(phase, panel.chrom, i, out_region, binary),
            chunk_memory(binary),
            panel.chrom,
        )
        for panel in panels
        for i, (_in_region, out_region, binary) in enumerate(panel.chunks)
    ]
    # Largest first, so the slowest chunks don't start last.
    chunks.sort(key=lambda job: job.memory, reverse=True)
    then = {
        panel.chrom: Job(f"{panel.chrom} ligate", functools.partial(ligate, panel))
        for panel in panels
    }
    logging.info(
        f"GLIMPSE2: {total} chunks, up to {jobs} at once within "
        f"{memory_budget / GIB:.1f} GiB"
    )
    run_jobs(chunks, workers=jobs, memory=memory_budget, then=then)
    parts = [work / f"{panel.chrom}.bcf" for panel in panels]
    metrics: dict[str, Scalar] = {"sample": sample}
    if depth is not None:
        metrics["autosomal_depth"] = round(depth, 2)
    return parts, metrics


def beagle_command(prefix: Path) -> list[str]:
    """``java -jar beagle.jar`` from the imputation environment, the jar checked
    against its pinned digest."""
    jars = sorted(prefix.glob("share/beagle-*/beagle.jar"))
    if sys.platform == "win32":
        launchers = [prefix / "Library" / "bin" / "java.exe"]
    else:
        # conda-forge's OpenJDK may expose only its JVM home, not a bin symlink.
        launchers = [prefix / "bin" / "java", prefix / "lib" / "jvm" / "bin" / "java"]
    if not jars:
        raise WGSExtractError(
            f"Beagle jar missing: expected {prefix / 'share'}/beagle-*/beagle.jar. "
            f"Restore it with `pixi install -e {ENV}`."
        )
    java = next((launcher for launcher in launchers if launcher.is_file()), None)
    if java is None:
        expected = " or ".join(str(launcher) for launcher in launchers)
        raise WGSExtractError(
            f"Java executable missing: expected {expected}. Restore OpenJDK with "
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
    names = set(re.findall(r"^##contig=<ID=([^,>]+)", header, re.MULTILINE))
    for panel in panels:
        for name in (panel.chrom, panel.chrom.removeprefix("chr")):
            if name in names and name not in lengths:
                raise UsageError(
                    f"{vcf} declares {name} without a length; cannot verify GRCh38. "
                    "Supply GRCh38 contig lengths with "
                    "`bcftools reheader --fai GRCh38.fa.fai` before imputing."
                )
    rename = None
    if not any(c.startswith("chr") for c in names):
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
        observed = 'GT="mis"'
        if re.search(r"^##INFO=<ID=IMP,", header, re.MULTILINE):
            observed += " || INFO/IMP=1"
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
                 "-e", observed, "-Ou"],
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
        imputed = Path(f"{out}.vcf.gz")
        ensure_vcf_indexed(imputed)
        gt.unlink()
        part = work / f"{panel.chrom}.vcf.gz"
        run_command(
            ["bcftools", "annotate", "-h", contig,
             "-a", panel.panel,
             "-c", "INFO/WGSE_PANEL_AC:=INFO/AC,INFO/WGSE_PANEL_AN:=INFO/AN",
             "-Oz", "-o", part,
             imputed]
        )  # fmt: skip
        for index in index_paths(imputed, "vcf"):
            Path(index).unlink()
        imputed.unlink()
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
    imputed = low = nonref = 0
    with tempfile.TemporaryFile(mode="w+t", dir=Path(vcf).parent) as rows:
        run_command(
            ["bcftools", "query", "-i", "INFO/IMP=1", "-f", "%FILTER\t[%GT]\n", vcf],
            stdout=rows,
        )
        rows.seek(0)
        for line in rows:
            filt, gt = line.rstrip("\n").split("\t")
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
    source = params.input or params.vcf_input
    assert source is not None
    outdir = str(
        (params.outdir or Path(source).expanduser().absolute().parent).expanduser()
    )
    out_vcf = str(Path(outdir, OUTPUT))
    source_path = Path(source).expanduser()
    if Path(out_vcf).resolve() == source_path.resolve() or (
        Path(out_vcf).exists()
        and source_path.exists()
        and Path(out_vcf).samefile(source_path)
    ):
        raise UsageError(
            f"vcf impute would overwrite its input {source}; choose another --outdir."
        )
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
    if reads_mode:
        # GLIMPSE2 runs a chunk per thread, side by side.
        threads, memory = get_resource_threads(params.threads), params.memory
    else:
        threads, memory = get_resource_defaults(params.threads, params.memory)
    budget = glimpse_memory_budget(memory, int(threads)) if reads_mode else None
    Path(outdir).mkdir(parents=True, exist_ok=True)
    tool = "GLIMPSE2" if reads_mode else "Beagle"
    preflight_resources(
        lambda: estimate_imputation(
            reads=reads_mode,
            threads=int(threads),
            bases=sum(_panel_length(p.panel, p.chrom) or 0 for p in panels),
            chunk_memory=[
                chunk_memory(binary) for p in panels for _i, _o, binary in p.chunks
            ]
            if reads_mode
            else (),
            memory_budget=budget,
        )
    )
    logging.info(f"Imputing {', '.join(chroms)} with {tool} into {out_vcf}")
    with tempfile.TemporaryDirectory(
        dir=scratch_parent(outdir), prefix=".wgse_impute_"
    ) as tmp:
        work = Path(tmp)
        if budget is not None:
            parts, metrics = _glimpse(
                params, panels, int(threads), budget, outdir, work
            )
        else:
            assert memory is not None
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
