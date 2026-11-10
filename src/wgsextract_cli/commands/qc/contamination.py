"""`qc contamination`: DNA from another person in a BAM/CRAM, with VerifyBamID2.

VerifyBamID2 (Zhang et al. 2020, Genome Research) fits the reads' allele
counts at common SNPs to a mixture of two people whose allele frequencies come
from a reference panel's principal components, so it needs no genotypes of the
sample and works across ancestries. Its FREEMIX is the estimated fraction of
the reads from the second person. The 1000 Genomes phase 3 panels its Bioconda
package ships cover GRCh37 and GRCh38; other builds (T2T) need a panel of their
own (``--svd-prefix``). See docs/contamination.md.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.qc import QcContaminationParams
from wgsextract_cli.core import contamination, sample_workspace
from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.pixi_tools import pixi_env_prefix
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command

from ..genotyping.common import Alignment, env_tool, open_alignment

TOOL = ENV = "verifybamid2"
PANEL_FILES = (".UD", ".mu", ".bed")
# Covered panel markers below which an estimate is only a rough guide; the
# bundled panels have 100,000 (or 10,000) markers genome-wide.
MIN_CONFIDENT_MARKERS = 5000
MIN_CONFIDENT_DEPTH = 4.0
_MARKERS = re.compile(r"Total Number Markers:\s*(\d+)")
_VERSION = re.compile(r"Version:\s*(\S+)")
_INSUFFICIENT = "Insufficient Available markers"


@dataclass(frozen=True)
class Estimate:
    freemix: float
    reads: int
    depth: float
    markers: int | None
    pcs: dict[str, float]


def _bundled_resource_dir(command: list[str]) -> Path | None:
    """The `resource` folder of the VerifyBamID2 package that runs."""
    candidates = []
    if command and Path(command[0]).is_absolute() and Path(command[0]).is_file():
        candidates.append(Path(command[0]).resolve().parent / "resource")
    prefix = pixi_env_prefix(ENV)
    if prefix is not None:
        candidates += sorted(prefix.glob("share/verifybamid2-*/resource"), reverse=True)
    return next((c for c in candidates if c.is_dir()), None)


def _panel_prefix(
    params: QcContaminationParams, aln: Alignment, command: list[str]
) -> str:
    if params.svd_prefix:
        prefix = expand_path(params.svd_prefix)
        missing = [s for s in PANEL_FILES if not Path(prefix + s).is_file()]
        if missing:
            raise UsageError(
                f"--svd-prefix {prefix}: {', '.join(prefix + s for s in missing)} "
                "not found (VerifyBamID2 needs .UD, .mu and .bed)."
            )
        return prefix
    folder = _bundled_resource_dir(command)
    build = {"hg19": "b37", "hg38": "b38"}[aln.build]
    prefix = str(Path(folder or "", f"1000g.phase3.{params.sites}.{build}.vcf.gz.dat"))
    if folder is None or not all(Path(prefix + s).is_file() for s in PANEL_FILES):
        raise WGSExtractError(
            "VerifyBamID2's 1000 Genomes resources weren't found; reinstall its "
            "environment (pixi install -e verifybamid2) or pass --svd-prefix."
        )
    return prefix


def _other_naming(name: str) -> str:
    """chr20 as 20, and 20 as chr20."""
    return name[3:] if name.startswith("chr") else f"chr{name}"


def _matching_panel(prefix: str, aln: Alignment, work: str) -> str:
    """The panel with its contigs named as the alignment's (chr20 or 20): the
    1000 Genomes GRCh37 panel names them 20, the GRCh38 one chr20. Decided by
    the contigs both have, so a one-chromosome alignment matches too."""
    with open(prefix + ".bed") as f:
        contigs = {line.split("\t", 1)[0] for line in f if line.strip()}
    if contigs & aln.lengths.keys():
        return prefix
    renamed = {c: _other_naming(c) for c in contigs}
    if not set(renamed.values()) & aln.lengths.keys():
        raise WGSExtractError(
            "None of the contigs of the VerifyBamID2 panel are in the input "
            f"(panel: {', '.join(sorted(contigs)[:3])}...)."
        )
    local = str(Path(work, Path(prefix).name))
    with open(prefix + ".bed") as src, open(local + ".bed", "w", newline="\n") as dst:
        for line in src:
            contig, sep, rest = line.partition("\t")
            dst.write(renamed.get(contig, contig) + sep + rest)
    with open(prefix + ".mu") as src, open(local + ".mu", "w", newline="\n") as dst:
        for line in src:
            contig, sep, rest = line.partition(":")
            dst.write(renamed.get(contig, contig) + sep + rest)
    for suffix in (".UD", ".V"):
        if Path(prefix + suffix).is_file():
            shutil.copyfile(prefix + suffix, local + suffix)
    return local


def _require_one_sample(path: str, reference: str | None) -> None:
    header = get_bam_header(path, reference) or ""
    samples = set(re.findall(r"^@RG\t(?:.*\t)?SM:([^\t\n]+)", header, re.MULTILINE))
    if len(samples) > 1:
        raise WGSExtractError(
            "qc contamination needs a single-sample alignment; this one has read "
            f"groups of {len(samples)} samples ({', '.join(sorted(samples))})."
        )


def parse_self_sm(path: str) -> tuple[float, int, float]:
    """(FREEMIX, #READS, AVG_DP) from a VerifyBamID2 .selfSM file."""
    with open(path) as f:
        header = f.readline().rstrip("\n").split("\t")
        row = dict(zip(header, f.readline().rstrip("\n").split("\t"), strict=False))
    try:
        return float(row["FREEMIX"]), int(row["#READS"]), float(row["AVG_DP"])
    except (KeyError, ValueError) as e:
        raise WGSExtractError(f"Unreadable VerifyBamID2 report {path}: {e}") from e


def parse_ancestry(path: str) -> dict[str, float]:
    """The PCs of the intended sample from a .Ancestry file (pc1, pc2, ...)."""
    pcs: dict[str, float] = {}
    with open(path) as f:
        next(f, None)
        for line in f:
            fields = line.split()
            if len(fields) >= 3:
                pcs[f"pc{fields[0]}"] = float(fields[2])
    return pcs


def _run(command: list[str], args: list[str], sanity_check: bool) -> tuple[int, str]:
    full = [*command, *args, *([] if sanity_check else ["--DisableSanityCheck"])]
    done = run_command(full, capture_output=True, check=False)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _covered_markers(log: str) -> int | None:
    """Panel sites with reads, as VerifyBamID2's pileup counts them."""
    found = _MARKERS.search(log)
    return int(found[1]) if found else None


def _estimate(
    command: list[str], aln: Alignment, panel: str, work: str
) -> tuple[Estimate, str, bool]:
    """Run VerifyBamID2; returns the estimate, its log and whether its marker
    sanity check had to be disabled (too few markers covered)."""
    output = str(Path(work, aln.stem))
    args = ["--SVDPrefix", panel, "--Reference", aln.require_reference(),
            "--BamFile", aln.path, "--Output", output, "--NumThread", aln.threads]  # fmt: skip
    status, log = _run(command, args, sanity_check=True)
    relaxed = status != 0 and _INSUFFICIENT in log
    if relaxed and _covered_markers(log) == 0:
        # Without the check VerifyBamID2 would still report a FREEMIX, from
        # no reads at all (an mtDNA-only extract, say).
        raise WGSExtractError(
            f"{Path(aln.path).name} has no reads at any of the panel's sites "
            "(is it a subset without the autosomes?); contamination can't be "
            "estimated from it."
        )
    if relaxed:
        # A region subset or low coverage covers too few markers for the
        # check; the estimate is still made, with a warning.
        status, log = _run(command, args, sanity_check=False)
    if status != 0 or not Path(output + ".selfSM").is_file():
        tail = "\n".join(line for line in log.splitlines() if "Process " not in line)
        raise WGSExtractError(
            f"VerifyBamID2 failed (exit {status}): {tail[-1500:].strip()}"
        )
    freemix, reads, depth = parse_self_sm(output + ".selfSM")
    covered = _covered_markers(log)
    if covered == 0 or reads == 0 or not math.isfinite(freemix + depth):
        raise WGSExtractError(
            f"VerifyBamID2 found no reads at the panel's sites in "
            f"{Path(aln.path).name}; contamination can't be estimated from it."
        )
    pcs = (
        parse_ancestry(output + ".Ancestry")
        if Path(output + ".Ancestry").is_file()
        else {}
    )
    return (
        Estimate(freemix, reads, depth, covered, pcs),
        log,
        relaxed,
    )


def _confidence(estimate: Estimate, relaxed: bool) -> sample_workspace.Confidence:
    if relaxed or (estimate.markers or 0) < 1000:
        return "low"
    if (
        estimate.markers or 0
    ) < MIN_CONFIDENT_MARKERS or estimate.depth < MIN_CONFIDENT_DEPTH:
        return "medium"
    return "high"


def run_contamination(params: QcContaminationParams) -> None:
    aln = open_alignment(params, purpose="qc contamination", any_build=True)
    if aln.build not in ("hg19", "hg38") and not params.svd_prefix:
        build = "T2T" if aln.build == "t2t" else (aln.build or "an unknown build")
        raise WGSExtractError(
            f"{Path(aln.path).name} is aligned to {build}; VerifyBamID2's bundled "
            "1000 Genomes resources cover GRCh37/hg19 and GRCh38 only. Pass "
            "--svd-prefix with a panel built for this build (see "
            f"{contamination.GUIDE})."
        )
    aln.require_reference()
    _require_one_sample(aln.path, aln.reference)
    command = env_tool(TOOL, ENV)
    prefix = _panel_prefix(params, aln, command)

    result = current_result()
    result.add_input(aln.path, "input")
    result.set_reference(aln.reference)
    stem = f"{aln.stem}.contamination"
    report = Path(aln.outdir, stem + ".selfSM")
    ancestry = Path(aln.outdir, stem + ".Ancestry")
    with tempfile.TemporaryDirectory(dir=aln.outdir, prefix=".wgse_contam_") as work:
        panel = _matching_panel(prefix, aln, work)
        logging.info(
            f"Estimating contamination with VerifyBamID2 ({Path(prefix).name})..."
        )
        estimate, log, relaxed = _estimate(command, aln, panel, work)
        if version := _VERSION.search(log):
            result.record_tool_version(TOOL, version[1])
        for source, target in ((Path(work, aln.stem + ".selfSM"), report),
                               (Path(work, aln.stem + ".Ancestry"), ancestry)):  # fmt: skip
            if source.is_file():
                with atomic_output(target) as staged:
                    shutil.copyfile(source, staged)

    result.set_manifest_stem(Path(aln.outdir, stem))
    result.add_output(report, "contamination_report", kind="tsv")
    if ancestry.is_file():
        result.add_output(ancestry, "contamination_ancestry", kind="tsv")
    confidence = _confidence(estimate, relaxed)
    result.add_metrics(
        freemix=round(estimate.freemix, 5),
        freemix_sites=estimate.markers,
        freemix_reads=estimate.reads,
        freemix_depth=round(estimate.depth, 2),
        freemix_confidence=confidence,
        svd_panel=Path(prefix).name,
        **{f"ancestry_{k}": round(v, 5) for k, v in estimate.pcs.items()},
    )
    sites = f"{estimate.markers:,} panel sites" if estimate.markers else "panel sites"
    logging.info(
        f"Contamination (FREEMIX): {estimate.freemix:.2%} from {sites} at a mean "
        f"depth of {estimate.depth:.1f}x ({confidence} confidence)"
    )
    sample_workspace.record(
        contamination.FACT,
        aln.path,
        round(estimate.freemix, 5),
        method=f"VerifyBamID2 FREEMIX, {Path(prefix).name}, {sites}",
        confidence=confidence,
    )
    if relaxed:
        warn(
            "contamination_few_sites",
            f"Only {sites} have reads, too few for VerifyBamID2's marker check "
            "(a region subset or very low coverage); the estimate is rough.",
            sites=estimate.markers,
        )
    threshold = contamination.max_contamination()
    if threshold and estimate.freemix >= threshold:
        warn(
            contamination.WARNING,
            f"About {estimate.freemix:.1%} of the reads in {Path(aln.path).name} "
            f"look like another person's DNA (FREEMIX, above qc_max_contamination "
            f"{threshold:.1%}). Genotypes, microarray exports and reports from it "
            f"may be wrong. See {contamination.GUIDE}.",
            freemix=round(estimate.freemix, 4),
            threshold=threshold,
        )


cmd_contamination = cli_handler(QcContaminationParams, run_contamination)
