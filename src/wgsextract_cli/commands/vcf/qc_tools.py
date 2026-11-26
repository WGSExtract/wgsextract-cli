"""`vcf variantqc` (DISCVRSeq VariantQC) and `vcf granite-qc` (granite qcVCF)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from wgsextract_cli.api.vcf import VcfGraniteQcParams, VcfVariantQcParams
from wgsextract_cli.core import sample_workspace
from wgsextract_cli.core.dependencies import find_discvrseq_jar, get_tool_path
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.paths import absolute_path, expand_path
from wgsextract_cli.core.ref_library.assets import (
    is_fasta_path,
    resolve_reference_library,
)
from wgsextract_cli.core.reference_resolver import ReferenceLibrary
from wgsextract_cli.core.results import Scalar, current_result, human_output, warn
from wgsextract_cli.core.tool_catalog import INSTALL_HINTS
from wgsextract_cli.core.tool_commands import _tool_command_parts
from wgsextract_cli.core.utils import (
    MissingDependencyError,
    UsageError,
    WGSExtractError,
    get_resource_defaults,
    run_command,
)
from wgsextract_cli.core.variant_files import verify_paths_exist
from wgsextract_cli.core.variant_sample import prepare_input_vcf
from wgsextract_cli.core.vcf_reference import identify_vcf_reference

from .._extract_helpers import cleanup_files
from .basic import require_vcf_input, vcf_input_handler


def _vcf_stem(path: Path) -> str:
    name = path.name
    for suffix in (".vcf.gz", ".vcf.bgz", ".g.vcf.gz", ".gvcf.gz", ".vcf", ".bcf"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _discvrseq_argv(explicit_jar: str | None, memory: str) -> list[str]:
    jar = find_discvrseq_jar(explicit_jar)
    if jar:
        java17 = get_tool_path("java17")
        if not java17:
            raise MissingDependencyError(
                "Java 17 is required to run DISCVRSeq VariantQC. "
                "Install it with 'wgsextract deps install java17' "
                "(or install java17 in Tools / set java17_executable in config.toml)."
            )
        return [*_tool_command_parts(java17), f"-Xmx{memory}", "-jar", jar]
    if explicit_jar:
        raise WGSExtractError(f"DISCVRSeq JAR not found: {explicit_jar}")
    wrapper = get_tool_path("discvrseq")
    if wrapper:
        return _tool_command_parts(wrapper)
    raise MissingDependencyError(
        f"DISCVRSeq is not installed. {INSTALL_HINTS['discvrseq']}"
    )


def _granite_argv(explicit_path: str | None) -> list[str]:
    if explicit_path:
        resolved = Path(expand_path(explicit_path))
        if not resolved.is_file():
            raise WGSExtractError(f"granite executable not found: {explicit_path}")
        return [str(resolved)]
    found = get_tool_path("granite")
    if found:
        return _tool_command_parts(found)
    raise MissingDependencyError(
        f"granite is not installed. {INSTALL_HINTS['granite']}"
    )


def _variantqc_label(path: Path, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", _vcf_stem(path)).strip("_.-") or "vcf"
    label = base
    suffix = 2
    while label in used:
        label = f"{base}_{suffix}"
        suffix += 1
    used.add(label)
    return label


def run_variantqc(params: VcfVariantQcParams) -> None:
    """Run DISCVRSeq VariantQC to produce an interactive HTML VCF QC report."""
    input_file = require_vcf_input(params)
    verify_paths_exist({"--vcf-input": input_file})
    vcf_path = Path(absolute_path(input_file))
    current_result().add_input(
        str(vcf_path), "vcf_input" if params.vcf_input else "input"
    )
    extra_paths: list[Path] = []
    for extra in params.extra_vcf or ():
        extra_str = os.fspath(extra)
        verify_paths_exist({"--extra-vcf": extra_str})
        resolved_extra = Path(absolute_path(extra_str))
        if resolved_extra != vcf_path and resolved_extra not in extra_paths:
            extra_paths.append(resolved_extra)
            current_result().add_input(str(resolved_extra), "extra_vcf")
    all_vcfs = [vcf_path, *extra_paths]

    _, memory = get_resource_defaults(None, params.memory)
    explicit_jar = os.fspath(params.discvrseq_jar) if params.discvrseq_jar else None
    cmd_base = _discvrseq_argv(explicit_jar, memory)

    ref_arg = os.fspath(params.ref) if params.ref else None
    user_fasta = ref_arg if ref_arg and is_fasta_path(ref_arg) else None
    ref_lib = resolve_reference_library(ref_arg if ref_arg and not user_fasta else None)
    detection = identify_vcf_reference(
        vcf_path, ref_lib_dir=ref_lib, reference_fasta=user_fasta
    )
    lib = ReferenceLibrary(
        ref_arg or ref_lib, detection.signature, input_path=str(vcf_path)
    )
    resolved_ref = user_fasta or lib.fasta
    if not resolved_ref or not Path(resolved_ref).is_file():
        raise WGSExtractError(
            "DISCVRSeq VariantQC requires the matching reference FASTA (-R). "
            "Install the matching genome in Reference Genomes or pass --ref."
        )
    current_result().set_reference(resolved_ref, detection.build)

    outdir = Path(os.fspath(params.outdir)) if params.outdir else vcf_path.parent
    outdir.mkdir(parents=True, exist_ok=True)
    out_html = outdir / f"{_vcf_stem(vcf_path)}.variantqc.html"

    ped = os.fspath(params.pedigree) if params.pedigree else None
    if ped:
        verify_paths_exist({"--pedigree": ped})

    with ExitStack() as cleanup:
        # GATK reads only indexed VCFs: index each in place, or an indexed
        # working copy when it is plain text or its folder is read-only.
        inputs = [
            prepare_input_vcf(str(path), None, str(outdir), cleanup)[0]
            for path in all_vcfs
        ]
        cmd = [*cmd_base, "VariantQC", "-R", resolved_ref]
        if len(all_vcfs) == 1:
            cmd.extend(["-V", inputs[0]])
        else:
            used_labels: set[str] = set()
            for path, vcf_arg in zip(all_vcfs, inputs, strict=True):
                cmd.extend([f"-V:{_variantqc_label(path, used_labels)}", vcf_arg])
        cmd.extend(["-O", str(out_html)])
        if ped:
            cmd.extend(["-ped", ped])

        logging.info(
            "Running DISCVRSeq VariantQC on %d VCF(s) (%s) -> %s",
            len(all_vcfs),
            ", ".join(p.name for p in all_vcfs),
            out_html,
        )
        try:
            with cleanup_files(on_failure=[str(out_html)]):
                run_command(cmd)
        except (OSError, subprocess.SubprocessError) as e:
            raise WGSExtractError(f"DISCVRSeq VariantQC failed: {e}") from e

    if not out_html.is_file():
        raise WGSExtractError(f"DISCVRSeq VariantQC produced no report at {out_html}.")

    current_result().add_output(out_html, "variantqc_report", kind="html")
    current_result().add_metrics(
        reference_model=detection.describe(),
        vcfs=len(all_vcfs),
        report=out_html.name,
    )
    human_output(f"VariantQC report: {out_html}")


def _vcf_samples(vcf_path: Path) -> list[str]:
    res = run_command(["bcftools", "query", "-l", str(vcf_path)], capture_output=True)
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _pedigree_gender(vcf_path: Path) -> str:
    workspace = sample_workspace.workspace_for(str(vcf_path))
    if not workspace:
        return "U"
    sex = workspace.overrides(str(vcf_path)).get("sex")
    if not sex:
        fact = workspace.detected("sex", str(vcf_path))
        sex = str(fact.value) if fact else None
    if isinstance(sex, str):
        lowered = sex.strip().lower()
        if lowered.startswith("m"):
            return "M"
        if lowered.startswith("f"):
            return "F"
    return "U"


def _pedigree_has_parents(pedigree_path: Path) -> bool:
    try:
        data = json.loads(pedigree_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return any(
                isinstance(entry, dict) and bool(entry.get("parents")) for entry in data
            )
    except (OSError, ValueError):
        pass
    return False


def _summarize_granite_json(path: Path) -> tuple[dict[str, Scalar], list[str]]:
    metrics: dict[str, Scalar] = {}
    lines: list[str] = []
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return metrics, lines
    if not isinstance(payload, dict):
        return metrics, lines

    # Standard granite qcVCF report structure: top-level metric sections keyed by
    # "total variants", "transition-transversion ratio", "heterozygosity ratio",
    # "depth of coverage".
    if "total variants" in payload or "transition-transversion ratio" in payload:
        per_sample: dict[str, dict[str, Scalar]] = {}
        for entry in payload.get("total variants") or []:
            if isinstance(entry, dict) and entry.get("name"):
                bucket = per_sample.setdefault(str(entry["name"]), {})
                for src, dst in (
                    ("total", "total_variants"),
                    ("SNV", "snvs"),
                    ("INS", "insertions"),
                    ("DEL", "deletions"),
                ):
                    if isinstance(entry.get(src), (int, float)):
                        bucket[dst] = entry[src]
        for entry in payload.get("transition-transversion ratio") or []:
            if (
                isinstance(entry, dict)
                and entry.get("name")
                and isinstance(entry.get("ratio"), (int, float))
            ):
                per_sample.setdefault(str(entry["name"]), {})["ti_tv"] = entry["ratio"]
        het_hom = payload.get("heterozygosity ratio")
        if isinstance(het_hom, dict):
            for entry in het_hom.get("SNV") or []:
                if (
                    isinstance(entry, dict)
                    and entry.get("name")
                    and isinstance(entry.get("ratio"), (int, float))
                ):
                    per_sample.setdefault(str(entry["name"]), {})["het_hom"] = entry[
                        "ratio"
                    ]
        for entry in payload.get("depth of coverage") or []:
            if isinstance(entry, dict) and entry.get("name"):
                for key, val in entry.items():
                    if (
                        key != "name"
                        and isinstance(val, dict)
                        and isinstance(val.get("average"), (int, float))
                    ):
                        per_sample.setdefault(str(entry["name"]), {})["mean_dp"] = val[
                            "average"
                        ]
                        break
        for sample_name, vals in per_sample.items():
            lines.append(f"Sample: {sample_name}")
            for key, value in vals.items():
                lines.append(f"  {key:<24}{value}")
                if len(per_sample) == 1:
                    metrics[key] = value
        return metrics, lines

    for sample_name, body in payload.items():
        if not isinstance(body, dict):
            continue
        lines.append(f"Sample: {sample_name}")
        for key, value in body.items():
            if isinstance(value, (int, float, str, bool)):
                lines.append(f"  {key:<24}{value}")
                if len(payload) == 1:
                    metrics[key] = value
            elif isinstance(value, dict):
                summary = ", ".join(
                    f"{k}={v}"
                    for k, v in value.items()
                    if isinstance(v, (int, float, str))
                )
                if summary:
                    lines.append(f"  {key:<24}{summary}")
    return metrics, lines


def _run_granite_with_diploid_fallback(
    cmd: list[str], vcf_path: Path, out_json: Path, tmp_dir: str
) -> None:
    """Run granite qcVCF, retrying without haploid GT records if granite rejects them."""
    res = run_command(cmd, capture_output=True, check=False)
    if res.returncode == 0:
        return
    combined = f"{res.stdout or ''}\n{res.stderr or ''}"
    if "not enough values to unpack" in combined and "GT.split" in combined:
        diploid_vcf = Path(tmp_dir, "diploid_only.vcf.gz")
        run_command(
            [
                "bcftools",
                "view",
                "-e",
                'GT="hap"',
                "-Oz",
                "-o",
                str(diploid_vcf),
                str(vcf_path),
            ]
        )
        check = run_command(
            ["bcftools", "view", "-H", str(diploid_vcf)],
            capture_output=True,
            check=False,
        )
        if not (check.stdout or "").strip():
            raise WGSExtractError(
                f"{vcf_path.name} contains only haploid genotypes (such as chrM or chrY); "
                "granite qcVCF requires diploid genotypes (e.g. an autosomal or whole-genome VCF)."
            )
        warn(
            "granite_haploid_skipped",
            f"Excluded haploid genotype records (e.g. chrM/chrY/hemizygous X) from "
            f"{vcf_path.name} before running granite qcVCF, which requires diploid genotypes.",
        )
        retry_cmd = [
            str(diploid_vcf) if part == str(vcf_path) else part for part in cmd
        ]
        run_command(retry_cmd)
        return
    raise subprocess.CalledProcessError(
        res.returncode, cmd, output=res.stdout, stderr=res.stderr
    )


def run_granite_qc(params: VcfGraniteQcParams) -> None:
    """Run granite qcVCF for single-sample or family VCF quality control metrics."""
    input_file = require_vcf_input(params)
    verify_paths_exist({"--vcf-input": input_file})
    verify_dependencies(["bcftools"])
    log_dependency_info(["bcftools"])

    vcf_path = Path(absolute_path(input_file))
    current_result().add_input(
        str(vcf_path), "vcf_input" if params.vcf_input else "input"
    )

    explicit_granite = os.fspath(params.granite_path) if params.granite_path else None
    granite_cmd = _granite_argv(explicit_granite)

    samples = (
        [s.strip() for s in params.samples.split(",") if s.strip()]
        if params.samples
        else _vcf_samples(vcf_path)
    )
    if not samples:
        raise UsageError(
            f"{vcf_path.name} has no sample columns; granite qcVCF requires at least one sample."
        )

    outdir = Path(os.fspath(params.outdir)) if params.outdir else vcf_path.parent
    outdir.mkdir(parents=True, exist_ok=True)
    out_json = outdir / f"{_vcf_stem(vcf_path)}.granite_qc.json"

    detection = identify_vcf_reference(vcf_path)
    if detection.build:
        ref_lib = resolve_reference_library(None)
        lib = ReferenceLibrary(ref_lib, detection.signature, input_path=str(vcf_path))
        if lib.fasta and Path(lib.fasta).is_file():
            current_result().set_reference(lib.fasta, detection.build)

    with tempfile.TemporaryDirectory(prefix="wgse_granite_") as tmp:
        if params.pedigree:
            ped_path = Path(os.fspath(params.pedigree))
            verify_paths_exist({"--pedigree": str(ped_path)})
        else:
            gender = _pedigree_gender(vcf_path) if len(samples) == 1 else "U"
            pedigree = [
                {
                    "individual": sample,
                    "sample_name": sample,
                    "gender": gender,
                    "parents": [],
                }
                for sample in samples
            ]
            ped_path = Path(tmp, "pedigree.json")
            ped_path.write_text(
                json.dumps(pedigree, indent=2), encoding="utf-8", newline="\n"
            )

        cmd = [
            *granite_cmd,
            "qcVCF",
            "-i",
            str(vcf_path),
            "-o",
            str(out_json),
            "-p",
            str(ped_path),
            "--samples",
            *samples,
            "--ti_tv",
            "--het_hom",
        ]
        if params.trio_errors or _pedigree_has_parents(ped_path):
            cmd.append("--trio_errors")

        logging.info("Running granite qcVCF on %s -> %s", vcf_path.name, out_json)
        try:
            with cleanup_files(on_failure=[str(out_json)]):
                _run_granite_with_diploid_fallback(cmd, vcf_path, out_json, tmp)
        except (OSError, subprocess.SubprocessError) as e:
            raise WGSExtractError(f"granite qcVCF failed: {e}") from e

    if not out_json.is_file():
        raise WGSExtractError(f"granite qcVCF produced no report at {out_json}.")

    metrics, summary_lines = _summarize_granite_json(out_json)
    current_result().add_output(out_json, "granite_qc_json", kind="json")
    current_result().add_metrics(
        reference_model=detection.describe(),
        samples=len(samples),
        **metrics,
    )
    human_output(f"granite qcVCF report: {out_json}")
    for line in summary_lines:
        human_output(line)


cmd_variantqc = vcf_input_handler(VcfVariantQcParams, run_variantqc)
cmd_granite_qc = vcf_input_handler(VcfGraniteQcParams, run_granite_qc)
