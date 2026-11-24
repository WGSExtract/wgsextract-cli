"""Keep mutserve's mtDNA heteroplasmy evidence for ``report`` (#1175).

``lineage mt-haplogroup --contamination`` on a BAM/CRAM calls chrM with
mutserve. Its calls (VCF, with each call's level) and its per-position read
table (``--write-raw``: depth and base fractions on each strand) are kept,
unchanged, in the coordinates of the mtDNA reference the reads were called
against; ``details.heteroplasmy`` records that reference, mutserve's version
and settings, and core/mt_heteroplasmy.py reads them.
"""

from __future__ import annotations

import csv
import gzip
import shutil
from pathlib import Path

from wgsextract_cli.core import mt_heteroplasmy as mt
from wgsextract_cli.core.ref_library.mitochondrial import (
    CHM13_CHRM,
    fasta_mito,
    sequence_md5,
)
from wgsextract_cli.core.results import Scalar, current_result
from wgsextract_cli.core.temp_directory import move_file

CALLS = "mutserve_heteroplasmies.vcf.gz"
SITE_EVIDENCE = "mutserve_heteroplasmies.raw.txt.gz"
LENGTH = 16569


def mutserve_settings() -> list[str]:
    """mutserve's call settings: haplocheck's workflow (level 1%, mapQ 30,
    baseQ 20), deletions, and the raw table the report reads."""
    return [
        "--level",
        str(mt.EVIDENCE_LEVEL),
        "--mapQ",
        str(mt.MAPQ),
        "--baseQ",
        str(mt.BASEQ),
        "--deletions",
        "--write-raw",
    ]


def raw_name(calls: str) -> str:
    """mutserve names its raw table after the output path up to its first dot."""
    return f"{calls.split('.', 1)[0]}_raw.txt"


def keep(
    tmp: Path,
    calls: str,
    outdir: Path,
    *,
    origin: str | None,
    contig: str,
    fasta: str,
    version: str | None,
) -> tuple[dict[str, object], list[tuple[Path, str]]]:
    """Move mutserve's outputs from ``tmp`` into ``outdir`` and record their
    metrics; returns ``details.heteroplasmy`` and the outputs with their roles
    (added after the haplogroup report, which names the run's manifest)."""
    calls_out, raw_out = outdir / CALLS, outdir / SITE_EVIDENCE
    move_file(tmp / calls, calls_out)
    counts = mt.count_calls(mt.read_calls(calls_out))
    known = origin if origin in mt.ASSESSABLE_ORIGINS else None
    metrics: dict[str, Scalar] = {
        "mt_homoplasmies": counts["homoplasmies"],
        "mt_heteroplasmies": counts["heteroplasmies"],
    }
    outputs = [(calls_out, mt.CALLS_ROLE)]
    raw_source, compressed = tmp / raw_name(calls), tmp / SITE_EVIDENCE
    try:
        summary = mt.depth_summary(mt.read_reads(raw_source), LENGTH)
        # mtime 0: the same table compresses to the same bytes.
        with (
            raw_source.open("rb") as source,
            gzip.GzipFile(compressed, "wb", mtime=0) as target,
        ):
            shutil.copyfileobj(source, target)
    except (OSError, ValueError, KeyError, OverflowError, csv.Error):
        current_result().warn(
            "mt_evidence_missing",
            "mutserve's per-position read table is missing or unreadable; "
            "kept the calls, but reviewed variants cannot be assessed.",
        )
    else:
        move_file(compressed, raw_out)
        outputs.append((raw_out, mt.SITE_EVIDENCE_ROLE))
        metrics.update(
            mt_median_depth=summary["median_depth"],
            mt_min_depth_fraction=round(summary["min_depth_fraction"], 4),
        )
    current_result().add_metrics(**metrics)
    sequence = fasta_mito(fasta, contig)
    details: dict[str, object] = {
        "caller": "mutserve",
        "version": version,
        "settings": " ".join(mutserve_settings()),
        "mt_reference": {
            "origin": known,
            "contig": contig,
            "md5": sequence_md5(sequence) if sequence else None,
            "coordinates": "CHM13v2.0 chrM" if known == CHM13_CHRM else known,
        },
        **metrics,
    }
    return details, outputs
