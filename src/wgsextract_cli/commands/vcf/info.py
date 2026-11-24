"""`vcf info`: fast summary of a VCF, gVCF, or BCF file (header, build/model, samples, counts)."""

from __future__ import annotations

import gzip
import os
import re
from pathlib import Path
from typing import Any

from wgsextract_cli.api.vcf import VcfInfoParams
from wgsextract_cli.core import sample_workspace
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.download_progress import format_bytes
from wgsextract_cli.core.imputed_vcf import describe as describe_imputation
from wgsextract_cli.core.imputed_vcf import imputation_info
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.reference_assets import (
    is_fasta_path,
    reference_genomes_dir,
    resolve_reference_library,
)
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.core.variant_files import (
    get_file_version,
    verify_paths_exist,
)
from wgsextract_cli.core.vcf_reference import identify_vcf_reference

from .basic import require_vcf_input, vcf_input_handler

_MAX_UNINDEXED_SCAN_BYTES = (
    16 * 1024 * 1024
)  # 16 MiB cap so unindexed info stays instant
_VCF_FORMAT_RE = re.compile(r"^##fileformat=(.+)$")
_SOURCE_RE = re.compile(r"^##source=(.+)$")
_GATK_RE = re.compile(r"^##GATKCommandLine=<ID=([^,>]+)")
_BCFTOOLS_RE = re.compile(r"^##bcftools_([a-zA-Z0-9_]+)Version=([^\s;+]+)")


def _index_kind(vcf_path: Path) -> str | None:
    for suffix in (".tbi", ".csi"):
        if Path(str(vcf_path) + suffix).is_file():
            return suffix
    return None


def _parse_header_metadata(header: str) -> dict[str, Any]:
    fileformat = ""
    sources: list[str] = []
    samples: list[str] = []
    is_gvcf = False
    for line in header.splitlines():
        if line.startswith("#CHROM\t"):
            cols = line.split("\t")
            if len(cols) > 9:
                samples = cols[9:]
            continue
        if not fileformat and (m := _VCF_FORMAT_RE.match(line)):
            fileformat = m.group(1).strip()
        if line.startswith("##ALT=<ID=NON_REF") or line.startswith("##GVCFBlock"):
            is_gvcf = True
        if (
            line.startswith("##INFO=<ID=END,")
            and not is_gvcf
            and "GVCF" in header.upper()
        ):
            is_gvcf = True
        if m := _SOURCE_RE.match(line):
            sources.append(m.group(1).strip())
        elif m := _GATK_RE.match(line):
            sources.append(f"GATK {m.group(1).strip()}")
        elif line.startswith("##DeepVariant_version="):
            sources.append(f"DeepVariant {line.split('=', 1)[1].strip()}")
        elif "Clair3" in line and line.startswith("##"):
            sources.append("Clair3")
        elif m := _BCFTOOLS_RE.match(line):
            sources.append(f"bcftools {m.group(1)} {m.group(2)}")
    unique_sources = list(dict.fromkeys(sources))
    return {
        "fileformat": fileformat or "VCF",
        "sources": unique_sources,
        "samples": samples,
        "is_gvcf": is_gvcf,
    }


def _indexed_counts(vcf_path: str) -> tuple[int | None, dict[str, int], list[str]]:
    """Instant total, per-contig counts, and present contigs from .tbi/.csi index."""
    bcftools = get_tool_path("bcftools")
    if bcftools:
        res = run_command(
            [bcftools, "index", "-s", vcf_path], capture_output=True, check=False
        )
        if res.returncode == 0:
            per_chrom: dict[str, int] = {}
            total = 0
            for line in res.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3 and parts[2].isdigit():
                    count = int(parts[2])
                    per_chrom[parts[0]] = count
                    total += count
            if per_chrom:
                return total, per_chrom, list(per_chrom.keys())
            nres = run_command(
                [bcftools, "index", "-n", vcf_path], capture_output=True, check=False
            )
            if nres.returncode == 0 and nres.stdout.strip().isdigit():
                return int(nres.stdout.strip()), {}, []
    # Older .tbi indexes omit per-contig record counts (bcftools index -s fails and
    # index -n reports 0), but tabix -l still lists every contig present instantly.
    tabix = get_tool_path("tabix")
    if tabix:
        lres = run_command([tabix, "-l", vcf_path], capture_output=True, check=False)
        if lres.returncode == 0:
            chroms = [line.strip() for line in lres.stdout.splitlines() if line.strip()]
            if chroms:
                return None, {}, chroms
    return None, {}, []


def _unindexed_counts(vcf_path: Path) -> tuple[int | None, dict[str, int], list[str]]:
    """Fast exact count for small unindexed VCFs; avoids slow full scans of huge files."""
    if (
        vcf_path.suffix.lower() == ".bcf"
        or vcf_path.stat().st_size > _MAX_UNINDEXED_SCAN_BYTES
    ):
        return None, {}, []
    per_chrom: dict[str, int] = {}
    total = 0
    try:
        opener = gzip.open if vcf_path.suffix.lower() in (".gz", ".bgz") else open
        with opener(vcf_path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line or line.startswith("#"):
                    continue
                chrom = line.split("\t", 1)[0]
                per_chrom[chrom] = per_chrom.get(chrom, 0) + 1
                total += 1
        return total, per_chrom, list(per_chrom.keys())
    except OSError:
        return None, {}, []


def _content_summary(chroms: list[str]) -> str:
    if not chroms:
        return "Unknown"
    canonical = []
    for c in chroms:
        clean = c[3:] if c.lower().startswith("chr") else c
        clean = "M" if clean.upper() in ("M", "MT") else clean.upper()
        canonical.append(clean)
    present = set(canonical)
    buckets = []
    if any(k.isdigit() for k in present):
        buckets.append("Auto")
    if "X" in present:
        buckets.append("X")
    if "Y" in present:
        buckets.append("Y")
    if "M" in present:
        buckets.append("Mito")
    if any(not k.isdigit() and k not in {"X", "Y", "M"} for k in present):
        buckets.append("Other")
    return ", ".join(buckets) if buckets else "Unknown"


def _render_vcf_info(data: dict[str, Any]) -> str:
    lines = [
        f"{'File':<28}{data['filename']}",
        f"{'Reference Genome':<28}{data['reference_model']}",
    ]
    if data.get("md5_signature"):
        lines.append(f"{'MD5 Signature':<28}{data['md5_signature']}")
    samples = data.get("samples") or []
    if samples:
        preview = ", ".join(samples[:8]) + (
            f" (+{len(samples) - 8} more)" if len(samples) > 8 else ""
        )
        lines.append(f"{'Samples':<28}{len(samples)} ({preview})")
    else:
        lines.append(f"{'Samples':<28}0 (sites-only)")
    if data.get("records") is not None:
        n_chroms = len(data.get("chromosomes") or {})
        chrom_note = (
            f" across {n_chroms:,} contig{'s' if n_chroms != 1 else ''}"
            if n_chroms
            else ""
        )
        lines.append(f"{'Variant Records':<28}{data['records']:,}{chrom_note}")
    elif data.get("indexed"):
        lines.append(
            f"{'Variant Records':<28}Counts not stored in {data['index']} index "
            "(re-index with tabix/bcftools or run VCF Stats)"
        )
    else:
        lines.append(
            f"{'Variant Records':<28}Unindexed (index with tabix or run VCF Stats for exact count)"
        )
    if data.get("file_content") and data["file_content"] != "Unknown":
        lines.append(f"{'File Content':<28}{data['file_content']}")
    if data.get("sources"):
        lines.append(f"{'Caller / Source':<28}{', '.join(data['sources'][:4])}")
    if data.get("imputed_by"):
        lines.append(f"{'Imputed By':<28}{data['imputed_by']}")
    idx_label = f"Indexed ({data['index']})" if data.get("index") else "Unindexed"
    lines.append(f"{'File Stats':<28}{idx_label}, {data['size']}")
    fmt = data["fileformat"]
    if data.get("is_gvcf"):
        fmt = f"{fmt} (gVCF)"
    if data.get("container_format") and data["container_format"] != "Unknown":
        fmt = f"{fmt}; {data['container_format']}"
    lines.append(f"{'File Format':<28}{fmt}")
    return "\n".join(lines)


def run_vcf_info(params: VcfInfoParams) -> None:
    input_file = require_vcf_input(params)
    verify_paths_exist({"--vcf-input": input_file})
    verify_dependencies(["bcftools"])
    log_dependency_info(["bcftools"])

    path = Path(absolute_path(input_file))
    current_result().add_input(str(path), "vcf_input" if params.vcf_input else "input")

    ref_arg = os.fspath(params.ref) if params.ref else None
    user_fasta = ref_arg if ref_arg and is_fasta_path(ref_arg) else None
    ref_lib = resolve_reference_library(ref_arg if ref_arg and not user_fasta else None)

    header_res = run_command(
        ["bcftools", "view", "-h", str(path)], capture_output=True, check=False
    )
    header = header_res.stdout if header_res.returncode == 0 else ""

    detection = identify_vcf_reference(
        path,
        ref_lib_dir=ref_lib,
        reference_fasta=user_fasta,
        header=header,
        allow_body_sampling=True,
    )
    if detection.build:
        sample_workspace.record(
            "build",
            str(path),
            detection.build,
            method=detection.evidence or "vcf inspection",
            confidence="high" if detection.evidence.startswith("header") else "medium",
        )

    resolved_ref = user_fasta
    if not resolved_ref and detection.model and detection.model.fasta and ref_lib:
        candidate = Path(reference_genomes_dir(ref_lib)) / detection.model.fasta
        if candidate.is_file():
            resolved_ref = str(candidate)
    if resolved_ref and Path(resolved_ref).is_file():
        current_result().set_reference(resolved_ref, detection.build)

    meta = _parse_header_metadata(header)
    index_suffix = _index_kind(path)
    records, per_chrom, present_chroms = (
        _indexed_counts(str(path)) if index_suffix else _unindexed_counts(path)
    )
    size_bytes = path.stat().st_size
    container_format = get_file_version(str(path))

    imputed = imputation_info(header.splitlines())
    imputed_by = None
    if imputed is not None:
        imputed_by = imputed.get("Tool", "unknown")
        warn(
            "imputed_vcf",
            f"{path.name} is {describe_imputation(imputed)}: its genotypes are "
            "predicted from a reference panel, not read from this sample's DNA, "
            "and rare variants in it are unreliable.",
            tool=imputed.get("Tool"),
            panel=imputed.get("Panel"),
        )

    data: dict[str, Any] = {
        "filename": path.name,
        "reference_model": detection.describe(),
        "reference_build": detection.major_build,
        "reference_build_key": detection.build,
        "reference_evidence": detection.evidence,
        "md5_signature": detection.signature,
        "naming": detection.naming,
        "samples": meta["samples"],
        "sample_count": len(meta["samples"]),
        "records": records,
        "chromosomes": per_chrom,
        "present_contigs": present_chroms,
        "file_content": _content_summary(present_chroms),
        "sources": meta["sources"],
        "fileformat": meta["fileformat"],
        "is_gvcf": meta["is_gvcf"],
        "container_format": container_format,
        "index": index_suffix,
        "indexed": index_suffix is not None,
        "size_bytes": size_bytes,
        "size": format_bytes(size_bytes),
        "imputed_by": imputed_by,
    }

    result = current_result()
    result.set_details(data)
    result.add_metrics(
        reference_model=data["reference_model"],
        major_build=data["reference_build"],
        samples=data["sample_count"],
        records=data["records"],
        indexed=data["indexed"],
        is_gvcf=data["is_gvcf"],
        size_bytes=size_bytes,
    )
    human_output(_render_vcf_info(data))


cmd_vcf_info = vcf_input_handler(VcfInfoParams, run_vcf_info)
