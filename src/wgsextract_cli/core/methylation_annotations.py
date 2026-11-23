"""Build-matched annotations for methylation regions."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from wgsextract_cli.core.network_policy import require_download
from wgsextract_cli.core.ref_library.assets import resolve_reference_library
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.utils import UsageError, WGSExtractError

UCSC_HG38 = (
    "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/database/cpgIslandExt.txt.gz"
)
UCSC_HS1 = "https://hgdownload.soe.ucsc.edu/gbdb/hs1/bbi/cpgIslandExt.bb"
HG38_ISLAND_SHA256 = "2339f8bad0ec9993211287b319b8d59ddeff30a5010505f649817332ed8222f8"
HS1_ISLAND_SHA256 = "13273652c3450c3c639f37c02caa49867526fddee3138176164bf193167cbeda"
TRANSCRIPT_RULE = (
    "MANE Select, then RefSeq Select, then longest exonic transcript; "
    "ties by accession, with unaccessioned transcripts ordered by GFF feature ID"
)
BUILDS = ("hg38", "t2t")
PRESETS = ("promoters", "cpg-islands")


@dataclass(frozen=True)
class Chromosome:
    name: str
    length: int
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Region:
    chrom: str
    start: int | None
    end: int | None
    name: str
    gene_id: str | None = None
    transcript: str | None = None
    strand: str | None = None


def _asset(filename: str) -> bytes:
    return (
        resources.files("wgsextract_cli")
        .joinpath(f"assets/methylation/{filename}")
        .read_bytes()
    )


def chromosomes(build: str) -> tuple[Chromosome, ...]:
    return tuple(
        Chromosome(row[0], row[1], tuple(row[2:]))
        for row in json.loads(_asset(f"{build}-chromosomes.json"))["chromosomes"]
    )


def promoter_regions(
    lines: Iterable[str],
    lengths: Mapping[str, int],
    upstream: int,
    downstream: int,
) -> Iterator[Region]:
    for line in lines:
        chrom, name, gene_id, accession, strand, boundary = line.rstrip("\r\n").split(
            "\t"
        )
        if not accession:
            yield Region(chrom, None, None, name, gene_id)
            continue
        tss = int(boundary)
        left, right = (
            (upstream, downstream) if strand == "+" else (downstream, upstream)
        )
        yield Region(
            chrom,
            max(0, tss - left),
            min(lengths[chrom], tss + right),
            name,
            gene_id,
            accession,
            strand,
        )


def island_regions(lines: Iterable[str], *, ucsc_table: bool) -> Iterator[Region]:
    for line in lines:
        fields = line.rstrip("\r\n").split("\t")
        i = int(ucsc_table)  # SQL dump has the bin column; BED does not.
        chrom, start, end = fields[i : i + 3]
        yield Region(chrom, int(start), int(end), f"{chrom}:{start}-{end}")


def hg38_island_path() -> Path:
    return Path(resolve_reference_library(), "methylation", "hg38-cpgIslandExt.txt.gz")


def _hg38_islands() -> bytes:
    path = hg38_island_path()
    if not path.is_file():
        require_download("UCSC hg38 masked CpG islands (cpgIslandExt)")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not download_file(
            UCSC_HG38,
            str(path),
            expected_sha256=HG38_ISLAND_SHA256,
            resolve_release_digest=False,
            use_cache=False,
            use_mirrors=False,
        ):
            raise WGSExtractError(
                f"Could not download and verify UCSC cpgIslandExt: {path}"
            )
    with path.open("rb") as source:
        data = source.read(
            717_985
        )  # Pinned snapshot size + one to detect trailing bytes.
    if hashlib.sha256(data).hexdigest() != HG38_ISLAND_SHA256:
        raise UsageError(
            f"Checksum mismatch for cached UCSC hg38 cpgIslandExt: {path}. "
            "Move the file aside and rerun with --download."
        )
    return data


def load_regions(
    build: str, preset: str, lengths: Mapping[str, int], upstream: int, downstream: int
) -> tuple[list[Region], dict[str, object]]:
    chrom_data = json.loads(_asset(f"{build}-chromosomes.json"))
    metadata: dict[str, object] = {"assembly_report": chrom_data["assembly_report"]}
    asset_digests: dict[str, str] = {}
    regions: list[Region] = []
    if preset == "promoters":
        for part in (1, 2):
            name = f"{build}-promoters-{part}.tsv.gz"
            data = _asset(name)
            asset_digests[name] = hashlib.sha256(data).hexdigest()
            with gzip.open(
                io.BytesIO(data), "rt", encoding="utf-8", newline=""
            ) as lines:
                regions.extend(promoter_regions(lines, lengths, upstream, downstream))
        metadata.update(chrom_data["promoters"])
        metadata["transcript_rule"] = TRANSCRIPT_RULE
    else:
        hg38 = build == "hg38"
        name = hg38_island_path().name if hg38 else "t2t-islands.bed.gz"
        data = _hg38_islands() if hg38 else _asset(name)
        asset_digests[name] = hashlib.sha256(data).hexdigest()
        with gzip.open(io.BytesIO(data), "rt", encoding="utf-8", newline="") as lines:
            regions = [
                region
                for region in island_regions(lines, ucsc_table=hg38)
                if region.chrom in lengths
            ]
        metadata.update(
            {
                "source": "UCSC masked cpgIslandExt",
                "version": f"{'hg38' if hg38 else 'hs1'} snapshot 2026-11-22",
                "url": UCSC_HG38 if hg38 else UCSC_HS1,
                "sha256": HG38_ISLAND_SHA256 if hg38 else HS1_ISLAND_SHA256,
            }
        )
    metadata["asset_sha256"] = asset_digests
    return regions, metadata
