import json
import logging
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from wgsextract_cli import __version__
from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.vcf import VcfChainAnnotateParams, VcfTableParams
from wgsextract_cli.core import gtex_data, regulatory_data, regulatory_overlap
from wgsextract_cli.core._confidence_rules import thresholds_from
from wgsextract_cli.core.artifacts import ChildRun, ended_as, lineage_args
from wgsextract_cli.core.atomic_output import atomic_output, publish_output_set
from wgsextract_cli.core.config import config_args
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.output_checks import vcf_record_count
from wgsextract_cli.core.paths import absolute_path, same_path
from wgsextract_cli.core.ref_library.assets import input_stem
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary
from wgsextract_cli.core.results import (
    MANIFEST_SUFFIX,
    current_result,
    manifest_dir_for,
    warn,
)
from wgsextract_cli.core.steps import Step, StepJournal, resume_policy
from wgsextract_cli.core.table_writer import check_format
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    get_resource_threads,
    run_command,
)
from wgsextract_cli.core.variant_files import calculate_bam_md5, ensure_vcf_indexed
from wgsextract_cli.core.vcf_build import require_grch38
from wgsextract_cli.core.workspace_activity import forward_workspace_admission

from .._vep_engines import VepEngine, configured_engine
from .._vep_run import vep_output_paths
from .basic import record_variants, require_vcf_input, vcf_input_handler
from .chain_scope import ChainScope, resolve_scope, select_records, vcf_indexes
from .clinvar_revel import CLINVAR_ANNOTATED, REVEL_ANNOTATED
from .deleteriousness import CADD_ANNOTATED, GERP_ANNOTATED
from .gtex import (
    GTEX_TSV,
    input_generations,
    protect_output,
    record_sidecar_source,
    write_sidecar,
)
from .population import GNOMAD_ANNOTATED, PHYLOP_ANNOTATED
from .regulatory import REGULATORY_ANNOTATED
from .splicing import ALPHAMISSENSE_ANNOTATED, PHARMGKB_ANNOTATED, SPLICEAI_ANNOTATED
from .table import run_table

_JOURNAL = "chain_annotate.steps.json"
_GTEX_SIDECAR = "chain_" + GTEX_TSV

# annotation -> (ReferenceLibrary attribute holding its data, step output name)
_VCF_STEPS = {
    "clinvar": ("clinvar_vcf", CLINVAR_ANNOTATED),
    "revel": ("revel_file", REVEL_ANNOTATED),
    "phylop": ("phylop_file", PHYLOP_ANNOTATED),
    "cadd": ("cadd_file", CADD_ANNOTATED),
    "gerp": ("gerp_file", GERP_ANNOTATED),
    "gnomad": ("gnomad_vcf", GNOMAD_ANNOTATED),
    "spliceai": ("spliceai_vcf", SPLICEAI_ANNOTATED),
    "alphamissense": ("alphamissense_vcf", ALPHAMISSENSE_ANNOTATED),
    "pharmgkb": ("pharmgkb_vcf", PHARMGKB_ANNOTATED),
    "regulatory": ("regulatory_file", REGULATORY_ANNOTATED),
}


def _step_command(ann: str) -> list[str]:
    if ann == "vep":
        return ["vep", "run"]
    return ["vcf", ann]


def _step_output(ann: str, step_input: str, step_outdir: str) -> str:
    if ann == "vep":
        return vep_output_paths(step_input, step_outdir, "vcf")[1]
    return str(Path(step_outdir, _VCF_STEPS[ann][1]))


def _forward_step_warnings(step: str, ann: str, step_outdir: str) -> None:
    """Repeat what a step warned about its annotation data (``annotation_*``,
    such as resource records it excluded): its log was captured and its folder
    is removed. Findings about the sample stay with the step's own command."""
    step_path = Path(step_outdir)
    manifests = sorted(manifest_dir_for(step_path).glob(f"*{MANIFEST_SUFFIX}"))
    if not manifests:
        manifests = sorted(step_path.glob(f"*{MANIFEST_SUFFIX}"))
    for manifest in manifests:
        if manifest.name.endswith(".failed" + MANIFEST_SUFFIX):
            continue  # an earlier, failed attempt's
        for item in json.loads(manifest.read_text(encoding="utf-8"))["warnings"]:
            if item["code"].startswith("annotation_"):
                warn(
                    item["code"],
                    f"{step} '{ann}': {item['message']}",
                    **item["details"],
                )


def dataset(ann: str, lib: ReferenceLibrary) -> str | None:
    """The reference data an annotation step reads: a file, or VEP's cache
    directory; None when it isn't installed."""
    # chain-annotate is meant to run offline, so VEP also needs its cache: the
    # configured engine's (vep-rs keeps its own in the cache's vep-rs folder).
    if ann == "vep":
        if not lib.vep_cache:
            return None
        cache = vep_step_engine(lib).cache_root(Path(lib.vep_cache))
        return str(cache) if cache.is_dir() else None
    attribute = "gtex_file" if ann == "gtex" else _VCF_STEPS[ann][0]
    return getattr(lib, attribute, None) or None


def _step_resource_args(ann: str, lib: ReferenceLibrary) -> list[str]:
    if ann == "regulatory":
        return []
    if ann == "vep":
        assert lib.vep_cache is not None
        # Pin the same offline engine and cache the journal tracks: standalone
        # VEP also looks in ~/.vep when it automatically selects an engine.
        return ["--vep-cache", lib.vep_cache, "--engine", vep_step_engine(lib).name]
    resource = dataset(ann, lib)
    if not resource:
        return []
    flag = "--am-file" if ann == "alphamissense" else f"--{ann}-file"
    args = [flag, resource]
    if ann == "cadd" and lib.cadd_indel_file:
        args += ["--cadd-indel-file", lib.cadd_indel_file]
    return args


def vep_step_engine(lib: ReferenceLibrary) -> VepEngine:
    """The engine the VEP step's `vep run` will use with this library's cache."""
    return configured_engine([Path(lib.vep_cache)] if lib.vep_cache else [])


def skip_reason(ann: str, lib: ReferenceLibrary) -> str | None:
    """Why an annotation step can't run here, or None if it can."""
    tool = "bcftools"
    if ann == "vep":
        tool = "vep-rs" if vep_step_engine(lib).name == "vep-rs" else "vep"
    if not get_tool_path(tool):
        return f"{tool} is not installed."
    if not dataset(ann, lib):
        if ann == "regulatory":
            return regulatory_data.missing(regulatory_data.path(lib.root))
        return (
            "required reference data not found. "
            f"Run 'wgsextract ref {ann}' to download."
        )
    if ann == "clinvar" and not getattr(lib, "fasta", None):
        return "matching reference FASTA not found; pass --ref with an indexed FASTA."
    return None


def _annotations(params: VcfChainAnnotateParams) -> list[str]:
    annotations = [
        a.strip().lower() for a in params.annotations.split(",") if a.strip()
    ]
    if not annotations:
        raise WGSExtractError("No valid annotations provided in --annotations.")
    unknown = [
        a for a in annotations if a not in {"vep", "gtex"} and a not in _VCF_STEPS
    ]
    if unknown:
        raise WGSExtractError(
            f"Unknown annotation type(s): {', '.join(unknown)}. Valid: "
            f"{', '.join([*_VCF_STEPS, 'vep', 'gtex'])}"
        )
    repeated = [ann for ann, count in Counter(annotations).items() if count > 1]
    if repeated:
        raise UsageError(f"--annotations lists {', '.join(repeated)} more than once.")
    return annotations


def _annotation_step(
    ann: str,
    position: int,
    source: str,
    output: str,
    lib: ReferenceLibrary,
) -> Step:
    inputs: dict[str, str | None] = {
        "vcf": source,
        "data": dataset(ann, lib),
        "reference": lib.fasta,
    }
    if lib.fasta:
        for ext in (".fai", ".gzi"):
            if Path(lib.fasta + ext).is_file():
                inputs[ext[1:]] = lib.fasta + ext
    if ann == "cadd":
        inputs["indel_data"] = lib.cadd_indel_file
    settings: dict[str, object] = {"wgsextract": __version__}
    tools = ["bcftools", "bgzip", "tabix"]
    if ann == "regulatory":
        table = regulatory_data.path(lib.root)
        if not (source_data := regulatory_data.provenance(table, verify=True)):
            raise WGSExtractError(regulatory_data.missing(table))
        inputs.update(
            data=str(table),
            index=str(regulatory_data.index(table)),
            provenance=str(regulatory_data.marker(table)),
        )
        settings["regulatory_source"] = source_data
    if ann == "clinvar":
        settings["confidence"] = asdict(thresholds_from())
    if ann == "vep":
        engine = vep_step_engine(lib)
        settings.update(engine=engine.name, build=lib.build)
        tools.append(engine.tool)
    return Step(
        f"{ann}:{position}",
        inputs=inputs,
        outputs=[output],
        params=settings,
        tools=tools,
        version=2 if ann == "vep" else 1,
    )


def _invoke_annotation(command: list[str], ann: str, step: str, output: str) -> None:
    try:
        # Captured so chained runs stay readable; surfaced on failure.
        res = run_command(forward_workspace_admission(command), capture_output=True)
    except subprocess.CalledProcessError as e:
        for line in (e.stderr or "").strip().splitlines():
            logging.error(f"  [{ann} ERROR] {line}")
        raise WGSExtractError(f"{step} Annotation step '{ann}' failed.") from e
    if not Path(output).is_file():
        for line in (res.stderr or "").strip().splitlines():
            logging.error(f"  [{ann} STDERR] {line}")
        raise WGSExtractError(f"{step} '{ann}' did not produce {output}.")


def _record_step_run(
    ann: str,
    result_file: Path,
    reason: str,
    outcome: Literal["ok", "failed", "cancelled"],
) -> ChildRun:
    try:
        document = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = None
    result_file.unlink(missing_ok=True)
    if (
        outcome != "ok"
        and isinstance(document, dict)
        and document.get("status") == "ok"
    ):
        # The annotator exited successfully but its expected output is missing.
        document["status"] = outcome
    return current_result().run.add_child(
        ann,
        document,
        command=" ".join(_step_command(ann)),
        reason=reason,
        outcome=outcome,
    )


def _run_annotation(
    journal: StepJournal,
    step: Step,
    ann: str,
    label: str,
    source: str,
    output: str,
    args: list[str],
) -> None:
    decision = journal.decide(step)
    if journal.dry_run:
        return
    if decision.run:
        if same_path(output, source):
            raise UsageError(f"Annotation step '{ann}' would overwrite its input.")
        with journal.running(decision) as handle:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).unlink(missing_ok=True)
            for index in vcf_indexes(output):
                index.unlink()
            result_file = Path(output).parent / "step.result.json"
            result_file.unlink(missing_ok=True)
            try:
                _invoke_annotation(
                    [*args, "--result-file", str(result_file)], ann, label, output
                )
                if ann == "regulatory":
                    _check_regulatory_generation(result_file, step)
            except BaseException as error:
                _record_step_run(ann, result_file, decision.reason, ended_as(error))
                raise
            child = _record_step_run(ann, result_file, decision.reason, "ok")
            handle.metrics["result_runs"] = [
                {"run": child.run, "command": child.command}
            ]
            for index in vcf_indexes(output):
                handle.add_output(index)
    else:
        current_result().run.record_step(
            ann, decision.metrics, decision.reason, "reused"
        )
    _forward_step_warnings(label, ann, str(Path(output).parent))


def _check_regulatory_generation(result_file: Path, step: Step) -> None:
    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
        actual = result["details"]["regulatory_source"]
    except (OSError, ValueError, KeyError, TypeError):
        actual = None
    if actual != step.params["regulatory_source"]:
        raise WGSExtractError(
            "SCREEN track changed during this chain. Rerun the chain."
        )


def _scope_step(
    journal: StepJournal,
    name: str,
    source: str,
    outdir: Path,
    params: VcfChainAnnotateParams,
    scope: ChainScope,
    *,
    whole_contigs: bool = False,
) -> str:
    # VEP infers structural-variant mode from the input stem.
    output = str(outdir / f"{input_stem(source)}.vcf.gz")
    step = Step(
        name,
        inputs={
            "vcf": source, "bed": params.bed, "reference_index": scope.reference_index,
            **{f"index:{p.name}": p for p in vcf_indexes(source)},
        },
        outputs=[output],
        params={
            "region": params.region, "whole_contigs": whole_contigs,
            "bed_sha256": scope.bed_sha256,
        },
        tools=["bcftools", "tabix"],
        version=2,
    )  # fmt: skip
    decision = journal.decide(step)
    if decision.run and not journal.dry_run:
        if same_path(output, source):
            raise UsageError("Region selection would overwrite its input.")
        with journal.running(decision) as handle:
            outdir.mkdir(parents=True, exist_ok=True)
            with atomic_output(output) as staged:
                select_records(
                    source, staged, scope.whole_contigs if whole_contigs else scope.intervals,
                    get_resource_threads(params.threads, stage="compression"),
                    remap=name == "trim",
                )  # fmt: skip
                ensure_vcf_indexed(staged)
            for index in vcf_indexes(output):
                handle.add_output(index)
    return output


def _requires_contig_context(ann: str, lib: ReferenceLibrary) -> bool:
    if not lib.fasta or ann in {"gerp", "regulatory", "vep"}:
        return False
    resource = dataset(ann, lib)
    return not (
        ann == "phylop" and resource and resource.lower().endswith((".bw", ".bigwig"))
    )


def _work_dir(path: Path, protected: list[Path], cleanup: list[Path]) -> Path:
    resolved = path.resolve()
    if path.is_symlink() or any(
        source.is_relative_to(path) or source.resolve().is_relative_to(resolved)
        for source in protected
    ):
        raise UsageError(
            f"The chain needs its own workspace at {path}; choose another --outdir."
        )
    cleanup.append(path)
    return path


def _plan_manifest_stem(
    outdir: Path,
    params: VcfChainAnnotateParams,
    input_file: str,
    lib: ReferenceLibrary,
    annotations: list[str],
) -> Path:
    stem = outdir / "chain_annotate.plan"
    manifest = manifest_dir_for(outdir) / (stem.name + MANIFEST_SUFFIX)
    protected = _chain_inputs(params, input_file, lib, annotations)
    if any(path is not None and same_path(manifest, path) for path in protected):
        raise UsageError(
            "The dry-run manifest would overwrite an input; choose another --outdir."
        )
    return stem


def _chain_inputs(
    params: VcfChainAnnotateParams,
    input_file: str,
    lib: ReferenceLibrary,
    annotations: list[str],
) -> list[str | Path | None]:
    tracks = [dataset(ann, lib) for ann in annotations]
    if "cadd" in annotations:
        tracks.append(lib.cadd_indel_file)
    paths: list[str | Path | None] = [
        input_file, params.bed, params.ref, lib.fasta, lib.fai,
        *vcf_indexes(input_file), *tracks,
    ]  # fmt: skip
    for path in (params.bed, lib.fasta, *tracks):
        if path:
            paths.extend(vcf_indexes(str(path)))
            paths.append(f"{path}.gzi")
    if "gtex" in annotations:
        paths.append(gtex_data.receipt_path(gtex_data.path(lib.root)))
    if "regulatory" in annotations:
        track = regulatory_data.path(lib.root)
        paths.extend((regulatory_data.index(track), regulatory_data.marker(track)))
    return paths


def _run_steps(
    journal: StepJournal,
    params: VcfChainAnnotateParams,
    annotations: list[str],
    source: str,
    outdir: Path,
    lib: ReferenceLibrary,
    child_args: list[str],
    step_dirs: list[Path],
    scope: ChainScope | None,
    gtex_table: Path | None,
) -> str:
    ready: list[tuple[int, str]] = []
    for position, ann in enumerate(annotations, start=1):
        if reason := skip_reason(ann, lib):
            if ann == "regulatory":
                raise WGSExtractError(reason)
            warn(
                "annotation_skipped",
                f"[{position}/{len(annotations)}] Skipping '{ann}': {reason}",
                annotation=ann,
                reason=reason,
            )
        else:
            ready.append((position, ann))
    if not ready and gtex_table is None:
        raise WGSExtractError(
            "No annotation was applied: every requested step was skipped."
        )

    protected = [
        absolute_path(path)
        for path in (
            source,
            params.bed,
            lib.fasta,
            gtex_table,
            *(dataset(ann, lib) for _, ann in ready),
        )
        if path is not None
    ]
    if any(ann == "cadd" for _, ann in ready) and lib.cadd_indel_file:
        protected.append(absolute_path(lib.cadd_indel_file))

    last_context_step = 0
    if scope is not None:
        # Left alignment can move an indel arbitrarily far within its contig;
        # ClinVar can also compose MNVs from SNVs outside the requested span.
        last_context_step = max(
            (i for i, ann in ready if _requires_contig_context(ann, lib)), default=0
        )
        scope_dir = _work_dir(outdir / "chain_select", protected, step_dirs)
        source = _scope_step(
            journal, "select", source, scope_dir, params, scope,
            whole_contigs=bool(last_context_step),
        )  # fmt: skip

    reference = lib.fasta or params.ref
    for position, ann in ready:
        step_dir = _work_dir(
            outdir / f"chain_step_{position}_{ann}", protected, step_dirs
        )
        output = _step_output(ann, source, str(step_dir))
        args = [
            *child_args, *_step_command(ann),
            "--input", source, "--outdir", str(step_dir),
        ]  # fmt: skip
        args += [
            "--threads",
            str(params.threads) if params.threads is not None else "auto",
        ]
        step_ref = lib.root if ann == "regulatory" else reference
        if step_ref:
            args += ["--ref", str(step_ref)]
        args += _step_resource_args(ann, lib)
        args += lineage_args(current_result().run, ann)
        _run_annotation(
            journal, _annotation_step(ann, position, source, output, lib),
            ann, f"[{position}/{len(annotations)}]", source, output, args,
        )  # fmt: skip
        source = output
        if scope is not None and position == last_context_step:
            trim_dir = _work_dir(outdir / "chain_trim", protected, step_dirs)
            source = _scope_step(journal, "trim", source, trim_dir, params, scope)
    if not journal.dry_run:
        applied = [ann for _, ann in ready]
        if gtex_table is not None:
            applied.append("gtex")
        current_result().add_metrics(
            annotations_applied=",".join(applied),
            annotations_skipped=len(annotations) - len(ready),
        )
    return source


def _publish_chain(
    source: str,
    input_file: str,
    final: Path,
    table: Path | None,
    sidecar: Path,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, int]:
    with (
        TemporaryDirectory(prefix=".chain-publish-", dir=final.parent) as stage,
        ExitStack() as outputs,
    ):
        vcf = Path(stage, final.name)
        if source == input_file:
            # With only GTEx selected there may be no compressed VCF step.
            run_command(["bcftools", "view", "--no-version", "-Oz", "-o", vcf, source])
            run_command(["tabix", "-p", "vcf", vcf])
        else:
            shutil.copy2(source, vcf)
            for ext in (".tbi", ".csi"):
                if Path(source + ext).exists():
                    shutil.copy2(source + ext, str(vcf) + ext)
        metrics = {}
        if table is not None:
            tsv = outputs.enter_context(atomic_output(sidecar, retire_indexes=False))
            metrics = write_sidecar(str(vcf), table, Path(tsv), sidecar)
        indexes = {
            Path(str(vcf) + ext): Path(str(final) + ext) for ext in (".tbi", ".csi")
        }
        files = {src: dst for src, dst in indexes.items() if src.exists()}
        files[vcf] = final
        publish_output_set(
            files,
            retire=list(indexes.values()),
            checkpoint=checkpoint,
            finalize=outputs.close if table is not None else None,
        )
    return metrics


def _publish_gtex(
    journal: StepJournal,
    source: str,
    input_file: str,
    final: Path,
    table: Path,
    sidecar: Path,
    protect_sidecar: Callable[[], None],
) -> None:
    source_indexes = vcf_indexes(source) if source != input_file else ()
    inputs: dict[str, str | Path] = {
        "vcf": source,
        "track": table,
        "track_provenance": gtex_data.receipt_path(table),
        **{f"index:{p.name}": p for p in source_indexes},
    }
    generations = input_generations(inputs)

    def unchanged() -> None:
        protect_sidecar()
        if input_generations(inputs) != generations:
            raise WGSExtractError(
                "A GTEx chain input changed during annotation. Rerun the chain."
            )

    decision = journal.decide(
        Step(
            "gtex",
            inputs=inputs,
            # Journal completion re-reads paths; key reuse to the generation we used.
            params={"input_generations": generations},
            outputs=[final, sidecar],
            tools=["bcftools", "tabix"],
        )
    )
    if journal.dry_run:
        return
    if decision.run:
        # A retry must not remove previous outputs before their replacements exist.
        with journal.running(decision, keep_partial=True) as handle:
            handle.metrics.update(
                _publish_chain(
                    source, input_file, final, table, sidecar, checkpoint=unchanged
                )
            )
            for path in vcf_indexes(str(final)):
                handle.add_output(path)
    else:
        with gtex_data.open_track(table, verify=True, cancel=cancel_event()) as (
            _,
            provenance,
        ):
            unchanged()
            record_sidecar_source(
                table,
                provenance,
                {
                    key: value
                    for key, value in decision.metrics.items()
                    if key.startswith("gtex_") and isinstance(value, int)
                },
            )
    current_result().add_output(sidecar, "gtex_eqtl_associations")


def run_chain_annotate(
    params: VcfChainAnnotateParams, keep_awake: bool | None = None
) -> None:
    verify_dependencies(["bcftools", "tabix"])
    annotations = _annotations(params)
    table_format = params.table_format or "tsv"
    if params.to_table:
        check_format(table_format)
    input_file = require_vcf_input(params)
    outdir = absolute_path(params.outdir or absolute_path(input_file).parent)
    final_out = outdir / "chain_annotated.vcf.gz"
    if same_path(final_out, input_file):
        raise UsageError(
            "vcf chain-annotate would overwrite its input; choose another --outdir."
        )
    md5_sig = (
        calculate_bam_md5(input_file, None)
        if input_file.lower().endswith((".bam", ".cram"))
        else None
    )
    lib = ReferenceLibrary(params.ref, md5_sig, input_path=input_file)
    sidecar = outdir / _GTEX_SIDECAR
    sidecar_inputs = (
        _chain_inputs(params, input_file, lib, sorted(set(annotations) | _VCF_STEPS.keys()))
        if "gtex" in annotations or sidecar.exists() or sidecar.is_symlink()
        else []
    )  # fmt: skip

    def protect_sidecar() -> None:
        protect_output(sidecar, *sidecar_inputs, final_out)

    protect_sidecar()
    result = current_result()
    if not params.dry_run:
        result.expect_output(final_out)
    if "regulatory" in annotations:
        regulatory_overlap.require_grch38(input_file)
        table = regulatory_data.path(lib.root)
        if not regulatory_data.provenance(table, verify=True):
            raise WGSExtractError(regulatory_data.missing(table))
    gtex_table = None
    if "gtex" in annotations:
        require_grch38(input_file, "GTEx")
        gtex_table = gtex_data.path(lib.root)
        gtex_receipt = gtex_data.receipt_path(gtex_table)
        if not gtex_data.installed(gtex_table):
            raise WGSExtractError(gtex_data.missing(gtex_table))
        protect_output(final_out, gtex_table, gtex_receipt)
    if params.dry_run:
        result.set_manifest_stem(
            _plan_manifest_stem(outdir, params, input_file, lib, annotations)
        )
    scope = resolve_scope(input_file, params.region, params.bed, lib.fasta)
    child_args = [
        sys.executable, "-m", "wgsextract_cli.main",
        *config_args(keep_awake=keep_awake),
    ]  # fmt: skip

    logging.info(f"Starting chained annotation with: {', '.join(annotations)}")
    step_dirs: list[Path] = []
    with StepJournal(
        outdir / _JOURNAL, params.COMMAND, resume_policy(params)
    ) as journal:
        try:
            source = _run_steps(
                journal,
                params,
                [ann for ann in annotations if ann != "gtex"],
                input_file,
                outdir,
                lib,
                child_args,
                step_dirs,
                scope,
                gtex_table,
            )
            if gtex_table is not None:
                _publish_gtex(
                    journal,
                    source,
                    input_file,
                    final_out,
                    gtex_table,
                    sidecar,
                    protect_sidecar,
                )
            elif journal.dry_run:
                journal.decide(
                    Step("publish", inputs={"vcf": source}, outputs=[final_out])
                )
            else:
                _publish_chain(source, input_file, final_out, None, sidecar)
            if journal.dry_run:
                if params.to_table:
                    journal.decide(Step("table", inputs={"vcf": final_out}))
                return
            logging.info(f"Chain annotation complete: {final_out}")
            count = vcf_record_count(final_out)
            record_variants(str(final_out), "annotated_vcf", count)
            if count == 0:
                warn(
                    "no_variants",
                    "The annotated VCF contains no records.",
                    path=str(final_out),
                )
            if params.to_table:
                run_table(
                    VcfTableParams(
                        input=final_out, outdir=outdir, table_format=table_format
                    )
                )
        except BaseException:
            if step_dirs and not journal.dry_run:
                logging.warning("Preserving chain annotation intermediates for resume.")
            raise
        if not params.keep_intermediates:
            for step_dir in step_dirs:
                shutil.rmtree(step_dir, ignore_errors=True)


cmd_chain_annotate = vcf_input_handler(
    VcfChainAnnotateParams, run_chain_annotate, keep_awake="keep_awake"
)
