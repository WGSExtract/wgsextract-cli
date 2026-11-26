"""``panel audit``: local research filtering, callability and allele watches."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from os import SEEK_END
from pathlib import Path
from typing import Any

from wgsextract_cli import __version__
from wgsextract_cli.api.panel import PanelAuditParams
from wgsextract_cli.commands import _panel_calibration
from wgsextract_cli.commands._panel_check import inspect_panel
from wgsextract_cli.commands.vcf.table import VcfStream, table_stem
from wgsextract_cli.core import exon_callability as ec
from wgsextract_cli.core import panel_audit as audit
from wgsextract_cli.core import panel_spec, variant_table
from wgsextract_cli.core import reference_evidence as evidence
from wgsextract_cli.core.atomic_output import publish_output_set
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.lookup import genotypes, input_build
from wgsextract_cli.core.lookup.identifiers import local_contig
from wgsextract_cli.core.panel_watch import Watch
from wgsextract_cli.core.results import (
    current_result,
    human_output,
    manifest_path_for,
    warn,
)
from wgsextract_cli.core.table_writer import (
    Column,
    Value,
    check_format,
    provenance,
    write_table,
)
from wgsextract_cli.core.temp_directory import scratch_directory
from wgsextract_cli.core.utils import UsageError, WGSExtractError, popen
from wgsextract_cli.core.variant_confidence import Thresholds, thresholds_from

_SCHEMA = "wgsextract.panel_audit/1"
_GVCF_SUFFIXES = (
    ".g.vcf",
    ".g.vcf.gz",
    ".g.vcf.bgz",
    ".g.bcf",
    ".gvcf",
    ".gvcf.gz",
    ".gvcf.bgz",
)
_TABLE_COLUMNS = (
    Column("group_id", "TEXT", "Research panel group."),
    Column("decision", "TEXT", "Qualifying or unscored for this group."),
    Column("allele_copies", "INTEGER", "Number of called copies of this ALT."),
    Column("missing_annotations", "TEXT", "Missing annotations, comma separated."),
    Column("research_only", "INTEGER", panel_spec.DISCLAIMER),
    *variant_table.COLUMNS,
)


def _deepvariant_blocks(source: genotypes.VcfSource, work: Path) -> bool:
    # Some DeepVariant exports omit the ALT declaration and retain the same
    # END header line in both the gVCF and the variants-only call set.
    if (
        "##DeepVariant_version=" not in source.header
        or "##INFO=<ID=END," not in source.header
    ):
        return False
    command = [
        "bcftools",
        "query",
        "-i",
        'N_ALT=1 && ALT="<*>" && INFO/END!="."',
        "-f",
        "%POS\n",
        source.path,
    ]
    with (
        tempfile.TemporaryFile(dir=work) as errors,
        popen(command, stdout=subprocess.PIPE, stderr=errors, text=True) as proc,
    ):
        assert proc.stdout is not None
        try:
            if proc.stdout.readline():
                return True
            status = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
        if status:
            errors.seek(max(0, errors.seek(0, SEEK_END) - 1000))
            detail = errors.read().decode(errors="replace").strip()
            raise WGSExtractError(
                f"Could not read {source.path} while checking DeepVariant reference blocks "
                f"(bcftools query exited {status}). Verify or regenerate the input."
                + (f"\n{detail}" if detail else "")
            )
    return False


def _open(path: Path, option: str) -> genotypes.VcfSource:
    if not path.is_file():
        raise UsageError(f"{option} must name a local file: {path}.")
    return genotypes.open_vcf(str(path), None, option=option)


def _gvcf(
    params: PanelAuditParams, source: genotypes.VcfSource, build: str, work: Path
) -> tuple[genotypes.VcfSource | None, genotypes.Evidence | None]:
    path = params.gvcf or (
        Path(source.path) if source.is_gvcf else evidence.companion(source.path)
    )
    if not path:
        warn(
            "panel_no_reference_evidence",
            "No gVCF is available. Unlisted loci are not assessed, not reference.",
        )
        return None, None
    local_path = Path(path).expanduser().absolute()
    gvcf = source if local_path == Path(source.path) else _open(local_path, "--gvcf")
    if not gvcf.is_gvcf:
        gvcf.is_gvcf = _deepvariant_blocks(gvcf, work)
    if not gvcf.is_gvcf:
        raise UsageError(f"--gvcf must contain reference evidence: {path}.")
    if gvcf.sample != source.sample:
        raise UsageError(
            f"The gVCF {path} holds a different sample than {source.path}."
        )
    input_build(gvcf, build)
    if any(
        source.lengths[other] != length
        for name, length in gvcf.lengths.items()
        if (other := local_contig(name, source.lengths)) is not None
    ):
        raise UsageError(
            f"The gVCF {path} and {source.path} disagree on contig lengths."
        )
    current_result().add_input(path, evidence.ROLE)
    readable = evidence.readable(gvcf.path, source.sample, work)
    if readable is None:
        raise UsageError(f"The gVCF {path} no longer holds sample {source.sample}.")
    if readable != gvcf.path:
        gvcf = _open(Path(readable), "--gvcf")
    return gvcf, genotypes.Evidence(
        evidence.ReferenceEvidence(
            readable, header_text=gvcf.header, check_variant_quality=True
        ),
        str(path),
        "explicit" if params.gvcf else "input" if source.is_gvcf else "manifest",
    )


def _callability(
    params: PanelAuditParams,
    build: str,
    reference: genotypes.Evidence | None,
    thresholds: Thresholds,
    symbols: set[str],
    model: ec.ExonModel | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    if params.exons and reference is None and model is None:
        raise UsageError("--exons requires reference evidence from a gVCF.")
    if reference is None or not symbols:
        return {}, model.describe() if model else None
    if model is None:
        if params.exons:
            current_result().add_input(params.exons, "exon_model")
            model = ec.load_model(params.exons)
        elif build in ("hg19", "hg38"):
            model = ec.bundled_model(build)
        else:
            raise UsageError(f"No exon model is bundled for {build}; give --exons.")
    names = ec.contig_names(model, reference.reader.lengths)
    exons = [exon for symbol in sorted(symbols) for exon in model.genes.get(symbol, ())]
    scored = ec.assess(reference.reader, exons, names, thresholds, 0)
    rows = ec.genes(sorted(symbols), model, scored)
    return {row.gene.upper(): row.document({}) for row in rows}, model.describe()


def _aggregate_state(states: set[str]) -> str:
    if "has_qualifying" in states:
        return "has_qualifying"
    if states == {"none_qualifying_callable"}:
        return "none_qualifying_callable"
    if "unscored" in states:
        return "unscored"
    if states <= {"none_qualifying_callable", "no_qualifying_observed"}:
        return "no_qualifying_observed"
    return "not_run" if states <= {"not_run"} else "not_readable"


def _groups(
    groups: Sequence[dict[str, Any]],
    by_gene: Mapping[str, list[audit.Allele]],
    callable_genes: Mapping[str, dict[str, Any]],
) -> list[Sequence[Value]]:
    table: list[Sequence[Value]] = []
    for group in groups:
        absent: Counter[str] = Counter()
        states: set[str] = set()
        for gene in group["genes"]:
            symbol = str(gene.get("symbol", "")).upper()
            callable_gene = callable_genes.get(symbol)
            qualifying: list[audit.Allele] = []
            gene["qualifying"] = []
            gene["unscored"] = []
            for allele in by_gene.get(symbol, ()):
                decision, missing = audit.assess(allele, group["filters"])
                if decision is audit.Decision.REJECTED:
                    continue
                if decision is audit.Decision.QUALIFYING:
                    qualifying.append(allele)
                else:
                    absent.update(missing)
                gene[decision.value].append(allele.document(missing))
                table.append(
                    (
                        group["id"],
                        decision.value,
                        len(allele.copies),
                        ",".join(missing),
                        1,
                        *allele.row,
                    )
                )
            phase, biallelic = audit.allele_phase(qualifying)
            if qualifying:
                state = "has_qualifying"
            elif gene["unscored"]:
                state = "unscored"
            elif callable_gene is None or callable_gene["state"] == ec.NOT_RUN:
                state = "not_run"
            elif callable_gene["state"] == ec.CALLABLE and not callable_gene[
                "states"
            ].get(evidence.State.VARIANT.value):
                state = "none_qualifying_callable"
            elif callable_gene["state"] == ec.CALLABLE:
                state = "no_qualifying_observed"
            else:
                state = "not_readable"
            gene.update(
                callability=callable_gene,
                state=state,
                phase=phase,
                biallelic_state=biallelic,
                qualifying_allele_copies=sum(len(a.copies) for a in qualifying),
            )
            states.add(state)
        group["state"] = _aggregate_state(states)
        group["calibration"] = {"state": "not_run"}
        if absent:
            warn(
                "panel_score_column_missing",
                f"Group {group['id']} has calls left unscored by missing predictor scores.",
                group=group["id"],
                columns=",".join(sorted(absent)),
            )
    return table


def _run(
    params: PanelAuditParams,
    source: genotypes.VcfSource,
    outdir: Path,
    work: Path,
) -> None:
    build = input_build(source, params.build)
    annotation_build = panel_spec.PANEL_BUILDS[build]
    spec, notes, details = inspect_panel(params.panel, annotation_build)
    if (declared := spec.get("build")) is not None and declared != build:
        raise UsageError(f"Panel build {declared} does not match input build {build}.")
    thresholds = thresholds_from(params)
    gvcf, reference = _gvcf(params, source, build, work)
    symbols = {
        gene["symbol"].upper()
        for group in details["groups"]
        for gene in group["genes"]
        if gene.get("symbol") and gene.get("cds_bp")
    }
    calibrator = None
    exon_model = None
    settings = details["calibration"]
    if params.requests_calibration(spec):
        use_expected_counts = params.requests_expected_counts(settings)
        if use_expected_counts and "gnomad_expected_syn" not in settings["match_on"]:
            settings["match_on"] = [*settings["match_on"], "gnomad_expected_syn"]
        calibrator, exon_model = _panel_calibration.prepare(
            params,
            source,
            build,
            details["groups"],
            reference.reader if reference else None,
            thresholds,
            use_expected_counts=use_expected_counts,
        )
    callable_genes, model = _callability(
        params,
        annotation_build,
        reference,
        thresholds,
        symbols,
        exon_model,
    )
    by_gene: dict[str, list[audit.Allele]] = defaultdict(list)
    search_symbols = symbols | (set(calibrator.opportunities) if calibrator else set())
    if search_symbols:
        with tempfile.TemporaryFile(dir=work) as errors:
            stream = VcfStream(
                ["bcftools", "view", "--no-version", source.path], errors
            )
            try:
                if not (
                    "SYMBOL" in (stream.header.csq_fields or ())
                    or "Gene_Name" in (stream.header.ann_fields or ())
                ):
                    raise UsageError(
                        "Gene groups require a VEP or SnpEff annotation with gene "
                        "symbols. An unannotated input cannot show that no variants qualify."
                    )
                for allele in audit.gene_alleles(
                    stream.stdout,
                    stream.header,
                    notes,
                    search_symbols,
                ):
                    if allele.gene in symbols:
                        by_gene[allele.gene].append(allele)
                    if calibrator is not None:
                        calibrator.record(allele)
                stream.check()
            finally:
                stream.close()
    rows = _groups(details["groups"], by_gene, callable_genes)
    calibration = None
    if calibrator is not None:
        if params.null_sets is not None:
            settings["null_sets"] = params.null_sets
        settings["seed"] = (
            params.seed if params.seed is not None else settings.get("seed", 0)
        )
        calibration = calibrator.finish(details["groups"], settings)
    if spec.get("sites") or spec.get("haplotypes"):
        if (
            gvcf is not None
            and source.is_gvcf
            and reference
            and reference.origin == "input"
        ):
            watch_source = gvcf
        elif not source.indexed:
            with scratch_directory(work, ".watch_") as index_work:
                readable = evidence.readable(
                    source.path, source.sample, Path(index_work)
                )
                if readable is None:
                    raise UsageError(
                        f"{source.path} no longer holds sample {source.sample}."
                    )
                _watches(
                    spec,
                    details,
                    _open(Path(readable), "--vcf-input"),
                    gvcf,
                    reference,
                    thresholds,
                )
            watch_source = None
        else:
            watch_source = source
        if watch_source is not None:
            _watches(spec, details, watch_source, gvcf, reference, thresholds)
    document = {
        **details,
        "schema": _SCHEMA,
        "wgsextract_version": __version__,
        "research_only": True,
        "disclaimer": panel_spec.DISCLAIMER,
        "sample": source.sample,
        "source_panel": spec,
        "exon_model": model,
        "calibration_universe": calibration,
        "reference_evidence": reference.origin if reference else None,
    }
    _write(params, source, build, outdir, document, rows)


def _watches(
    spec: Mapping[str, Any],
    details: dict[str, Any],
    source: genotypes.VcfSource,
    gvcf: genotypes.VcfSource | None,
    reference: genotypes.Evidence | None,
    thresholds: Thresholds,
) -> None:
    watch = Watch(source, reference, gvcf, thresholds)
    for site in details["sites"]:
        found = watch.at(site["locus"])
        site.update({k: v for k, v in found.items() if k != "locus"})
    for checked, haplotype in zip(
        details["haplotypes"], spec.get("haplotypes", ()), strict=True
    ):
        found = watch.haplotype(haplotype)
        checked.update({k: v for k, v in found.items() if k != "gene"})
        if found["status"] == "ambiguous_phase":
            warn(
                "panel_haplotype_ambiguous_phase",
                f"Haplotype {checked['id']} has no phase between informative sites.",
                haplotype=checked["id"],
            )


def _write(
    params: PanelAuditParams,
    source: genotypes.VcfSource,
    build: str,
    outdir: Path,
    document: dict[str, Any],
    rows: list[Sequence[Value]],
) -> None:
    stem = table_stem(source.path).removesuffix("_variants") + ".panel_audit"
    report = outdir / f"{stem}.json"
    table = outdir / f"{stem}.{params.table_format}"
    result = current_result()
    inputs = [Path(record.path) for record in result.inputs]
    for destination in (report, table, manifest_path_for(report)):
        for path in inputs:
            path = path.expanduser()
            if destination.resolve() == path.resolve() or (
                destination.exists() and destination.samefile(path)
            ):
                raise UsageError(
                    f"Audit output {destination} would overwrite input {path}."
                )
    result.expect_output(report)
    with tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_audit_output_") as work:
        staged_table, staged_report = Path(work, table.name), Path(work, report.name)
        count = write_table(
            staged_table,
            _TABLE_COLUMNS,
            rows,
            params.table_format,
            metadata=provenance(
                inputs,
                reference_build=build,
                sample=source.sample,
                research_only="true",
                disclaimer=panel_spec.DISCLAIMER,
            ),
        )
        staged_report.write_text(
            json.dumps(document, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        publish_output_set({staged_table: table, staged_report: report})
    result.add_output(report, "panel_audit")
    result.add_output(table, "panel_audit_table", metrics={"rows": count})
    result.add_metrics(rows=count, sample=source.sample, reference_build=build)
    result.set_details({"schema": _SCHEMA, "research_only": True})
    human_output(f"{panel_spec.DISCLAIMER}\nOutputs: {report}, {table.name}")


def run_panel_audit(params: PanelAuditParams) -> None:
    verify_dependencies(["bcftools"])
    check_format(params.table_format)
    source_path = params.vcf_input.expanduser().absolute()
    source = _open(source_path, "--vcf-input")
    current_result().add_input(source.path, "vcf_input")
    outdir = (params.outdir or Path(source.path).parent).expanduser().absolute()
    outdir.mkdir(parents=True, exist_ok=True)
    with scratch_directory(outdir, ".wgse_panel_audit_") as work:
        # Ordinary indexed VCF site audits must not scan the whole file.
        if (
            params.gvcf is None
            and not source.is_gvcf
            and source_path.name.lower().endswith(_GVCF_SUFFIXES)
        ):
            source.is_gvcf = _deepvariant_blocks(source, Path(work))
        _run(params, source, outdir, Path(work))
