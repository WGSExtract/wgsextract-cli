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
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.results import warn
from wgsextract_cli.core.utils import WGSExtractError, run_command

from .data import file_sha256, manifest
from .markers import (
    Call,
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
from .vcf_calls import query_vcf as _query_vcf

CREDIT = (
    "Native Yleaf (Erasmus MC; Ralf et al., Mol Biol Evol 2018, "
    "doi:10.1093/molbev/msy032)"
)
Y_NAMES = ("Y", "chrY")


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
    lengths = sequence_lengths(
        get_bam_header(str(path), str(reference) if reference else None)
    )
    name = next((name for name in Y_NAMES if name in lengths), None)
    if not name:
        raise WGSExtractError("Input alignment has no Y chromosome.")
    total_reads = "not assessed (regional Y query)"
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
    assume_reference: bool = False,
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
    if assume_reference and (no_ref_inference or not is_vcf):
        raise WGSExtractError(
            "--assume-reference requires VCF input and cannot be combined with "
            "--no-ref-inference."
        )
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
    polarity = None
    if not pos_file and selected.polarity_file is not None:
        from .polarity import correct_polarity

        regular_file = selected.positions(build)
        filter_damage = ancient and marker_file != regular_file
        marker_file = regular_file
        markers = read_markers(marker_file)
        markers, polarity = correct_polarity(
            markers,
            marker_file,
            selected.polarity_file,
            selected.id,
            build,
            ancient=filter_damage,
        )
        logging.info(
            "Y marker polarity: %s independently verified reversals corrected",
            polarity["corrected"],
        )
    else:
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
                calls, has_reference_evidence = _query_vcf(
                    input_path,
                    positions={m.pos for m in markers},
                    reference_sequence=reference_sequence,
                    min_quality=quality,
                    min_reads=min_reads,
                    majority=majority,
                    work=Path(temp),
                )
                if not has_reference_evidence:
                    warn(
                        "y_reference_evidence_missing",
                        "No callable reference evidence was found at the Y markers. Results may be "
                        "incomplete; use a gVCF or all-sites VCF. Missing sites "
                        "are not confirmed callable.",
                    )
                if assume_reference:
                    assert reference_sequence is not None
                    warn(
                        "y_reference_assumed",
                        "Assuming the reference at absent Y markers for upstream "
                        "Yleaf compatibility; this can bias the haplogroup.",
                    )
                    for marker in markers:
                        if marker.pos <= len(reference_sequence):
                            calls.setdefault(
                                marker.pos,
                                Call(
                                    reference_sequence[marker.pos - 1],
                                    0,
                                    100.0,
                                    assumed_reference=True,
                                ),
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
                report_absence=is_vcf,
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
                        **({"polarity": polarity} if polarity is not None else {}),
                    },
                    "citation": CREDIT,
                    "min_reads": min_reads,
                    "base_quality": quality,
                    "base_majority": majority,
                    "min_score": min_score,
                    "reference_inference": is_vcf and not no_ref_inference,
                    "assume_reference": assume_reference,
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
