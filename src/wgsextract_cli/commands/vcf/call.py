"""``vcf call``: call several variant types in one run, optionally merged."""

from __future__ import annotations

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
from wgsextract_cli.core.output_checks import vcf_record_count
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_files import ensure_vcf_indexed

from .basic import record_variants, run_small_variants
from .structural import run_cnv, run_sv

#: The output role of each type's VCF, as its own command records it.
TYPE_ROLES = {
    "snp": "snps",
    "indel": "indels",
    "sv": "structural_variants",
    "cnv": "copy_number_variants",
}
MERGED_NAME = "variants.vcf.gz"
MERGED_GVCF_NAME = "variants.g.vcf.gz"


def _run_step(
    params: VcfCallParams, kind: str, *, gvcf: bool, variants: bool = True
) -> None:
    """Run one type as its own command would. Settings ``vcf call`` doesn't
    take (mimalloc, parallel) come from config.toml, as they would there."""
    common: dict[str, Any] = {
        "input": params.input,
        "ref": params.ref,
        "outdir": params.outdir,
        "threads": params.threads,
        "region": params.region,
        "qc_min_depth": params.qc_min_depth,
    }
    if kind in ("snp", "indel"):
        cls = VcfSnpParams if kind == "snp" else VcfIndelParams
        small = cls(
            **common,
            ploidy=params.ploidy,
            reference_evidence="gvcf" if gvcf else "none",
        )
        run_small_variants(with_config_defaults(small), variants=variants)
    elif kind == "sv":
        sv = VcfSvParams(
            **common,
            caller=params.sv_caller,
            pacbio=params.pacbio,
            ont=params.ont,
            long_read=params.long_read,
            ccs=params.ccs,
            tandem_repeats=params.tandem_repeats,
        )
        run_sv(with_config_defaults(sv))
    else:
        cnv = VcfCnvParams(**common, ploidy=params.cnv_ploidy)
        run_cnv(with_config_defaults(cnv))


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
                samples_file.write_text(f"{sample}\n", encoding="utf-8")
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
    small = [kind for kind in types if kind in ("snp", "indel")]
    gvcf = params.reference_evidence == "gvcf"
    parts: list[str] = []
    if small and gvcf:
        logging.info("vcf call: writing the SNP and indel gVCF")
        _run_step(params, small[0], gvcf=True, variants=False)
        parts.append(_output_for("reference_evidence"))
    for kind in types:
        if kind in small and gvcf:
            continue
        logging.info(f"vcf call: calling {kind}")
        _run_step(params, kind, gvcf=False)
        parts.append(_output_for(TYPE_ROLES[kind]))
    current_result().add_metrics(types=",".join(types))
    if params.merge and len(parts) > 1:
        outdir = params.outdir or Path(params.input).absolute().parent
        name = MERGED_GVCF_NAME if small and gvcf else MERGED_NAME
        out_vcf = str(Path(outdir, name))
        logging.info(f"vcf call: merging {len(parts)} VCFs into {out_vcf}")
        merge_vcfs(parts, out_vcf)
        record_variants(out_vcf, "merged_variants", vcf_record_count(out_vcf))
        _remove_parts(parts)


def _remove_parts(parts: list[str]) -> None:
    """Delete the merged parts and their indexes, and drop them from the result."""
    removed: set[str] = set()
    for part in parts:
        for path in (part, part + ".tbi", part + ".csi"):
            file = Path(path)
            if file.exists():
                file.unlink()
            removed.add(str(file.expanduser().absolute()))
    result = current_result()
    result.outputs[:] = [r for r in result.outputs if r.path not in removed]


cmd_call = cli_handler(VcfCallParams, run_call)
