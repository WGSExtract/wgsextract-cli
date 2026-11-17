"""Local input discovery for the opt-in real-data smoke suites."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.smoke_utils import REPO_ROOT
from wgsextract_cli.core.config import settings, typed_settings


@dataclass(frozen=True)
class RealDataset:
    bam: Path
    vcf: Path
    ref: Path
    target_tab: Path | None
    fastq_r1: Path | None
    fastq_r2: Path | None
    cram: Path | None
    region: str | None = None


def _existing_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.exists() else None


def _first_existing(paths: list[Path]) -> Path | None:
    return next((path for path in paths if path.exists()), None)


def _first_existing_recursive(root: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        match = _first_existing(sorted(root.rglob(pattern)))
        if match:
            return match
    return None


def _latest_benchmark_dataset() -> Path | None:
    run_roots = [
        REPO_ROOT / "out" / "ci-real-data" / "benchmark" / "runs",
        REPO_ROOT / "out" / "benchmark" / "runs",
    ]
    candidates: list[Path] = []
    for root in run_roots:
        if root.exists():
            candidates.extend(root.glob("*/dataset/real/hg19-mini-hg00096"))
    existing = [path for path in candidates if path.is_dir()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def dataset_from_options(config: pytest.Config) -> RealDataset | None:
    bam = _existing_path(config.getoption("real_input"))
    vcf = _existing_path(config.getoption("real_vcf"))
    ref = _existing_path(config.getoption("real_ref"))
    if not bam or not vcf or not ref:
        return None
    return RealDataset(
        bam=bam,
        vcf=vcf,
        ref=ref,
        target_tab=_existing_path(config.getoption("real_targets")),
        fastq_r1=_existing_path(config.getoption("real_fastq_r1")),
        fastq_r2=_existing_path(config.getoption("real_fastq_r2")),
        cram=bam if bam.suffix == ".cram" else None,
    )


def dataset_from_config() -> RealDataset | None:
    dev = typed_settings().dev
    if not dev.real_genome_tests_enabled or dev.real_genome_test_path is None:
        return None
    root = _existing_path(dev.real_genome_test_path)
    if not root or not root.is_dir():
        return None
    bam = _first_existing_recursive(root, ["*.bam", "*.cram"])
    vcf = _first_existing_recursive(
        root, ["*.snp-indel.genome.vcf.gz", "filtered.vcf.gz", "*.vcf.gz"]
    )
    ref_value = settings.get("reference_fasta") or settings.get("reference_library")
    ref = _existing_path(str(ref_value)) if ref_value else None
    if not bam or not vcf or not ref:
        return None
    fastqs = sorted(root.rglob("*_R[12].fastq.gz"))
    return RealDataset(
        bam=bam,
        vcf=vcf,
        ref=ref,
        target_tab=_first_existing_recursive(root, ["*.targets.tab.gz"]),
        fastq_r1=fastqs[0] if len(fastqs) >= 2 else None,
        fastq_r2=fastqs[1] if len(fastqs) >= 2 else None,
        cram=bam
        if bam.suffix == ".cram"
        else _first_existing_recursive(root, ["*.cram"]),
    )


def dataset_from_benchmark_cache() -> RealDataset | None:
    dataset_dir = _latest_benchmark_dataset()
    if not dataset_dir:
        return None
    bam = _first_existing(list(dataset_dir.glob("*.bam")))
    vcf = _first_existing(list(dataset_dir.glob("*.vcf.gz")))
    ref = _first_existing(
        list(dataset_dir.glob("*.fa.gz")) + list(dataset_dir.glob("*.fa"))
    )
    if not bam or not vcf or not ref:
        return None
    fastqs = sorted(dataset_dir.glob("*_R[12].fastq.gz"))
    return RealDataset(
        bam=bam,
        vcf=vcf,
        ref=ref,
        target_tab=_first_existing(list(dataset_dir.glob("*.targets.tab.gz"))),
        fastq_r1=fastqs[0] if len(fastqs) >= 2 else None,
        fastq_r2=fastqs[1] if len(fastqs) >= 2 else None,
        cram=_first_existing(list(dataset_dir.glob("*.cram"))),
    )
