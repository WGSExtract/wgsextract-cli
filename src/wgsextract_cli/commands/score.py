"""Research-only polygenic scoring."""

import argparse
import csv
import json
import tempfile
from pathlib import Path

from wgsextract_cli.api._cli import add_arguments, cli_handler
from wgsextract_cli.api._runtime import report
from wgsextract_cli.api.score import ScorePgsParams
from wgsextract_cli.core import pgs, pgs_plink
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.utils import UsageError


def register(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "score", help="Local research-only polygenic scores."
    )
    subs = parser.add_subparsers(dest="score_cmd", required=True)
    sub = subs.add_parser(
        "pgs",
        parents=[base_parser],
        help="Score an additive PGS Catalog or custom file locally.",
    )
    add_arguments(sub, ScorePgsParams)
    sub.set_defaults(func=cli_handler(ScorePgsParams, run_pgs))


def run_pgs(params: ScorePgsParams) -> None:
    verify_dependencies(["bcftools", *(["plink2"] if params.plink2_check else [])])
    outdir = (params.outdir or params.input.parent).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    selected = params.pgs_id
    if params.trait:
        if params.trait not in pgs.CURATED:
            raise UsageError(
                f"Unknown trait {params.trait!r}. Choose: {', '.join(pgs.CURATED)}"
            )
        selected = pgs.CURATED[params.trait]
    if not params.scoring_file and not selected:
        raise UsageError("Choose --trait, --pgs-id or --scoring-file.")
    warn("pgs_research_only", pgs.DISCLAIMER)
    if not params.scoring_file and selected not in pgs.CURATED.values():
        warn(
            "pgs_uncurated_score",
            "This PGS ID is not in the curated list; review its publication and licence.",
        )
    report("pgs", "Reading scoring file")
    path = params.scoring_file or pgs.catalog_file(
        str(selected), params.build, outdir / "pgs_catalog"
    )
    scoring = pgs.ScoringFile(
        path,
        params.build,
        catalog_metadata=path.parent / f"{selected}.json"
        if not params.scoring_file
        else None,
    )
    if not params.scoring_file and scoring.metadata.get("pgs_id") != selected:
        raise UsageError("Downloaded scoring file does not match the requested PGS ID.")
    confident = pgs.ConfidentBed(params.confident_bed) if params.confident_bed else None
    reference = pgs.Reference(params.ref, params.build) if params.ref else None
    result = current_result()
    result.add_input(params.input, "vcf")
    result.add_input(path, "pgs_scoring_file")
    if params.confident_bed:
        result.add_input(params.confident_bed, "confident_bed")
    if params.ref:
        result.add_input(params.ref, "reference")
    report("pgs", "Indexing observed calls on disk")
    with tempfile.TemporaryDirectory(prefix=".pgs-", dir=outdir) as work:
        matched_file = Path(work) / "matched.tsv" if params.plink2_check else None
        genotypes = pgs.Genotypes(
            params.input, Path(work) / "calls.sqlite", params.sample, params.build
        )
        try:
            report("pgs", "Matching alleles and summing effect dosages")
            data = pgs.score(
                scoring,
                genotypes,
                confident=confident,
                reference=reference,
                min_match=params.min_match if params.min_match is not None else 0.75,
                matched_file=matched_file,
                keep_ambiguous=bool(params.keep_ambiguous),
            )
            if matched_file is not None and data["score_sum"] is not None:
                report("pgs", "Cross-checking matched calls with PLINK 2")
                data["plink2_crosscheck"] = pgs_plink.crosscheck(
                    matched_file, Path(work), data["score_sum"]
                )
        finally:
            genotypes.close()
            if reference is not None:
                reference.close()
    if data["match_rate"] < data["minimum_match_rate"]:
        warn(
            "pgs_low_match",
            "Too few scoring variants matched; percentile refused.",
            match_rate=data["match_rate"],
            minimum=data["minimum_match_rate"],
        )
    warn(
        "pgs_uncalibrated",
        "No ancestry-calibrated percentile: score-specific reference distributions are not yet available.",
    )
    if data["match_counts"].get("unsupported"):
        warn(
            "pgs_unsupported_variants",
            "Non-autosomal, non-SNV or unresolved-allele rows are unmatched.",
            variants=data["match_counts"]["unsupported"],
        )
    if params.keep_ambiguous:
        warn(
            "pgs_ambiguous_strand_assumed",
            "Palindromic SNPs retained: correctness depends on independently verified forward-strand scoring alleles.",
        )
    elif data["match_counts"].get("strand_ambiguous"):
        warn(
            "pgs_ambiguous_variants",
            "A/T and C/G SNPs are unmatched because their strand is ambiguous.",
            variants=data["match_counts"]["strand_ambiguous"],
        )
    data["build"] = params.build
    result.details = data
    result.add_metrics(
        score_sum=data["score_sum"],
        match_rate=data["match_rate"],
        variants_matched=data["variants_matched"],
        variants_total=data["variants_total"],
        percentile=None,
    )
    stem = f"{params.input.name.removesuffix('.gz').removesuffix('.vcf')}_{data['pgs_id']}_pgs"
    output = outdir / f"{stem}.json"
    with atomic_output(output, retire_indexes=False) as staged:
        Path(staged).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    result.add_output(output, "pgs")
    table = outdir / f"{stem}.tsv"
    with atomic_output(table, retire_indexes=False) as staged:
        with open(staged, "w", newline="") as stream:
            columns = [
                "pgs_id",
                "trait",
                "score_sum",
                "variants_matched",
                "variants_total",
                "match_rate",
                "percentile",
                "confidence",
                "license",
                "citation",
                "disclaimer",
            ]
            writer = csv.DictWriter(
                stream, fieldnames=columns, delimiter="\t", extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerow(data)
    result.add_output(table, "pgs_table")
    human_output(pgs.DISCLAIMER)
    human_output(
        f"{data['pgs_id']} ({data['trait']}): sum={data['score_sum']}; matched {data['variants_matched']}/{data['variants_total']} ({data['match_rate']:.1%}). Percentile unavailable."
    )
    human_output(f"Licence: {data['license']}\nCitation: {data['citation']}")
