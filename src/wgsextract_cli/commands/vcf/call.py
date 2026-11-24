"""``vcf call``: call several variant types in one run, optionally merged."""

from __future__ import annotations

import dataclasses
import logging
import tempfile
from pathlib import Path
from typing import Any

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._params import with_config_defaults
from wgsextract_cli.api.vcf import (
    VcfCallParams,
    VcfCnvParams,
    VcfIndelParams,
    VcfSnpParams,
    VcfSvParams,
    call_types,
)
from wgsextract_cli.core.atomic_output import safe_output
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_files import ensure_vcf_indexed

from .basic import record_variants, run_small_variants
from .structural import _sv_caller as sv_caller
from .structural import mixed_call_region, run_cnv, run_sv, validate_pbsv_region

#: The output role of each type's VCF, as its own command records it.
TYPE_ROLES = {
    "snp": "snps",
    "indel": "indels",
    "sv": "structural_variants",
    "cnv": "copy_number_variants",
}
MERGED_NAME = "variants.vcf.gz"
MERGED_GVCF_NAME = "variants.g.vcf.gz"


def _step_params(
    params: VcfCallParams, kind: str, *, gvcf: bool = False
) -> VcfSnpParams | VcfIndelParams | VcfSvParams | VcfCnvParams:
    """The params of one type's own command. Settings ``vcf call`` doesn't
    take (mimalloc, parallel) come from config.toml, as they would there; the
    ones it passes on are already resolved, so config doesn't override them."""
    common: dict[str, Any] = {
        "input": params.input,
        "ref": params.ref,
        "outdir": params.outdir,
        "threads": params.threads,
        "region": params.region,
        "qc_min_depth": params.qc_min_depth,
    }
    step: VcfSnpParams | VcfIndelParams | VcfSvParams | VcfCnvParams
    if kind in ("snp", "indel"):
        cls = VcfSnpParams if kind == "snp" else VcfIndelParams
        common["ploidy"] = params.ploidy
        common["reference_evidence"] = "gvcf" if gvcf else "none"
        step = cls(**common)
    elif kind == "sv":
        common.update(
            caller=params.sv_caller,
            pacbio=params.pacbio,
            ont=params.ont,
            long_read=params.long_read,
            ccs=params.ccs,
            tandem_repeats=params.tandem_repeats,
        )
        step = VcfSvParams(**common)
    else:
        common["ploidy"] = params.cnv_ploidy
        step = VcfCnvParams(**common)
    return with_config_defaults(step, keep=common)


def _run_step(
    params: VcfCallParams, kind: str, *, gvcf: bool, variants: bool = True
) -> None:
    """Run one type as its own command would."""
    step = _step_params(params, kind, gvcf=gvcf)
    if isinstance(step, VcfSnpParams | VcfIndelParams):
        run_small_variants(step, variants=variants)
    elif isinstance(step, VcfSvParams):
        run_sv(step)
    else:
        run_cnv(step)


def _preflight(params: VcfCallParams, types: list[str]) -> None:
    """Refuse SV and CNV calling that can't run before any type is called,
    so a missing caller or a bad SV flag doesn't surface hours in."""
    tools: list[str] = []
    if "sv" in types:
        sv = _step_params(params, "sv")
        assert isinstance(sv, VcfSvParams)
        caller, _reason, _hifi = sv_caller(sv)
        if caller == "pbsv":
            validate_pbsv_region(sv.region)
        tools.append(caller)
    if "cnv" in types:
        tools.append("delly")
    if tools:
        verify_dependencies([*dict.fromkeys(tools), "bcftools", "tabix", "samtools"])


def _output_for(role: str) -> str:
    for record in reversed(current_result().outputs):
        if record.role == role:
            return record.path
    raise WGSExtractError(f"The {role.replace('_', ' ')} step wrote no VCF.")


def _samples(vcf: str) -> list[str]:
    result = run_command(["bcftools", "query", "-l", vcf], capture_output=True)
    return [line for line in result.stdout.splitlines() if line]


def merge_vcfs(vcfs: list[str], out_vcf: str) -> None:
    """Join single-sample VCFs from different callers into one, sorted by
    position. A part whose sample is named differently (Sniffles names it
    after the file) is renamed to the first part's sample."""
    verify_dependencies(["bcftools", "tabix"])
    sample = None
    with tempfile.TemporaryDirectory(dir=Path(out_vcf).parent) as work:
        parts: list[str] = []
        for index, vcf in enumerate(vcfs):
            names = _samples(vcf)
            if len(names) != 1:
                raise WGSExtractError(
                    f"Can't merge {vcf}: it has {len(names)} samples, not one."
                )
            if sample is None:
                sample = names[0]
            elif names[0] != sample:
                renamed = str(Path(work, f"part{index}.vcf.gz"))
                samples_file = Path(work, f"part{index}.samples")
                samples_file.write_text(f"{sample}\n", encoding="utf-8", newline="\n")
                run_command(
                    ["bcftools", "reheader", "-s", str(samples_file)]
                    + ["-o", renamed, vcf]
                )
                ensure_vcf_indexed(renamed)
                vcf = renamed
            parts.append(vcf)
        with safe_output(out_vcf) as staged:
            if len(parts) == 1:
                run_command(["bcftools", "view", "-Oz", "-o", staged, parts[0]])
            else:
                # contract: --allow-overlaps interleaves the parts by position,
                # keeping every record, so the merge is coordinate-sorted.
                run_command(
                    ["bcftools", "concat", "--allow-overlaps", "--no-version"]
                    + ["-Oz", "-o", staged, *parts]
                )
    ensure_vcf_indexed(out_vcf)


def run_call(params: VcfCallParams) -> None:
    """``vcf call``: each chosen type as its own command would, then the merge.

    With the gVCF, one bcftools pass writes it in place of the SNP and indel
    VCFs, since it holds both. With ``merge`` the parts are removed once
    they are joined, leaving the merged file."""
    types = call_types(params.types)
    _preflight(params, types)
    small = [kind for kind in types if kind in ("snp", "indel")]
    small_params = params
    if small and params.region is not None and ("sv" in types or "cnv" in types):
        small_params = dataclasses.replace(
            params,
            region=mixed_call_region(
                str(params.input),
                str(params.ref) if params.ref else None,
                params.region,
            ),
        )
    gvcf = params.reference_evidence == "gvcf"
    parts: list[str] = []
    if small and gvcf:
        logging.info("vcf call: writing the SNP and indel gVCF")
        _run_step(small_params, small[0], gvcf=True, variants=False)
        parts.append(_output_for("reference_evidence"))
    for kind in types:
        if kind in small and gvcf:
            continue
        logging.info(f"vcf call: calling {kind}")
        _run_step(small_params if kind in small else params, kind, gvcf=False)
        parts.append(_output_for(TYPE_ROLES[kind]))
    # Each step set the run's variant count to its own; the run's is their sum,
    # unknown when a count is: the gVCF, whose records include reference
    # blocks, has none, so with it the SNP and indel counts are unknown.
    counts = [_variant_count(TYPE_ROLES[kind]) for kind in types]
    known = [count for count in counts if count is not None]
    total = sum(known) if len(known) == len(counts) else None
    current_result().add_metrics(types=",".join(types), variants=total)
    if params.merge and parts:
        outdir = params.outdir or Path(params.input).absolute().parent
        name = MERGED_GVCF_NAME if small and gvcf else MERGED_NAME
        out_vcf = str(Path(outdir, name))
        logging.info(f"vcf call: merging {len(parts)} VCFs into {out_vcf}")
        merge_vcfs(parts, out_vcf)
        _remove_parts(parts)
        record_variants(out_vcf, "merged_variants", None if small and gvcf else total)
        # The merge is made from the parts (which the manifest keeps as
        # discarded temporary files rather than deliverables), called from
        # the reads.
        current_result().add_output(
            out_vcf, "merged_variants", sources=[*parts, params.input]
        )


def _variant_count(role: str) -> int | None:
    for record in current_result().outputs:
        if record.role == role:
            count = record.metrics.get("variants")
            return count if isinstance(count, int) else None
    return None


def _remove_parts(parts: list[str]) -> None:
    """Delete the merged parts and their indexes; the result keeps them as
    discarded temporary files, not outputs."""
    result = current_result()
    for part in parts:
        for path in (part, part + ".tbi", part + ".csi"):
            result.discard_output(path)
            Path(path).unlink(missing_ok=True)


cmd_call = cli_handler(VcfCallParams, run_call)
