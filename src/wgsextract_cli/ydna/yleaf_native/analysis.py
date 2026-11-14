# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from genid/Yleaf v4.1.6, yleaf/Yleaf.py and predict_haplogroup.py.
# Copyright Diego Montiel Gonzalez and Bram van Wersch, Erasmus MC.
# Modified 2026-11-01 by WGS Extract contributors: native API, argv subprocesses only
# for existing HTS tools; versioned result provenance. See provenance.json.
# Modified 2026-11-05: versioned trees (trees.py) and their provenance.
# Modified 2026-11-06: trees come from the registry, including custom trees.
"""Run native Yleaf analysis on a single-sample VCF or indexed BAM/CRAM."""

import csv
import json
import logging
import math
import subprocess
import tempfile
from bisect import bisect_right
from collections import Counter
from dataclasses import replace
from pathlib import Path

from wgsextract_cli.core.results import warn
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_files import vcf_index_chromosomes

from .data import file_sha256, manifest
from .markers import (
    Call,
    ad_call,
    gt_call,
    match_markers,
    pileup_call,
    read_markers,
    write_marker_tables,
)
from .predict import (
    PREDICTION_HEADER,
    Predictor,
    link_markers,
    prediction_row,
)
from .trees import YleafTree, ensure_tree

CREDIT = (
    "Native Yleaf (Erasmus MC; Ralf et al., Mol Biol Evol 2018, "
    "doi:10.1093/molbev/msy032)"
)
Y_NAMES = ("Y", "chrY")


def _query_vcf(
    path: Path,
    *,
    positions: set[int] | None = None,
    reference_sequence: str | None = None,
    min_quality: int = 20,
) -> tuple[dict[int, Call], bool]:
    samples = run_command(
        ["bcftools", "query", "-l", str(path)], capture_output=True
    ).stdout.splitlines()
    if len(samples) != 1:
        raise WGSExtractError("Y haplogroup analysis requires a single-sample VCF.")
    header = run_command(
        ["bcftools", "view", "-h", str(path)], capture_output=True
    ).stdout
    has_ad = "##FORMAT=<ID=AD," in header
    fields = [
        f"%{field}" if f"##FORMAT=<ID={field}," in header else "."
        for field in ("GT", "AD", "DP", "GQ", "MIN_DP")
    ]
    block_depth_field = "%INFO/MIN_DP" if "##INFO=<ID=MIN_DP," in header else "."
    region_args: list[str] = []
    if any(Path(str(path) + suffix).is_file() for suffix in (".tbi", ".csi")):
        contigs = vcf_index_chromosomes(path)
        if contigs:
            regions = [name for name in contigs if name in Y_NAMES]
            if not regions:
                raise WGSExtractError("Input VCF has no records on chromosome Y.")
            region_args = ["-r", ",".join(regions)]
    text = run_command(
        [
            "bcftools",
            "query",
            *region_args,
            "-i",
            'CHROM="Y" || CHROM="chrY"',
            "-f",
            "%CHROM\\t%POS\\t%END\\t%REF\\t%ALT\\t%FILTER\\t"
            + block_depth_field
            + "[\\t"
            + "\\t".join(fields)
            + "]\\n",
            str(path),
        ],
        capture_output=True,
    ).stdout
    if not text.strip():
        raise WGSExtractError("Input VCF has no records on chromosome Y.")
    calls = {}
    seen = set()
    marker_positions = sorted(positions or ())
    for line in text.splitlines():
        (
            _,
            position,
            end,
            ref,
            alt,
            site_filter,
            info_min_dp,
            gt,
            ad,
            dp,
            gq,
            min_dp,
        ) = line.split("\t")
        pos = int(position)
        if pos in seen:
            continue
        seen.add(pos)
        genotype = gt_call(ref, alt, gt)
        quality = float(gq) if gq != "." else None
        reference_block = (
            int(end) > pos
            and len(ref) == 1
            and alt in (".", "<NON_REF>", "<*>")
            and genotype is not None
            and genotype.base == ref
        )
        depth_metrics = [
            int(value)
            for value in (min_dp, dp, info_min_dp if reference_block else ".")
            if value != "."
        ]
        reads = min(depth_metrics) if depth_metrics else 0
        description = ""
        if site_filter != "PASS":
            description = "Filtered or unassessed variant"
        elif "." in gt.replace("|", "/").split("/") or gt == "":
            description = "Missing genotype"
        elif genotype is None:
            description = "Ambiguous or unsupported Y genotype"
        elif quality is not None and (
            not math.isfinite(quality) or quality < min_quality
        ):
            description = "Below genotype quality"
        call = Call(
            genotype.base if genotype else "NA",
            reads,
            100.0,
            description,
            gt,
            site_filter,
            quality,
        )
        if not description and ad != ".":
            evidence = ad_call(ref, alt, ad)
            call = replace(
                call,
                reads=min(reads, evidence.reads) if depth_metrics else evidence.reads,
                percentage=evidence.percentage,
            )
            if genotype and evidence.base != genotype.base:
                call = replace(call, description="Genotype and allele depths disagree")
        elif not description and not depth_metrics:
            call = replace(call, description="Missing depth evidence")
        calls[pos] = call
        # A FASTA supplies bases, not callability. Only an explicit reference
        # block can prove that an otherwise absent marker was assessed.
        if reference_sequence and positions and reference_block:
            minimums = [int(value) for value in (min_dp, info_min_dp) if value != "."]
            if not minimums:
                continue
            # DP/AD at the block's first position (or an average DP) does not
            # prove depth at any interior marker. MIN_DP is a block-wide floor.
            block_call = replace(call, reads=min(call.reads, *minimums))
            first = bisect_right(marker_positions, pos)
            last = bisect_right(
                marker_positions, min(int(end), len(reference_sequence))
            )
            for marker_pos in marker_positions[first:last]:
                calls.setdefault(
                    marker_pos,
                    replace(block_call, base=reference_sequence[marker_pos - 1]),
                )
    return calls, has_ad


def _reference_sequence(reference: Path) -> str:
    if not Path(str(reference) + ".fai").exists():
        run_command(["samtools", "faidx", str(reference)])
    contigs = {
        line.split("\t")[0]
        for line in Path(str(reference) + ".fai").read_text().splitlines()
    }
    name = next((name for name in Y_NAMES if name in contigs), None)
    if not name:
        raise WGSExtractError("The reference FASTA has no Y chromosome.")
    text = run_command(
        ["samtools", "faidx", str(reference), name], capture_output=True
    ).stdout
    return "".join(text.splitlines()[1:]).upper()


def _alignment_calls(
    path: Path,
    positions: set[int],
    work: Path,
    reference: Path | None,
    quality: int,
) -> tuple[dict[int, Call], str]:
    stats = run_command(["samtools", "idxstats", str(path)], capture_output=True).stdout
    rows = [line.split("\t") for line in stats.splitlines()]
    name = next((name for name in Y_NAMES if any(r[0] == name for r in rows)), None)
    if not name:
        raise WGSExtractError("Input alignment has no Y chromosome.")
    total_reads = str(sum(int(row[2]) for row in rows))
    bed = work / "positions.txt"
    bed.write_text(
        "".join(f"{name}\t{pos}\n" for pos in sorted(positions)),
        encoding="utf-8",
        newline="\n",
    )
    pileup = work / "chrY.pileup"
    command = [
        "samtools",
        "mpileup",
        "-l",
        str(bed),
        "-r",
        name,
        f"-ABQ{quality}q1",
        "-B",
    ]
    if reference and path.suffix.lower() == ".cram":
        command += ["-f", str(reference)]
    command.append(str(path))
    with pileup.open("w", encoding="utf-8", newline="\n") as stream:
        run_command(command, stdout=stream)
    calls = {}
    with pileup.open(encoding="utf-8") as stream:
        for line in stream:
            _, pos, ref, reads, sequence, *_ = line.rstrip("\n").split("\t")
            calls[int(pos)] = pileup_call(ref, int(reads), sequence)
    return calls, total_reads


def analyze(
    input_path: Path,
    output: Path,
    *,
    build: str,
    tree: str | YleafTree = "yfull",
    reference: Path | None = None,
    library: Path | None = None,
    pos_file: Path | None = None,
    min_reads: int = 10,
    quality: int = 20,
    majority: int = 90,
    min_score: float = 0.95,
    ancient: bool = False,
    no_ref_inference: bool = False,
) -> Path:
    """Write .out/.fmf/.info, hg_prediction.hg and run.json; never delete user data.

    ``tree`` is a registry id (built-in or config.toml ``[y_trees]``) or an
    already resolved tree, such as a ``--tree-file`` one.
    """
    if (
        min_reads < 1
        or not 10 <= quality <= 40
        or not 50 <= majority <= 99
        or not 0 <= min_score <= 1
    ):
        raise WGSExtractError(
            "Invalid Yleaf read/quality/majority/prediction threshold."
        )
    is_vcf = input_path.name.lower().endswith((".vcf", ".vcf.gz", ".bcf"))
    if not is_vcf and input_path.suffix.lower() not in (".bam", ".cram"):
        raise WGSExtractError(
            "Native Yleaf accepts BAM, CRAM or single-sample VCF/BCF."
        )
    if (
        is_vcf and not no_ref_inference or input_path.suffix.lower() == ".cram"
    ) and not reference:
        raise WGSExtractError(
            "A reference FASTA (--ref) is required for CRAM decoding and VCF "
            "reference inference. For targeted VCF panels use --no-ref-inference."
        )
    output.mkdir(parents=True, exist_ok=True)
    prediction = output / "hg_prediction.hg"
    prediction.unlink(missing_ok=True)
    metadata = manifest()
    version = metadata["upstream"]["tag"]
    selected = (
        tree
        if isinstance(tree, YleafTree)
        else ensure_tree(
            tree,
            build,
            library,
            None if pos_file else ("ancient" if ancient else "regular"),
        )
    )
    logging.info(
        "%s; tables %s; build %s; tree %s", CREDIT, version, build, selected.label
    )
    marker_file = pos_file or selected.positions(build, ancient)
    panel_source = "custom" if pos_file else selected.origin
    markers = read_markers(marker_file)
    predictor = Predictor(selected.tree_file, selected.qc)
    sample = input_path.name
    for suffix in (".vcf.gz", ".vcf", ".bcf", ".bam", ".cram"):
        if sample.lower().endswith(suffix):
            sample = sample[: -len(suffix)]
            break
    folder = output / sample
    folder.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="work-", dir=output) as temp:
            reference_sequence = None
            if is_vcf:
                if not no_ref_inference:
                    assert reference is not None
                    reference_sequence = _reference_sequence(reference)
                calls, _ = _query_vcf(
                    input_path,
                    positions={m.pos for m in markers},
                    reference_sequence=reference_sequence,
                    min_quality=quality,
                )
                total_reads = "VCF"
            else:
                calls, total_reads = _alignment_calls(
                    input_path, {m.pos for m in markers}, Path(temp), reference, quality
                )
            passing, failed = match_markers(
                markers,
                calls,
                min_reads=min_reads,
                majority=majority,
            )
        ordered = write_marker_tables(folder, sample, predictor.tree, passing, failed)
        haplotypes = link_markers(
            (str(o.marker.pos), o.marker.name, o.marker.haplogroup, o.state)
            for o in ordered
        )
        scores = predictor.predict(haplotypes, min_score)
        counts = Counter(o.description for o in failed)
        if is_vcf and failed:
            warn(
                "y_evidence_rejected",
                f"{len(failed)} Y markers are unknown or failed evidence QC; see {folder / (sample + '.fmf')}.",
                counts=json.dumps(counts, sort_keys=True),
            )
        (folder / f"{sample}.info").write_text(
            f"Total of mapped reads: {total_reads}\n"
            f"Markers with haplogroup information: {len(passing)}\n"
            f"Markers without haplogroup information: {len(failed)}\n"
            + "".join(f"{key}: {value}\n" for key, value in counts.items()),
            encoding="utf-8",
            newline="\n",
        )
        with prediction.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerow(PREDICTION_HEADER)
            writer.writerow(prediction_row(sample, haplotypes, scores, total_reads))
        (output / "run.json").write_text(
            json.dumps(
                {
                    "algorithm": "Yleaf",
                    "upstream": metadata["upstream"],
                    "data_release": metadata["release_tag"],
                    "build": build,
                    "tree": selected.provenance(),
                    "ancient": ancient,
                    "marker_panel": {
                        "source": panel_source,
                        "identifier": marker_file.name
                        if pos_file
                        else selected.identifier(marker_file),
                        "sha256": file_sha256(marker_file),
                    },
                    "citation": CREDIT,
                    "min_reads": min_reads,
                    "base_quality": quality,
                    "base_majority": majority,
                    "min_score": min_score,
                    "reference_inference": is_vcf and not no_ref_inference,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        prediction.unlink(missing_ok=True)
        raise WGSExtractError(f"Native Yleaf analysis failed: {error}") from error
    return prediction
