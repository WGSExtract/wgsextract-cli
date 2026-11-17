"""Research-only polygenic scoring."""

import csv
import json
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from wgsextract_cli.api._runtime import cancel_event, report
from wgsextract_cli.api.score import ScorePgsParams
from wgsextract_cli.cli.registration.score import register as register
from wgsextract_cli.core import (
    ancestry_reference,
    pgs,
    pgs_plink,
    pgs_reference,
    variant_confidence,
)
from wgsextract_cli.core import reference_evidence as evidence
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.reference_assets import input_stem
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command


def run_pgs(params: ScorePgsParams) -> None:
    if params.confident_bed and not params.ref:
        raise UsageError("--confident-bed requires an indexed --ref FASTA.")
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
    result = current_result()
    result.add_input(params.input, "vcf")
    result.add_input(path, "pgs_scoring_file")
    if params.confident_bed:
        result.add_input(params.confident_bed, "confident_bed")
    if params.ref:
        result.add_input(params.ref, "reference")
    calibration, reference_problem = _calibration(scoring, params)
    report("pgs", "Indexing observed calls on disk")
    with (
        tempfile.TemporaryDirectory(prefix=".pgs-", dir=outdir) as work,
        ExitStack() as handles,
    ):
        reference = pgs.Reference(params.ref, params.build) if params.ref else None
        if reference is not None:
            handles.callback(reference.close)
        matched_file = Path(work) / "matched.tsv" if params.plink2_check else None
        genotypes = pgs.Genotypes(
            params.input, Path(work) / "calls.sqlite", params.sample, params.build
        )
        handles.callback(genotypes.close)
        states = _reference_evidence(params, scoring, genotypes, reference, Path(work))
        report("pgs", "Matching alleles and summing effect dosages")
        data = pgs.score(
            scoring,
            genotypes,
            confident=confident,
            reference=reference,
            min_match=params.min_match if params.min_match is not None else 0.75,
            matched_file=matched_file,
            keep_ambiguous=bool(params.keep_ambiguous),
            calibration=calibration.accumulator if calibration else None,
            states=states[0] if states else None,
        )
        data["reference_evidence"] = states[0].describe(states[1]) if states else None
        if calibration is not None and data["match_rate"] >= data["minimum_match_rate"]:
            report("pgs", "Projecting the sample onto the ancestry PCA")
            calibration.project(params, genotypes.sample, Path(work), confident)
        if matched_file is not None and data["score_sum"] is not None:
            report("pgs", "Cross-checking matched calls with PLINK 2")
            data["plink2_crosscheck"] = pgs_plink.crosscheck(
                matched_file, Path(work), data["score_sum"]
            )
    if not params.scoring_file and pgs.author_license(data["license"]):
        warn(
            "pgs_license_restricted",
            f"{data['pgs_id']}'s authors set their own licence: {data['license']} "
            "Check that your use is allowed.",
            pgs_id=data["pgs_id"],
        )
    if data["match_rate"] < data["minimum_match_rate"]:
        warn(
            "pgs_low_match",
            "Too few scoring variants matched; percentile refused.",
            match_rate=data["match_rate"],
            minimum=data["minimum_match_rate"],
        )
    _calibrate(data, calibration, reference_problem)
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
        percentile=data["percentile"],
        z_adjusted=data["z_adjusted"],
    )
    stem = f"{input_stem(params.input)}_{data['pgs_id']}_pgs"
    output = outdir / f"{stem}.json"
    with atomic_output(output, retire_indexes=False) as staged:
        Path(staged).write_text(
            json.dumps(data, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    result.add_output(output, "pgs")
    table = outdir / f"{stem}.tsv"
    with atomic_output(table, retire_indexes=False) as staged:
        with open(staged, "w", newline="", encoding="utf-8") as stream:
            columns = [
                "pgs_id",
                "trait",
                "score_sum",
                "variants_matched",
                "variants_total",
                "match_rate",
                "percentile",
                "z_adjusted",
                "percentile_status",
                "confidence",
                "license",
                "citation",
                "disclaimer",
            ]
            writer = csv.DictWriter(
                stream,
                fieldnames=columns,
                delimiter="\t",
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerow(data)
    result.add_output(table, "pgs_table")
    human_output(pgs.DISCLAIMER)
    human_output(
        f"{data['pgs_id']} ({data['trait']}): sum={data['score_sum']}; matched {data['variants_matched']}/{data['variants_total']} ({data['match_rate']:.1%})."
    )
    if data["percentile"] is None:
        human_output(f"Percentile unavailable: {data['percentile_unavailable_reason']}")
    else:
        a = data["ancestry_calibration"]
        empirical = (
            f"; {a['empirical_percentile']:.0f}th percentile among the "
            f"{a['empirical_reference_samples']} most similar ({a['most_similar_population']}) reference samples"
            if a["empirical_percentile"] is not None
            else ""
        )
        human_output(
            f"Ancestry-adjusted percentile {data['percentile']:.0f} (Z {data['z_adjusted']:+.2f}, "
            f"95% interval for missing variants {a['percentile_interval'][0]:.0f}-"
            f"{a['percentile_interval'][1]:.0f}; confidence {data['confidence']}){empirical}. "
            "Relative to the HGDP+1KG "
            "reference, not an absolute risk."
        )
    human_output(f"Licence: {data['license']}\nCitation: {data['citation']}")


def _reference_evidence(
    params: ScorePgsParams,
    scoring: pgs.ScoringFile,
    genotypes: pgs.Genotypes,
    reference: pgs.Reference | None,
    work: Path,
) -> tuple[pgs.EvidenceStates, str] | None:
    """The reference evidence for the scored sites, and where it came from:
    the input itself when it is a gVCF, else the gVCF the caller published
    beside the input VCF (found through its result manifest)."""
    if genotypes.is_gvcf:
        gvcf, origin = str(params.input), "input"
    else:
        found = evidence.companion(params.input)
        if found is None:
            return None
        gvcf, origin = found, "manifest"
    samples = run_command(
        ["bcftools", "query", "-l", gvcf], capture_output=True
    ).stdout.splitlines()
    if genotypes.sample not in samples:
        warn(
            "pgs_reference_evidence_other_sample",
            "The gVCF beside the input holds another sample; it was not used.",
            reference_evidence=gvcf,
        )
        return None
    indexed = gvcf.endswith((".gz", ".bgz")) and any(
        Path(gvcf + suffix).is_file() for suffix in (".tbi", ".csi")
    )
    if len(samples) > 1 or not indexed:
        # The classifier reads the first sample of an indexed gVCF.
        copy = str(work / "evidence.g.vcf.gz")
        run_command(["bcftools", "view", "--no-version", "-s", genotypes.sample,
                     "-Oz", "-o", copy, gvcf])  # fmt: skip
        run_command(["bcftools", "index", "-t", copy])
        gvcf_read = copy
    else:
        gvcf_read = gvcf
    if origin == "manifest":
        current_result().add_input(gvcf, evidence.ROLE)
    if params.ref is None:
        warn(
            "pgs_reference_evidence_without_ref",
            "Without --ref, sites the gVCF calls callable reference but the "
            "VCF doesn't list can't be checked against the scoring alleles "
            "and stay missing.",
        )
    report("pgs", "Reading reference evidence (gVCF) at the scored sites")
    states = pgs.EvidenceStates(
        gvcf_read,
        genotypes.db,
        ((v.chrom, v.pos) for v in scoring.variants() if v.supported),
        variant_confidence.thresholds_from(None),
        reference,
    )
    states.path = gvcf
    return states, origin


class _Calibration:
    """The reference distributions of a calibrated score, the sample's
    calibrated sum (accumulated while scoring) and its ancestry PCs."""

    def __init__(self, reference: pgs_reference.PgsReference, pgs_id: str) -> None:
        self.reference = reference
        self.pgs_id = pgs_id
        self.accumulator = reference.accumulator(pgs_id)
        self.pcs: Any = None
        self.pca_sites = 0.0
        self.training_regions: Any = None
        self.problem: str | None = None

    def project(
        self,
        params: ScorePgsParams,
        sample: str,
        work: Path,
        confident: pgs.ConfidentBed | None,
    ) -> None:
        from wgsextract_cli.commands.qc.ancestry import pca_projection, vcf_genotypes

        try:
            ancestry = ancestry_reference.load(
                ancestry_reference.library_for(str(params.ref) if params.ref else None),
                cancel=cancel_event(),
            )
            if (
                ancestry_reference.SHA256
                != self.reference.info["ancestry_reference_sha256"]
            ):
                raise WGSExtractError(
                    "the ancestry reference isn't the one the PGS reference was built on"
                )
            genotypes = vcf_genotypes(
                str(params.input),
                ancestry,
                work,
                sample=sample,
                build="hg38",
                confident=confident,
            )
            self.pcs, self.pca_sites = pca_projection(genotypes, ancestry)
            self.training_regions = self.reference.training_regions(
                ancestry.pca()["region"].astype(str)
            )
        except WGSExtractError as exc:
            self.problem = f"No ancestry projection of this input: {exc}"


def _calibration(
    scoring: pgs.ScoringFile, params: ScorePgsParams
) -> tuple[_Calibration | None, str | None]:
    """The calibration of this exact scoring file, when the reference has one,
    or why the reference couldn't be loaded."""
    pgs_id = pgs_reference.SCORES.get(scoring.provenance()["sha256"])
    if pgs_id is None or params.build != pgs_reference.BUILD:
        return None, None
    try:
        reference = pgs_reference.load(
            pgs_reference.library_for(str(params.ref) if params.ref else None),
            cancel=cancel_event(),
        )
    except WGSExtractError as exc:
        return None, str(exc)
    return _Calibration(reference, pgs_id), None


# Catalog ancestry categories (development samples, else GWAS) that cover each
# reference region of the PCA.
_CATALOG_REGIONS = {
    "AFR": {"AFR"},
    "AMR": {"AMR"},
    "CSA": {"SAS", "ASN"},
    "EAS": {"EAS", "ASN"},
    "EUR": {"EUR"},
    "MID": {"GME"},
    "OCE": set(),
}


def _withhold(data: dict[str, Any], status: str, reason: str) -> None:
    data.update(percentile=None, percentile_status=status,
                percentile_unavailable_reason=reason)  # fmt: skip
    warn(
        "pgs_uncalibrated", f"No ancestry-adjusted percentile: {reason}", status=status
    )


def _calibrate(
    data: dict[str, Any],
    calibration: _Calibration | None,
    reference_problem: str | None,
) -> None:
    """Fill the percentile fields, or say why there is none."""
    data["z_adjusted"] = None
    data["ancestry_calibration"] = None
    if data["match_rate"] < data["minimum_match_rate"]:
        _withhold(data, "below_match_threshold",
                  "too few scoring variants matched.")  # fmt: skip
        return
    if reference_problem is not None:
        _withhold(data, "reference_unavailable",
                  f"the HGDP+1KG reference couldn't be loaded: {reference_problem}")  # fmt: skip
        return
    if calibration is None:
        _withhold(data, "reference_calibration_unavailable",
                  "no HGDP+1KG reference distribution for this scoring file "
                  f"(the reference covers the curated scores' {pgs_reference.BUILD} Catalog files).")  # fmt: skip
        return
    reference = calibration.reference
    a: dict[str, Any] = {
        "reference": f"pgs-ref-{reference.version}",
        "method": reference.info["method"],
        "pca_sites_fraction": calibration.pca_sites,
        "pcs": None if calibration.pcs is None else [float(x) for x in calibration.pcs],
    }
    data["ancestry_calibration"] = a
    if calibration.pcs is None:
        _withhold(data, "ancestry_projection_unavailable", str(calibration.problem))
        return
    if calibration.pca_sites < pgs_reference.MIN_ANCESTRY_SITES:
        _withhold(data, "ancestry_projection_incomplete",
                  f"only {calibration.pca_sites:.0%} of the ancestry PCA sites have a "
                  f"genotype (at least {pgs_reference.MIN_ANCESTRY_SITES:.0%} needed).")  # fmt: skip
        return
    a.update(pgs_reference.similarity(reference, calibration.pcs))
    # Unmatched calibration rows at the mean of the most similar region.
    result = calibration.accumulator.result(a["most_similar_population"])
    a.update(
        calibrated_score=result.total,
        calibration_variants=result.matched + result.imputed,
        calibration_variants_matched=result.matched,
        calibration_variants_imputed=result.imputed,
        imputed_variance_share=result.imputed_share,
    )
    if result.imputed_share > pgs_reference.MAX_IMPUTED_VARIANCE:
        _withhold(data, "calibration_coverage_too_low",
                  f"{result.imputed_share:.1%} of the reference-scored variants' variance is "
                  "missing from this sample (at most "
                  f"{pgs_reference.MAX_IMPUTED_VARIANCE:.0%} allowed).")  # fmt: skip
        return
    if a["reference_similarity_p"] < pgs_reference.MIN_POPULATION_P:
        _withhold(data, "ancestry_outside_reference",
                  "the sample's ancestry PCs are outside the HGDP+1KG reference's range.")  # fmt: skip
        return
    a.update(pgs_reference.adjust(reference, calibration.pgs_id, result.total, calibration.pcs,
                                  calibration.training_regions, a["most_similar_population"]))  # fmt: skip
    a["imputation_error_sd"] = pgs_reference.imputation_sd(
        reference, calibration.pgs_id, result, calibration.pcs,
        calibration.training_regions, a["most_similar_population"])  # fmt: skip
    spread = 1.96 * a["imputation_error_sd"]
    a["percentile_interval"] = [
        round(pgs_reference.normal_percentile(a["z_norm2"] - spread), 1),
        round(pgs_reference.normal_percentile(a["z_norm2"] + spread), 1),
    ]
    if a["population_low_confidence"]:
        a.update(empirical_percentile=None, empirical_z=None)
    data.update(
        percentile=round(a["adjusted_percentile"], 1),
        z_adjusted=a["z_norm2"],
        percentile_status="calibrated",
        percentile_unavailable_reason=None,
        confidence="medium",
    )
    development = pgs_reference.DEVELOPMENT_ANCESTRY.get(calibration.pgs_id)
    region = a["most_similar_population"]
    if development is None:
        data["confidence"] = "low"
        warn(
            "pgs_ancestry_unknown",
            "The ancestry the score was developed in is unknown, so it may "
            "predict less well for this sample.",
            most_similar_population=region,
        )
    elif not development & _CATALOG_REGIONS.get(region, set()):
        data["confidence"] = "low"
        warn(
            "pgs_ancestry_mismatch",
            "The score was developed in other ancestries than the reference "
            f"population this sample is most similar to ({region}); it may "
            "predict less well.",
            development=", ".join(sorted(development)),
            most_similar_population=region,
        )
