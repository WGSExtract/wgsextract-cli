"""Typed chromosome and quality-control table exports for info."""

import csv
import io
import logging
from pathlib import Path
from typing import Any

from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.table_writer import Column, table_path, write_table

CHROMOSOME_TABLE = (
    Column("seq_name", "TEXT", "Contig, or Total, or Other (all other contigs)."),
    Column("model_len", "INTEGER", "Reference length in bases."),
    Column("model_n_len", "INTEGER", "N bases in the reference."),
    Column("mapped_segments", "INTEGER", "Mapped reads (segments)."),
    Column("mapped_gbases", "REAL", "Mapped gigabases: segments x read length.", ".2f"),
    Column("mapped_ard", "INTEGER", "Average read depth over the non-N bases."),
    Column(
        "breadth_pct",
        "REAL",
        "Percentage of non-N bases covered by a read (see the breadth metadata).",
        "g",
    ),
)


def _qc_column(name: str, type_: str, description: str) -> Column:
    return Column(name, type_, description, ".6g" if type_ == "REAL" else "")


# One row: the manifest's info metrics, typed.
QC_TABLE = tuple(
    _qc_column(*column)
    for column in (
        ("reference_model", "TEXT", "Reference model the header matches."),
        ("sequencer", "TEXT", "Sequencer guessed from the read names and header."),
        ("sex", "TEXT", "Sex inferred from chrX/chrY coverage."),
        ("avg_read_len", "REAL", "Mean read length (sampled)."),
        ("is_paired", "BOOLEAN", "Paired-end reads."),
        ("avg_insert_size", "REAL", "Mean insert size (sampled)."),
        ("size_gb", "REAL", "File size in GiB."),
        ("sorted", "BOOLEAN", "Coordinate-sorted."),
        ("indexed", "BOOLEAN", "Has an index."),
        ("duplicates_marked", "BOOLEAN", "Duplicates are flagged."),
        ("duplicate_pct", "REAL", "Percentage of sampled reads flagged duplicate."),
        ("alt_aware", "BOOLEAN", "ALT contigs were mapped ALT-aware."),
        ("ard_mapped", "REAL", "Average read depth of the mapped reads."),
        ("ard_raw", "REAL", "Average read depth of all reads."),
        ("gbases_mapped", "REAL", "Mapped gigabases."),
        ("gbases_raw", "REAL", "Gigabases of all reads."),
        ("reads_mapped_m", "REAL", "Mapped reads, in millions."),
        ("reads_raw_m", "REAL", "All reads, in millions."),
        (
            "reads_mapped_pct",
            "REAL",
            "Mapped records as a percentage of all records; a split long read counts once per record (see mapped_read_pct).",
        ),
        ("read_n50", "REAL", "Long reads' N50 in bp (from the sampled reads)."),
        (
            "mapped_read_pct",
            "REAL",
            "Long reads mapped, as a percentage of reads rather than records.",
        ),
    )
)


def _qc_row(metrics: dict[str, Any]) -> tuple[Any, ...]:
    def value(column: Column) -> Any:
        raw = metrics.get(column.name)
        if raw is None or column.type in ("TEXT", "BOOLEAN"):
            return raw
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    return tuple(value(column) for column in QC_TABLE)


def _chromosome_rows(table_csv: str) -> list[tuple[Any, ...]]:
    """The typed rows of the chromosome table that `--csv` prints."""
    rows = []
    for row in list(csv.reader(io.StringIO(table_csv)))[1:]:
        name, length, n_len, segments, gbases, ard, breadth = row
        percent = breadth.lstrip("~").rstrip("% ").strip()
        rows.append(
            (
                name,
                int(length),
                int(n_len),
                int(segments),
                float(gbases),
                int(float(ard)),
                float(percent) if percent else None,
            )
        )
    return rows


def _export_tables(
    data: dict[str, Any], stem: Path, table_format: str, metadata: dict[str, str]
) -> None:
    """Save the chromosome table and the QC stats as typed tables."""
    _export_chromosomes(
        data, table_path(f"{stem}_chromosomes", table_format), table_format, metadata
    )
    result = current_result()
    qc_path = table_path(f"{stem}_qc", table_format)
    write_table(
        qc_path,
        QC_TABLE,
        [_qc_row(dict(result.metrics))],
        table_format,
        metadata=metadata,
    )
    result.add_output(qc_path, "qc_table", metrics={"rows": 1})


def _export_chromosomes(
    data: dict[str, Any], path: str, table_format: str, metadata: dict[str, str]
) -> None:
    table_csv = data["chrom_table_csv"]
    breadth = (
        "sampled estimate (coverage-sample)"
        if "~" in table_csv
        else "exact (calculate-coverage)"
        if "%" in table_csv
        else "not measured: run info calculate-coverage or coverage-sample"
    )
    rows = _chromosome_rows(table_csv)
    write_table(
        path,
        CHROMOSOME_TABLE,
        rows,
        table_format,
        metadata={**metadata, "breadth": breadth},
    )
    current_result().add_output(path, "chromosome_table", metrics={"rows": len(rows)})
    logging.info(f"Chromosome table saved to {path}")
