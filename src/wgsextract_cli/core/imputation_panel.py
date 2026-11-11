"""The GRCh38 imputation reference panel, installed per chromosome on request
(``ref imputation --install --chromosomes chr20``) into the reference library.

- Panel: the 1000 Genomes 30x (NYGC) phased SNV/indel/SV panel of 3,202
  samples (Byrska-Bishop et al. 2022), from IGSR's FTP. Its data are free to
  use without restriction (IGSR data reuse policy). Each chromosome's VCF is
  pinned by SHA-256 (EBI publishes only MD5s; both were checked).
- Genetic maps: GLIMPSE's GRCh38 maps (MIT, odelaneau/GLIMPSE), pinned to a
  commit and by SHA-256; Beagle reads the same map in PLINK format.

Installing keeps the biallelic SNPs (``panel.vcf.gz``, what GLIMPSE2's
tutorial and Beagle use), splits them into GLIMPSE2 chunks and writes each
chunk's binary reference, then deletes the download. The binary references
are tied to the GLIMPSE2 version that wrote them; a newer GLIMPSE2 rebuilds
them from ``panel.vcf.gz`` without downloading again.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core.download_progress import CancelEvent, ProgressCallback
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.reference_assets import resolve_reference_library
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    run_command,
    run_pipeline,
)

FOLDER = "imputation"
PANEL_ID = "1kGP-30x-GRCh38"
PANEL_LABEL = "1000 Genomes 30x phased panel (3,202 samples, NYGC), GRCh38"
_PANEL_BASE = (
    "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/"
    "1000G_2504_high_coverage/working/20220422_3202_phased_SNV_INDEL_SV/"
)
_MAP_COMMIT = "6fe2552121e88ae84d699227d97d6c6716718b94"
_MAP_BASE = (
    f"https://raw.githubusercontent.com/odelaneau/GLIMPSE/{_MAP_COMMIT}"
    "/maps/genetic_maps.b38/"
)
# Bump when installing writes different files, so old installs are redone.
LAYOUT_VERSION = 1
_MARKER = ".installed.json"
# Extra GLIMPSE2_chunk options. Its defaults (windows of at least 2 Mb and
# 20,000 common variants) suit whole chromosomes; it aborts on a panel
# smaller than one window.
CHUNK_OPTIONS: tuple[str, ...] = ()


@dataclass(frozen=True)
class Download:
    url: str
    size: int
    sha256: str


# chrX needs each sample's ploidy, which `vcf impute` doesn't handle yet.
AUTOSOMES = tuple(f"chr{i}" for i in range(1, 23))

# Panel VCF per chromosome: size in bytes and SHA-256.
_PANEL: dict[str, tuple[int, str]] = {
    "chr20": (
        687320087,
        "9e936fdf434a4aa4ff1d37c0d386a13a43a7211d4f09fb5da811ed6eb5125626",
    ),
}

_MAPS = {
    "chr1": "74719e16f0fe28864aeeef24160f8ee1ac4b5debce140cbc255c383357e02c12",
    "chr2": "d97886f9bdcc5af24e92d56fce2cb37bc6b65fb4147d4a7aa5ec5c5498bcfd77",
    "chr3": "70b32f955d15e4f79555a406f104c17cf66938553900b4d71c37f080b781b248",
    "chr4": "05ce2d46dc47886a50bc2ed839aabf88855ac7910171a895af24bbd2d7faae6d",
    "chr5": "58a2edbca615b2f1ea606e68b314b725743886c88f55958a4c8cdf399b73a350",
    "chr6": "67594580eb278157b09d4697c4a91aea62bbb26e9372d913497943e1a9f02ad2",
    "chr7": "27f92d714a4056d60cb81c6f138929f8ba7fb045a912de1562284747eae3d209",
    "chr8": "f4e2d9935cf41e65439087d4a98b4d5594df9076a9132360b23b6231d68ea5a1",
    "chr9": "d44e3ddd4a1ba0160d7fa73314f40638b47a425f91baba6f7373307694917734",
    "chr10": "cf11e39667f6088a08cf4c70644ad31aadff64bf17c450e3ab2c6c0c6edbcb4b",
    "chr11": "f39618dbc2034625455af4f000710deb1bb94f9751697d3ebbd638166c616cfb",
    "chr12": "413f2b3c7acc0f13073c57afbb39c7e1e85959e9835131b77b24939926d77371",
    "chr13": "89edc64d9f7b6e1bb59922eeac292e94b3fc9e677c2271f46e54a0ab786718dd",
    "chr14": "757e6ae7914ec7e0244f5bbfb84e8b9f26583ccba4997c44fec63ece00166989",
    "chr15": "de4ab5d6170fc95cdcb304e7390609042267e98344a59b4e6fba9dd75e48e393",
    "chr16": "6dbd8d38601e5b6700497b5452913e39f08a8e790fe1ae1b2a1236fbf6ffb256",
    "chr17": "6bb11166f491501bd15ce56a7e3f699e3ede134dff6c41f457f9d1a1c623a468",
    "chr18": "da91b3def50fb0bf5ba42247eb1747d5342a4709a795af054699f8b62788dc47",
    "chr19": "b98380d02f4e114234ed3c940c63131faf7c4e218b50f66f5f3df0b8cd81d698",
    "chr20": "015789f26d0bcb7bb4f433892a2dfc091b0995ac0abf9bebe2c5e9c01e37a16d",
    "chr21": "971e6bea3c756146224e78d3d409cb91dcb233b164f38633b85ac35acbab27d9",
    "chr22": "c23663c6e65274bc15fc44facf26535e8d59f3200af4b6e7d347890ecb7a58da",
}

# The installed files take about this share of the panel download, and the
# install needs the download and them at once (measured on chr20).
INSTALLED_SHARE = 1.0


def panel_download(chrom: str) -> Download:
    size, sha256 = _PANEL[chrom]
    name = f"1kGP_high_coverage_Illumina.{chrom}.filtered.SNV_INDEL_SV_phased_panel"
    return Download(f"{_PANEL_BASE}{name}.vcf.gz", size, sha256)


def map_download(chrom: str) -> Download:
    return Download(f"{_MAP_BASE}{chrom}.b38.gmap.gz", 0, _MAPS[chrom])


def chromosomes(text: str | None) -> list[str]:
    """``chr20,chr21`` or ``20-22`` or ``all`` as panel chromosome names."""
    if not text:
        raise UsageError("Name the chromosomes, e.g. --chromosomes chr20 or all.")
    if text.strip().lower() == "all":
        return list(AUTOSOMES)
    names: list[str] = []
    for item in (part.strip() for part in text.split(",")):
        bare = item.removeprefix("chr")
        lo, dash, hi = bare.partition("-")
        span = (
            range(int(lo), int(hi) + 1)
            if dash and lo.isdigit() and hi.isdigit()
            else None
        )
        for n in span or [bare]:
            name = f"chr{n}"
            if name not in AUTOSOMES:
                raise UsageError(
                    f"No imputation panel for {item}: the panel covers chr1-chr22 "
                    "(chrX is not supported yet)."
                )
            if name not in names:
                names.append(name)
    return names


def folder(reflib: str | None = None) -> Path:
    return Path(resolve_reference_library(reflib), FOLDER, PANEL_ID)


@dataclass(frozen=True)
class ChromosomePanel:
    chrom: str
    panel: Path  # biallelic SNPs, bgzipped and indexed
    gmap: Path  # GLIMPSE map
    plink_map: Path  # the same map for Beagle
    chunks: list[tuple[str, str, Path]]  # input region, output region, binary ref


def glimpse_version() -> str:
    """GLIMPSE2's version, as its binary references depend on it."""
    out = run_command(["GLIMPSE2_phase", "--help"], capture_output=True, check=False)
    for line in out.stdout.splitlines():
        if "Version" in line and ":" in line:
            return line.split(":", 1)[1].strip()
    raise WGSExtractError(
        "Could not read GLIMPSE2's version from GLIMPSE2_phase --help."
    )


def _marker(base: Path) -> dict[str, Any] | None:
    try:
        marker = json.loads((base / _MARKER).read_text())
    except (OSError, ValueError):
        return None
    return marker if isinstance(marker, dict) else None


def _panel_ready(base: Path, chrom: str) -> bool:
    marker = _marker(base)
    return bool(
        marker
        and marker.get("layout") == LAYOUT_VERSION
        and marker.get("panel_sha256") == _PANEL[chrom][1]
        and (base / "panel.vcf.gz").is_file()
        and (base / "map.gmap.gz").is_file()
    )


def installed(chrom: str, reflib: str | None = None) -> bool:
    base = folder(reflib) / chrom
    marker = _marker(base)
    return _panel_ready(base, chrom) and bool(marker and marker.get("chunks"))


def installed_chromosomes(reflib: str | None = None) -> list[str]:
    return [c for c in AUTOSOMES if installed(c, reflib)]


def require(chrom: str, reflib: str | None, version: str | None) -> ChromosomePanel:
    """An installed chromosome's panel; with ``version``, its binary references
    must be from that GLIMPSE2."""
    base = folder(reflib) / chrom
    marker = _marker(base)
    hint = (
        f"Install it with 'wgsextract ref imputation --install --chromosomes "
        f"{chrom}' (about {_PANEL[chrom][0] / 1e9:.1f} GB to download)."
    )
    if not installed(chrom, reflib) or marker is None:
        raise WGSExtractError(f"The {chrom} imputation panel is not installed. {hint}")
    if version is not None and marker.get("glimpse") != version:
        raise WGSExtractError(
            f"The {chrom} imputation panel was prepared with GLIMPSE2 "
            f"{marker.get('glimpse')}, not the installed {version}. {hint}"
        )
    return ChromosomePanel(
        chrom,
        base / "panel.vcf.gz",
        base / "map.gmap.gz",
        base / "map.plink",
        [(i, o, base / b) for i, o, b in marker["chunks"]],
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch(
    item: Download,
    dest: Path,
    progress: ProgressCallback | None,
    cancel: CancelEvent | None,
) -> None:
    if dest.is_file() and _sha256(dest) == item.sha256:
        return
    if not download_file(
        item.url,
        str(dest),
        progress,
        cancel,
        expected_sha256=item.sha256,
        resolve_release_digest=False,
    ):
        raise WGSExtractError(f"Could not download the pinned {item.url}.")


def needed_bytes(chroms: list[str], reflib: str | None = None) -> int:
    """Free space an install of ``chroms`` needs at its peak."""
    base = folder(reflib)
    return sum(
        int(_PANEL[c][0] * (1 + INSTALLED_SHARE))
        for c in chroms
        if not _panel_ready(base / c, c)
    )


def check_disk(chroms: list[str], reflib: str | None = None) -> None:
    need = needed_bytes(chroms, reflib)
    if not need:
        return
    target = folder(reflib)
    probe = target
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < need:
        raise WGSExtractError(
            f"Installing the imputation panel for {', '.join(chroms)} needs about "
            f"{need / 1e9:.1f} GB free in {target}; {free / 1e9:.1f} GB is free. "
            "Free some space, or install fewer chromosomes at a time."
        )


def _write_plink_map(gmap: Path, out: Path) -> None:
    """GLIMPSE's ``pos chr cM`` map as Beagle's PLINK ``chr . cM pos``."""
    with gzip.open(gmap, "rt") as src, out.open("w", newline="\n") as dst:
        next(src)
        for line in src:
            pos, chrom, cm = line.split()
            dst.write(f"chr{chrom.removeprefix('chr')}\t.\t{cm}\t{pos}\n")


def _chunk(base: Path, chrom: str, threads: int, version: str) -> None:
    chunks_txt = base / "chunks.txt"
    run_command(
        ["GLIMPSE2_chunk", "--input", base / "panel.vcf.gz", "--region", chrom,
         "--sequential", "--map", base / "map.gmap.gz", "--threads", str(threads),
         "--output", chunks_txt, "--log", base / "chunk.log", *CHUNK_OPTIONS]
    )  # fmt: skip
    bins = base / "bins"
    shutil.rmtree(bins, ignore_errors=True)
    bins.mkdir()
    chunks: list[list[str]] = []
    for line in chunks_txt.read_text().splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        in_region, out_region = fields[2], fields[3]
        # Each chunk gets its own prefix: GLIMPSE2 appends the region to it.
        prefix = bins / str(len(chunks))
        run_command(
            ["GLIMPSE2_split_reference", "--reference", base / "panel.vcf.gz",
             "--map", base / "map.gmap.gz", "--input-region", in_region,
             "--output-region", out_region, "--threads", str(threads),
             "--output", prefix, "--log", base / "split.log"]
        )  # fmt: skip
        written = sorted(bins.glob(f"{prefix.name}_*.bin"))
        if len(written) != 1:
            raise WGSExtractError(
                f"GLIMPSE2_split_reference didn't write {out_region}'s reference."
            )
        chunks.append([in_region, out_region, f"bins/{written[0].name}"])
    if not chunks:
        raise WGSExtractError(f"GLIMPSE2_chunk made no chunks for {chrom}.")
    marker = _marker(base) or {}
    marker.update(glimpse=version, chunks=chunks)
    (base / _MARKER).write_text(json.dumps(marker, indent=1), newline="\n")


def install_chromosome(
    chrom: str,
    reflib: str | None,
    threads: int,
    progress: ProgressCallback | None = None,
    cancel: CancelEvent | None = None,
) -> Path:
    base = folder(reflib) / chrom
    base.mkdir(parents=True, exist_ok=True)
    version = glimpse_version()
    if not _panel_ready(base, chrom):
        (base / _MARKER).unlink(missing_ok=True)
        _fetch(map_download(chrom), base / "map.gmap.gz", progress, cancel)
        _write_plink_map(base / "map.gmap.gz", base / "map.plink")
        raw = base / "download.vcf.gz"
        _fetch(panel_download(chrom), raw, progress, cancel)
        logging.info(f"Keeping the biallelic SNPs of the {chrom} panel")
        staged = base / "panel.partial.vcf.gz"
        samples = base / "samples.txt"
        listed = run_command(["bcftools", "query", "-l", raw], capture_output=True)
        samples.write_text(listed.stdout, newline="\n")
        run_pipeline(
            [
                ["bcftools", "norm", "-m", "-any", "--threads", str(threads),
                 "-Ou", raw],
                # Naming every sample makes bcftools recount AC/AN, which
                # GLIMPSE2 reads and the split leaves stale (no plugin needed,
                # as Windows' static bcftools has none).
                ["bcftools", "view", "-m2", "-M2", "-v", "snps", "-S", samples,
                 "--threads", str(threads), "-Oz", "-o", staged],
            ],
            description=f"Preparing the {chrom} panel",
        )  # fmt: skip
        samples.unlink()
        run_command(["bcftools", "index", "-f", "-t", staged])
        staged.replace(base / "panel.vcf.gz")
        Path(f"{staged}.tbi").replace(base / "panel.vcf.gz.tbi")
        raw.unlink()
        marker = {
            "layout": LAYOUT_VERSION,
            "panel": PANEL_LABEL,
            "panel_sha256": _PANEL[chrom][1],
            "map_sha256": _MAPS[chrom],
        }
        (base / _MARKER).write_text(json.dumps(marker, indent=1), newline="\n")
    if not installed(chrom, reflib) or (_marker(base) or {}).get("glimpse") != version:
        logging.info(f"Writing GLIMPSE2's binary references for {chrom}")
        _chunk(base, chrom, threads, version)
    return base
