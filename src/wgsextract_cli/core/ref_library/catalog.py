from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from wgsextract_cli.core.paths import absolute_path

_GENOME_DATA_CACHE: list[dict[str, Any]] = []


def load_genomes_from_csv(csv_path: str) -> list[dict[str, Any]]:
    if not Path(csv_path).exists():
        return []
    genomes = []
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                genomes.append(
                    {
                        "code": row.get("Pyth Code"),
                        "source": row.get("Source"),
                        "final": row.get("Final File Name"),
                        "url": row.get("URL"),
                        "label": row.get("Library Menu Label"),
                        "description": row.get("Description", ""),
                        "md5": row.get("Signature") or "",
                        # SHA-256 of the file at url, when pinned (our mirrors).
                        "sha256": (row.get("SHA256") or "").strip().lower(),
                    }
                )
    except (OSError, csv.Error) as e:
        logging.error(f"Error reading {csv_path}: {e}")
    return genomes


# Fallback when the catalog is missing: our GitHub copies (#701). "md5" is the WGS Extract sequence
# dictionary signature (MD5 of the sorted SN/LN pairs, as calculate_bam_md5
# computes for a BAM header), not a checksum of the file bytes; the catalog
# has it in its Signature column.
FALLBACK_GENOMES: list[dict[str, Any]] = [
    {
        "code": "hg38",
        "source": "GitHub",
        "final": "hg38.fa.gz",
        "url": "https://github.com/WGSExtract/wgsextract-cli/releases/download/reference-genomes-1/hg38.fa.gz",
        "label": "hg38 (Nebula) (NIH) (Rec)",
        "description": "hg38 No Alt (1K Genome; @US NIH)",
        "md5": "bd894134bddba260df88a90123a2ee9c",
        "sha256": "362592f9bfeda5e761db0ccf6d8fca9988e2a6a4e22071985e4c86e1a53f7b75",
    },
    {
        "code": "hg19",
        "source": "GitHub",
        "final": "hg19.fa.gz",
        "url": "https://github.com/WGSExtract/wgsextract-cli/releases/download/reference-genomes-1/hg19.fa.gz",
        "label": "hg19 (yoruba; early Dante)",
        "description": "hg19 Yoruba (@UCSC; used by Dante in 2018)",
        "md5": "ee4efe40ebd6f9468dab89963dcc5b65",
        "sha256": "88c00cae833c581536889748a9eb66f20a3ca6fac0323aec68304a6b3a13adff",
    },
    {
        "code": "T2Tv20",
        "source": "GitHub",
        "final": "chm13v2.0.fa.gz",
        "url": "https://github.com/WGSExtract/wgsextract-cli/releases/download/reference-genomes-1/chm13v2.0.fa.gz",
        "label": "T2T_v2.0 (PGP/HPP chrN) (Rec)",
        "description": "T2T v2.0 (chm13 v1.1; HG002 Y v2.7; UCSC SN; @AWS)",
        "md5": "ed9f80c8e04c461cba35dd1b85a2e2e8",
        "sha256": "f274b0ee8bc1bca18d231d0eb80d764650d7f02d1fe07c43819e21168224304d",
    },
    {
        "code": "hs37d5",
        "source": "GitHub",
        "final": "hs37d5.fa.gz",
        "url": "https://github.com/WGSExtract/wgsextract-cli/releases/download/reference-genomes-1/hs37d5.fa.gz",
        "label": "hs37d5 (Dante) (NIH) (Rec)",
        "description": "hs37d5 (1K Genome; @US NIH)",
        "md5": "92c90b84dac3d9d8402bf2f2dbe95961",
        "sha256": "97aaa5476c28aec58c4f0e1f561a76cb47fb2157c4e9d5848cdc9700e08f8159",
    },
]


# Pet genomes, added to the catalog when it doesn't list them.
PET_GENOMES: list[dict[str, Any]] = [
    {
        "code": "UU_Cfam_GSD_1.0",
        "source": "GitHub",
        "final": "GCF_011100685.1_UU_Cfam_GSD_1.0_genomic.fna.gz",
        "url": "https://github.com/WGSExtract/wgsextract-cli/releases/download/reference-genomes-1/GCF_011100685.1_UU_Cfam_GSD_1.0_genomic.fna.gz",
        "label": "Dog (Canis lupus familiaris) UU_Cfam_GSD_1.0 (NCBI)",
        "description": "Dog reference genome (Canis lupus familiaris; @NCBI UU_Cfam_GSD_1.0)",
        "md5": "567b0b7efc021c69f64fe63d85dbc797",
        "sha256": "a0155bdf0166d4ef89d1f013ed300e79d4e2b26db5c9eee942a14b72e408b98a",
    },
    {
        "code": "Fca126_mat1.0",
        "source": "GitHub",
        "final": "GCF_018350175.1_F.catus_Fca126_mat1.0_genomic.fna.gz",
        "url": "https://github.com/WGSExtract/wgsextract-cli/releases/download/reference-genomes-1/GCF_018350175.1_F.catus_Fca126_mat1.0_genomic.fna.gz",
        "label": "Cat (Felis catus) Fca126_mat1.0 (NCBI)",
        "description": "Cat reference genome (Felis catus; @NCBI Fca126_mat1.0)",
        "md5": "492b4726909f048464fdd00232f63f47",
        "sha256": "757c1dacf231e7cf0b4b11ef48988fcab1c25e68352a3c5f36a1d4424c23ab98",
    },
]


def get_available_genomes() -> list[dict[str, Any]]:
    global _GENOME_DATA_CACHE
    if _GENOME_DATA_CACHE:
        return _GENOME_DATA_CACHE

    cli_root = absolute_path(__file__).parents[2]
    csv_path = cli_root / "assets" / "reference" / "seed_genomes.csv"

    if Path(csv_path).exists():
        _GENOME_DATA_CACHE = load_genomes_from_csv(str(csv_path))

    if not _GENOME_DATA_CACHE:
        _GENOME_DATA_CACHE = [dict(g) for g in FALLBACK_GENOMES]

    # Append pet genomes if not already present
    for pg in PET_GENOMES:
        if not any(g["final"] == pg["final"] for g in _GENOME_DATA_CACHE):
            _GENOME_DATA_CACHE.append(dict(pg))

    return _GENOME_DATA_CACHE


def get_genome_status(final_name: str, reflib_dir: str) -> str:
    """Returns 'installed', 'unindexed', 'incomplete', or 'missing'."""
    if not reflib_dir:
        return "missing"
    from wgsextract_cli.core.reference_assets import reference_genomes_dir

    target_dir = reference_genomes_dir(reflib_dir)
    final_path = str(Path(target_dir, final_name))
    partial_path = final_path + ".partial"
    fai_path = final_path + ".fai"

    if Path(final_path).exists():
        if Path(fai_path).exists():
            return "installed"
        else:
            return "unindexed"
    if Path(partial_path).exists():
        return "incomplete"
    return "missing"


def catalog_genome_for(header: str, signature: str) -> dict[str, Any] | None:
    """The catalog genome an alignment was made against: the one whose
    sequence-dictionary signature matches, else the FASTA its header names."""
    from wgsextract_cli.core.reference_models import identify_reference

    genomes = get_available_genomes()
    genome = next((g for g in genomes if g["md5"] == signature), None)
    if genome:
        return genome
    model = identify_reference(header, signature)
    if not model or not model.fasta:
        return None
    return next(
        (
            g
            for g in genomes
            if g["final"] == model.fasta or g["url"].rsplit("/", 1)[-1] == model.fasta
        ),
        None,
    )
