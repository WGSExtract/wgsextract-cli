"""Import one tissue from the pinned GTEx v10 significant-pair archive."""

import csv
import gzip
import math
import sqlite3
import tarfile
from collections.abc import Callable
from pathlib import Path

from wgsextract_cli.core.download_progress import CancelEvent
from wgsextract_cli.core.utils import UsageError, WGSExtractError

LABEL = "GTEx v10 significant eQTL pairs"
TSV_COLUMNS = (
    "pval_nominal", "slope", "slope_se", "pval_nominal_threshold", "passes_threshold",
)  # fmt: skip
_COLUMNS = ["variant_id", "gene_id", "pval_nominal", "slope", "slope_se"]
_ARCHIVE_ROOT = "GTEx_Analysis_v10_eQTL_updated"
_TISSUES = tuple(
    """
    Adipose_Subcutaneous Adipose_Visceral_Omentum Adrenal_Gland
    Artery_Aorta Artery_Coronary Artery_Tibial Bladder
    Brain_Amygdala Brain_Anterior_cingulate_cortex_BA24 Brain_Caudate_basal_ganglia
    Brain_Cerebellar_Hemisphere Brain_Cerebellum Brain_Cortex Brain_Frontal_Cortex_BA9
    Brain_Hippocampus Brain_Hypothalamus Brain_Nucleus_accumbens_basal_ganglia
    Brain_Putamen_basal_ganglia Brain_Spinal_cord_cervical_c-1 Brain_Substantia_nigra
    Breast_Mammary_Tissue Cells_Cultured_fibroblasts Cells_EBV-transformed_lymphocytes
    Colon_Sigmoid Colon_Transverse Esophagus_Gastroesophageal_Junction Esophagus_Mucosa
    Esophagus_Muscularis Heart_Atrial_Appendage Heart_Left_Ventricle Kidney_Cortex
    Liver Lung Minor_Salivary_Gland Muscle_Skeletal Nerve_Tibial Ovary Pancreas
    Pituitary Prostate Skin_Not_Sun_Exposed_Suprapubic Skin_Sun_Exposed_Lower_leg
    Small_Intestine_Terminal_Ileum Spleen Stomach Testis Thyroid Uterus Vagina Whole_Blood
    """.split()
)


def validate_tissue(tissue: str | None) -> None:
    if tissue is not None and tissue not in _TISSUES:
        raise UsageError(
            f"Unknown GTEx v10 tissue {tissue!r}. Valid IDs: {', '.join(_TISSUES)}"
        )


def _check_cancel(cancel: CancelEvent | None) -> None:
    if cancel and cancel.is_set():
        raise WGSExtractError("Cancelled.")


def _extract(
    archive: Path, tissue: str, cancel: CancelEvent | None
) -> tuple[Path, Path]:
    pairs = f"{tissue}.v10.eQTLs.signif_pairs.parquet"
    egenes = f"{tissue}.v10.eGenes.txt.gz"
    expected = {
        f"{_ARCHIVE_ROOT}/{name}": archive.parent / name for name in (pairs, egenes)
    }
    with tarfile.open(archive, "r|gz") as bundle:
        for member in bundle:
            _check_cancel(cancel)
            if member.name not in expected:
                continue
            if not member.isfile():
                raise ValueError(f"{member.name} is not a regular archive member")
            target = expected[member.name]
            stream = bundle.extractfile(member)
            assert stream is not None
            with stream, target.open("xb") as destination:
                while chunk := stream.read(1 << 20):
                    _check_cancel(cancel)
                    destination.write(chunk)
    if not all(path.is_file() for path in expected.values()):
        raise ValueError(f"missing significant pairs or eGenes for {tissue}")
    return archive.parent / pairs, archive.parent / egenes


def _gene_thresholds(egenes: Path) -> dict[str, tuple[str, float | None]]:
    thresholds: dict[str, tuple[str, float | None]] = {}
    with gzip.open(egenes, "rt", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if not {"gene_id", "gene_name", "pval_nominal_threshold"}.issubset(
            reader.fieldnames or ()
        ):
            raise ValueError(
                "eGenes is missing gene ID, gene name or nominal threshold"
            )
        for row in reader:
            gene, name, text = (
                row[key] for key in ("gene_id", "gene_name", "pval_nominal_threshold")
            )
            threshold = None if text == "NA" else float(text)
            if (
                not gene
                or gene in thresholds
                or threshold is not None
                and not 0 <= threshold <= 1
            ):
                raise ValueError(f"invalid or duplicate eGenes threshold for {gene!r}")
            thresholds[gene] = name, threshold
    return thresholds


def load(
    archive: Path,
    db: sqlite3.Connection,
    tissue: str,
    schema: int,
    cancel: CancelEvent | None,
    parse_variant: Callable[[str], tuple[str, int, str, str]],
) -> None:
    """Use eGenes thresholds for the same versioned gene ID and tissue."""
    from pyarrow.parquet import ParquetFile

    pairs, egenes = _extract(archive, tissue, cancel)
    thresholds = _gene_thresholds(egenes)
    db.executescript(
        """
        CREATE TABLE association (
            chrom TEXT, pos INTEGER, ref TEXT, alt TEXT, ordinal INTEGER,
            tissue TEXT, gene TEXT, gene_name TEXT, pval_nominal REAL, slope REAL,
            slope_se REAL, pval_nominal_threshold REAL, passes_threshold INTEGER,
            PRIMARY KEY (chrom, pos, ref, alt, ordinal)
        ) WITHOUT ROWID;
        """
    )
    db.execute(f"PRAGMA user_version={schema}")
    db.execute("PRAGMA cache_size=-131072")
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    ordinal = 0
    with ParquetFile(pairs) as parquet:
        if not set(_COLUMNS).issubset(parquet.schema_arrow.names):
            raise ValueError("significant-pair Parquet is missing required columns")
        for batch in parquet.iter_batches(columns=_COLUMNS, use_threads=False):
            _check_cancel(cancel)
            rows = []
            columns = (column.to_pylist() for column in batch.columns)
            for variant, gene, pval, slope, se in zip(*columns, strict=True):
                entry = thresholds.get(gene)
                if entry is None or entry[1] is None:
                    raise ValueError(f"no eGenes threshold for {gene!r} in {tissue}")
                name, threshold = entry
                if not (
                    0 <= pval <= 1
                    and math.isfinite(slope)
                    and math.isfinite(se)
                    and se >= 0
                ):
                    raise ValueError(
                        f"invalid p-value or slope statistics for {variant}, {gene}"
                    )
                rows.append(
                    (*parse_variant(variant), ordinal, tissue, gene, name, pval,
                     slope, se, threshold, pval <= threshold)
                )  # fmt: skip
                ordinal += 1
            db.executemany(
                "INSERT INTO association VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
            )
    if not ordinal:
        raise ValueError(f"no significant pairs for {tissue}")
    db.commit()
