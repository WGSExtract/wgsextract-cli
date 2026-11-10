"""`typing smn`: SMN1/SMN2 copy number for SMA carrier status.

SMNCopyNumberCaller (Illumina; v1.1.2, Apache-2.0, upstream archived) calls
the copy number of full-length SMN1, SMN2 and SMN2Δ7–8 from the depth and the
SMN1/SMN2 base differences of a ~30x short-read WGS BAM/CRAM (Chen et al.
2020, Genet Med). One SMN1 copy is an SMA carrier; none, SMA. Two SMN1 copies
can still be a "2+0" silent carrier (both on one chromosome); g.27134T>G, which
it also counts, is associated with that arrangement.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.genotyping import TypingSmnParams
from wgsextract_cli.commands.info.metrics import is_long_read_library
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.pixi_tools import pixi_env_prefix
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import WGSExtractError, run_command

from .common import Alignment, env_tool, illumina_genome, open_alignment

NOT_DIAGNOSIS = (
    "SMN copy number from sequencing is a screening estimate, not a diagnosis; "
    "confirm with a clinical test (MLPA or qPCR) and genetic counselling."
)
# SMNCopyNumberCaller's GMMs and depth normalization are calibrated on, and
# documented for, WGS of at least 30x (#619): below it no calls are reported.
MIN_DEPTH = 30.0
# SMNCopyNumberCaller warns about uneven coverage above this MAD.
MAX_COVERAGE_MAD = 0.11
# SMN1 exons 7-8 (its "SMN1_Exon_7-8" region), to sample the read length.
# SMN1 and SMN2 exons 7-8 (its "Exon_7-8" regions), where the read length is
# sampled; reads of a sample without SMN1 map to SMN2.
_SMN_EXON78 = {
    "hg38": ("70948274-70954173", "70072856-70078755"),
    "hg19": ("70244101-70250000", "69368683-69374582"),
}
_LONG_READ_PLATFORMS = ("ONT", "NANOPORE", "PACBIO")


# Aligners SMNCopyNumberCaller was calibrated or tested on (its README: BWA and
# Isaac; DRAGEN's SMN caller derives from it). Novoalign, for one, places the
# 5q13 paralogs' reads differently: GIAB's HG002 Novoalign BAMs give SMN1 1,
# SMN2 1 where its bwa-mem BAM gives the truth, 2 and 2 (#619).
CALIBRATED_ALIGNERS = ("bwa", "bwa-mem2", "dragen", "isaac")
OTHER_ALIGNERS = (
    "novoalign", "bowtie2", "bowtie", "minimap2", "pbmm2", "ngmlr", "snap",
    "gem-mapper", "hisat2", "star", "bwa-mem3", "strobealign", "bwameth",
)  # fmt: skip


def detect_aligner(header: str) -> str | None:
    """The first aligner the header's @PG lines name (by PN, else ID)."""
    for line in header.splitlines():
        if not line.startswith("@PG"):
            continue
        tags = dict(f.split(":", 1) for f in line.split("\t")[1:] if ":" in f)
        for name in (tags.get("PN", ""), tags.get("ID", "").split(".")[0]):
            name = name.lower()
            if name.startswith("dragen"):
                return "dragen"
            if name in CALIBRATED_ALIGNERS or name in OTHER_ALIGNERS:
                return name
    return None


@dataclass(frozen=True)
class SmnCall:
    smn1: int | None
    smn2: int | str | None  # "2-3" when only a range is known
    smn2_delta78: int | None
    is_carrier: bool | None
    is_sma: bool | None
    g27134tg: int | None
    info: str | None
    median_depth: float | None
    coverage_mad: float | None


def _finite(value: object) -> float | None:
    """A number, or None for a missing or non-finite one (NaN from an empty
    sampling region)."""
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def parse_smn(json_path: str) -> SmnCall:
    with open(json_path) as f:
        result: dict[str, Any] = next(iter(json.load(f).values()))
    return SmnCall(
        smn1=result.get("SMN1"),
        smn2=result.get("SMN2"),
        smn2_delta78=result.get("SMN2delta78"),
        is_carrier=result.get("isCarrier"),
        is_sma=result.get("isSMA"),
        g27134tg=result.get("g27134TG_CN"),
        info=result.get("Info"),
        median_depth=_finite(result.get("Median_depth")),
        coverage_mad=_finite(result.get("Coverage_MAD")),
    )


def refuse_long_reads(aln: Alignment, header: str) -> None:
    """SMNCopyNumberCaller's model is for short reads: refuse input with any
    long-read read group, or whose SMN reads are long or absent."""
    platforms = {
        tag[3:].upper()
        for line in header.splitlines()
        if line.startswith("@RG")
        for tag in line.split("\t")
        if tag.startswith("PL:")
    }
    if platforms & set(_LONG_READ_PLATFORMS):
        raise WGSExtractError(
            "typing smn needs short-read (Illumina-style) WGS: SMNCopyNumberCaller "
            "is calibrated on ~30x short reads, and this input has long-read "
            f"read groups ({', '.join(sorted(platforms))})."
        )
    regions = [f"{aln.contig('5')}:{r}" for r in _SMN_EXON78[aln.build]]
    reads = run_command(
        ["samtools", "view", "-F", "0x904", *aln.ref_opt, aln.path, *regions],
        capture_output=True,
    ).stdout.splitlines()
    lengths = [len(f[9]) for f in (r.split("\t") for r in reads) if len(f) > 9]
    if not lengths:
        raise WGSExtractError(
            "The input has no reads at SMN1/SMN2 exons 7-8: typing smn needs "
            "~30x whole-genome short-read data, not a targeted or empty file."
        )
    mean = sum(lengths) / len(lengths)
    if is_long_read_library(mean, header, reads[0].split("\t", 1)[0]):
        raise WGSExtractError(
            "typing smn needs short-read (Illumina-style) WGS: SMNCopyNumberCaller "
            "is calibrated on ~30x short reads, and this input has long reads."
        )


def caller_version(caller: list[str]) -> str | None:
    """The version of the SMNCopyNumberCaller that runs, from the conda-meta
    of its environment: the smn one under `pixi run -e smn`, else the prefix
    the script is in (it has no --version)."""
    if "-e" in caller and caller[caller.index("-e") + 1] == "smn":
        prefix = pixi_env_prefix("smn")
    else:
        script = shutil.which(caller[-1]) or caller[-1]
        prefix = Path(script).resolve().parent.parent
    for record in (
        sorted(prefix.glob("conda-meta/smncopynumbercaller-*.json")) if prefix else []
    ):
        try:
            return str(json.loads(record.read_text())["version"])
        except (OSError, ValueError, KeyError):
            return None
    return None


def run_smn(params: TypingSmnParams) -> None:
    aln = open_alignment(params)
    genome = illumina_genome(aln, "SMNCopyNumberCaller")
    caller = env_tool("smn_caller.py", "smn")
    header = run_command(
        ["samtools", "view", "-H", *aln.ref_opt, aln.path], capture_output=True
    ).stdout
    refuse_long_reads(aln, header)
    aligner = detect_aligner(header)

    prefix = f"{aln.stem}.smn"
    with tempfile.TemporaryDirectory(dir=aln.outdir, prefix=".wgse_smn_") as work:
        manifest = str(Path(work, "manifest.txt"))
        with open(manifest, "w", newline="\n") as f:
            f.write(str(absolute_path(aln.path)) + "\n")
        command = [
            *caller, "--manifest", manifest, "--genome", genome, "--outDir", work,
            "--prefix", prefix, "--threads", aln.threads,
        ]  # fmt: skip
        if aln.is_cram:
            command += ["--reference", aln.require_reference()]
        logging.info("Calling SMN1/SMN2 copy number with SMNCopyNumberCaller...")
        try:
            run_command(command)
        except subprocess.CalledProcessError as e:
            raise WGSExtractError(
                f"SMNCopyNumberCaller failed (exit {e.returncode})."
            ) from e
        staged = {suffix: Path(work, prefix + suffix) for suffix in (".tsv", ".json")}
        for suffix, path in staged.items():
            if not path.is_file():
                raise WGSExtractError(
                    f"SMNCopyNumberCaller finished without writing {prefix}{suffix}."
                )
        try:
            call = parse_smn(str(staged[".json"]))
        except (OSError, ValueError, StopIteration, AttributeError) as e:
            raise WGSExtractError(
                f"SMNCopyNumberCaller wrote an unreadable {prefix}.json: {e}"
            ) from e
        depth = call.median_depth
        withheld = depth is None or depth < MIN_DEPTH
        # Below the minimum depth the caller's files are kept only as raw
        # diagnostics, under names that don't present them as calls.
        name = f"{prefix}.below_min_depth" if withheld else prefix
        for stale in (prefix, f"{prefix}.below_min_depth"):
            for suffix in staged:
                Path(aln.outdir, stale + suffix).unlink(missing_ok=True)
        for suffix, path in staged.items():
            shutil.move(str(path), str(Path(aln.outdir, name + suffix)))

    result = current_result()
    result.set_reference(aln.reference)
    if version := caller_version(caller):
        result.record_tool_version("SMNCopyNumberCaller", version)
    if withheld:
        result.add_output(Path(aln.outdir, name + ".tsv"), "smn_raw_below_min_depth")
        result.add_output(Path(aln.outdir, name + ".json"), "smn_raw_below_min_depth")
    else:
        result.add_output(Path(aln.outdir, name + ".tsv"), "smn_calls")
        result.add_output(Path(aln.outdir, name + ".json"), "smn_report")
    result.add_metrics(
        smn1_copy_number=None if withheld else call.smn1,
        smn2_copy_number=None if withheld else call.smn2,
        smn2_delta78_copy_number=None if withheld else call.smn2_delta78,
        sma_carrier=None if withheld else call.is_carrier,
        sma_affected=None if withheld else call.is_sma,
        g27134tg_copy_number=None if withheld else call.g27134tg,
        smn_filter=call.info,
        median_depth=depth,
        coverage_mad=call.coverage_mad,
    )
    depth_text = f"{depth}x" if depth is not None else "unknown"
    if withheld:
        warn(
            "low_coverage",
            f"Median depth {depth_text} is below {MIN_DEPTH:g}x; SMNCopyNumberCaller "
            f"needs at least {MIN_DEPTH:g}x WGS, so no SMN calls are reported (its "
            f"raw output: {name}.json).",
            gene="SMN1",
            depth=depth,
            threshold=MIN_DEPTH,
        )
        logging.info(NOT_DIAGNOSIS)
        return
    logging.info(
        f"SMN1: {_cn(call.smn1)}, SMN2: {_cn(call.smn2)}, SMN2Δ7–8: "
        f"{_cn(call.smn2_delta78)} ({call.info}; median depth {depth_text}). "
        f"SMA carrier: {_status(call.is_carrier)}; SMA affected: "
        f"{_status(call.is_sma)}."
    )
    if aligner and aligner not in CALIBRATED_ALIGNERS:
        warn(
            "aligner_not_calibrated",
            f"The input was aligned with {aligner}; SMNCopyNumberCaller is "
            "calibrated on bwa-like alignments (BWA, DRAGEN, Isaac), and other "
            "aligners place SMN1/SMN2 reads differently, which can shift the "
            "copy numbers. Realign with bwa-mem2 for a reliable call.",
            gene="SMN1",
            aligner=aligner,
        )
    if call.coverage_mad is not None and call.coverage_mad > MAX_COVERAGE_MAD:
        warn(
            "uneven_coverage",
            f"Coverage is uneven (MAD {call.coverage_mad} > {MAX_COVERAGE_MAD}); "
            "the copy-number calls may be unreliable.",
            gene="SMN1",
            coverage_mad=call.coverage_mad,
        )
    if call.smn1 is None:
        warn(
            "no_call",
            f"SMNCopyNumberCaller made no SMN1 call ({call.info}).",
            gene="SMN1",
            filter=call.info,
        )
    elif call.is_carrier is None or call.is_sma is None:
        warn(
            "status_uncertain",
            "SMNCopyNumberCaller couldn't settle the carrier/affected status "
            "from the SMN1 splice-site reads.",
            gene="SMN1",
        )
    if call.smn1 == 2 and call.g27134tg:
        warn(
            "possible_silent_carrier",
            f"Two SMN1 copies with {call.g27134tg} copy(ies) of g.27134T>G, which "
            "is associated with both SMN1 copies being on one chromosome (a 2+0 "
            "silent carrier); copy number alone can't tell.",
            gene="SMN1",
            g27134tg_copy_number=call.g27134tg,
        )
    elif call.smn1 == 2:
        logging.info(
            "Two SMN1 copies lowers, but doesn't rule out, carrier status: both "
            "can be on one chromosome (2+0), which copy number can't show."
        )
    logging.info(NOT_DIAGNOSIS)
    logging.info(f"SMNCopyNumberCaller results: {Path(aln.outdir, prefix + '.tsv')}")


def _cn(value: object) -> str:
    if value is None:
        return "no call"
    return f"{value} cop{'y' if value == 1 else 'ies'}"


def _status(value: bool | None) -> str:
    return "uncertain" if value is None else "yes" if value else "no"


cmd_smn = cli_handler(TypingSmnParams, run_smn)
