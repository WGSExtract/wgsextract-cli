"""``vep download | verify`` for vep-rs: its JSON transcript cache, built from
Ensembl's files by vep-rs's own ``vep-cache-builder``."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from http.client import IncompleteRead
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError

from wgsextract_cli.api._runtime import cancel_event, checkpoint
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.download_progress import redact_urls, retryable_http_status
from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor
from wgsextract_cli.core.ref_library import download_file
from wgsextract_cli.core.resource_estimates import (
    GIB,
    estimate_vep_download,
    preflight_resources,
)
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import WGSExtractError, run_command

from ._vep_engines import CHM13

if TYPE_CHECKING:
    from wgsextract_cli.api.vep import VepDownloadParams, VepVerifyParams

_CHROMOSOMES = [str(c) for c in range(1, 23)] + ["X", "Y", "MT"]

# Ensembl's T2T-CHM13v2.0 gene set (GCA_009914755.4) is a rapid release, not
# part of its numbered releases: a GFF3, GTF and proteins, and the genome, each
# folder with an md5sum.txt. It has no table dumps or variation files, and no
# mitochondrial genes (CHM13's own chrM isn't annotated).
_CHM13_ROOT = "rapid-release/species/Homo_sapiens/GCA_009914755.4/ensembl"
_CHM13_GENESET = "2022_07"
_CHM13_STEM = "Homo_sapiens-GCA_009914755.4"


def _chromosomes(assembly: str) -> list[str]:
    """The chromosomes a cache of ``assembly`` has transcripts on."""
    return _CHROMOSOMES[:-1] if assembly == CHM13 else _CHROMOSOMES


# Ensembl's MySQL table dumps the builder reads: the core tables for MANE,
# xrefs, protein domains and flags, and the variation tables for GENE_PHENO
# and the ClinVar allele columns of known variants.
_CORE_TABLES = (
    "analysis_description", "attrib_type", "external_db", "gene", "meta",
    "object_xref", "protein_feature", "seq_region", "seq_region_attrib",
    "seq_region_synonym", "transcript", "transcript_attrib", "translation",
    "translation_attrib", "xref",
)  # fmt: skip
_VARIATION_TABLES = (
    "attrib_type", "phenotype", "phenotype_feature", "phenotype_feature_attrib",
    "seq_region", "source",
)  # fmt: skip
_GENOME_WIDE_VCFS = (
    "1000GENOMES-phase_3.vcf.gz",
    "{species}_somatic.vcf.gz",
    "{species}_phenotype_associated.vcf.gz",
    "{species}_clinically_associated.vcf.gz",
)


@dataclass(frozen=True)
class _Source:
    """One Ensembl file: where it is and where it goes."""

    url: str
    dest: Path
    # Optional files (absent for some releases or assemblies) only warn.
    optional: bool = False
    # A table dump Ensembl may publish uncompressed (some GRCh37 ones).
    table: bool = False
    # The folder's checksum listing: BSD sums, or md5sum's output.
    listing: str = "CHECKSUMS"


def _sources(
    mirror_root: str, params: VepDownloadParams, work: Path
) -> tuple[dict[str, Path], list[_Source]]:
    """The Ensembl files a vep-rs cache is built from, and the builder's
    inputs among them. GRCh37's files live under pub/grch37, with the
    release-87 gene set Ensembl keeps for it."""
    species, release, assembly = params.species, params.vep_version, params.assembly
    if assembly == CHM13:
        return _chm13_sources(mirror_root, params, work)
    name = species[0].upper() + species[1:]
    grch37 = assembly == "GRCh37"
    base = f"{mirror_root}/{'grch37/' if grch37 else ''}release-{release}"
    genes = "87" if grch37 else release
    db = f"{release}_{'37' if grch37 else '38'}"
    gff3 = work / f"{name}.{assembly}.{genes}.gff3.gz"
    pep = work / f"{name}.{assembly}.pep.all.fa.gz"
    genome = work / f"{name}.{assembly}.dna.primary_assembly.fa.gz"
    sources = [
        _Source(f"{base}/gff3/{species}/{gff3.name}", gff3),
        _Source(f"{base}/fasta/{species}/pep/{pep.name}", pep),
        _Source(f"{base}/fasta/{species}/dna/{genome.name}", genome),
    ]
    core, variation = work / "core_dumps", work / "variation_dumps"
    sources += [
        _Source(
            f"{base}/mysql/{species}_core_{db}/{t}.txt.gz",
            core / f"{t}.txt.gz",
            table=True,
        )
        for t in _CORE_TABLES
    ]
    # The gene phenotype counts (GENE_PHENO) come from the variation tables.
    sources += [
        _Source(
            f"{base}/mysql/{species}_variation_{db}/{t}.txt.gz",
            core / f"{t}.txt.gz",
            table=True,
        )
        for t in ("phenotype", "phenotype_feature")
    ]
    inputs = {"gff3": gff3, "pep": pep, "genome": genome, "core": core}
    if params.variations:
        sources += [
            _Source(
                f"{base}/mysql/{species}_variation_{db}/{t}.txt.gz",
                variation / f"{t}.txt.gz",
                table=True,
            )
            for t in _VARIATION_TABLES
        ]
        vcfs = work / "variation_vcfs"
        names = [f"{species}-chr{c}.vcf.gz" for c in _CHROMOSOMES]
        names += [n.format(species=species) for n in _GENOME_WIDE_VCFS]
        for vcf in names:
            # A chromosome's VCF is required; the builder reads it without
            # its index, and the genome-wide files are extras.
            required = vcf.startswith(f"{species}-chr")
            for file, optional in ((vcf, not required), (vcf + ".csi", True)):
                url = f"{base}/variation/vcf/{species}/{file}"
                sources.append(_Source(url, vcfs / file, optional))
        inputs |= {"variation": variation, "vcfs": vcfs}
    return inputs, sources


def _chm13_sources(
    mirror_root: str, params: VepDownloadParams, work: Path
) -> tuple[dict[str, Path], list[_Source]]:
    """The rapid-release files of Ensembl's T2T-CHM13v2.0 gene set."""
    if params.species != "homo_sapiens":
        raise WGSExtractError(f"{CHM13} is a homo_sapiens assembly.")
    if params.variations:
        warn(
            "vep_variations_unavailable",
            f"Ensembl publishes no known-variant files for {CHM13} to build "
            "from; the cache has transcripts only.",
        )
    base = f"{mirror_root}/{_CHM13_ROOT}"
    geneset = f"{base}/geneset/{_CHM13_GENESET}"
    files = {
        "gff3": f"{geneset}/{_CHM13_STEM}-{_CHM13_GENESET}-genes.gff3.gz",
        "gtf": f"{geneset}/{_CHM13_STEM}-{_CHM13_GENESET}-genes.gtf.gz",
        "pep": f"{geneset}/{_CHM13_STEM}-{_CHM13_GENESET}-pep.fa.gz",
        "genome": f"{base}/genome/{_CHM13_STEM}-unmasked.fa.gz",
    }
    inputs = {key: work / url.rsplit("/", 1)[1] for key, url in files.items()}
    sources = [
        _Source(url, inputs[key], listing="md5sum.txt") for key, url in files.items()
    ]
    return inputs, sources


# Files fetched at once: Ensembl's FTP serves each connection at about
# 0.5 MB/s, and download_file splits only files over 1 GiB into ranges.
_PARALLEL_DOWNLOADS = 6


def _fetch(sources: list[_Source]) -> None:
    """Download each file not already complete, several at a time, resuming a
    partial one, and check each against its folder's Ensembl CHECKSUMS."""
    from ._vep_resources import _require_in_scope

    checksums: dict[str, dict[str, _Sum]] = {}
    cancel = cancel_event()
    todo: list[tuple[str, Path, _Sum]] = []
    for source in sources:
        checkpoint()
        folder, filename = source.url.rsplit("/", 1)
        listing = _expected_sums(f"{folder}/{source.listing}", checksums)
        url, dest = source.url, source.dest
        if filename not in listing and source.table and filename[:-3] in listing:
            url, dest = url[:-3], dest.with_suffix("")
            filename = filename[:-3]
        for path in (dest, *(Path(f"{dest}{x}") for x in _SIDECARS)):
            _require_in_scope(path)
        if Path(f"{dest}.ok").exists() and dest.exists():
            continue
        expected = listing.get(filename)
        if expected is None:
            if source.optional:
                warn(
                    "vep_source_missing",
                    f"{url} isn't published; the cache is built without it.",
                    url=url,
                )
                continue
            raise WGSExtractError(
                f"{filename} is not listed in {folder}/{source.listing}; Ensembl "
                f"doesn't publish it for this release and assembly."
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        todo.append((url, dest, expected))

    failed = threading.Event()
    lock = threading.Lock()
    first_error: list[BaseException] = []

    class _Stop:
        """Stops the other downloads when the run is cancelled or one fails."""

        def is_set(self) -> bool:
            return failed.is_set() or bool(cancel and cancel.is_set())

    def fetch(url: str, dest: Path, expected: _Sum) -> None:
        if failed.is_set():
            return
        logging.info(f"Downloading {url}")
        try:
            if not download_file(url, str(dest), None, _Stop()):
                raise WGSExtractError(f"Download failed: {url}")
            _check_download(dest, expected)
        except BaseException as e:
            # A download stopped because another failed fails too; only the
            # first failure is the one to report.
            with lock:
                if not failed.is_set():
                    first_error.append(e)
                    failed.set()
            raise

    with ContextThreadPoolExecutor(max_workers=_PARALLEL_DOWNLOADS) as pool:
        futures = [pool.submit(fetch, *item) for item in todo]
    checkpoint()
    if first_error:
        raise first_error[0]
    for future in futures:
        future.result()


# Files download_file keeps beside a download, and the completion marker.
_SIDECARS = (".ok", ".partial", ".partial.segments", ".partial.source")


# A file's checksum as its folder lists it: a BSD sum and 1 KiB block count
# (Ensembl's CHECKSUMS), or an MD5 digest (md5sum.txt).
_Sum = tuple[int, int] | str


def _check_download(dest: Path, expected: _Sum) -> None:
    from ._vep_resources import calculate_bsd_sum

    actual: _Sum
    if isinstance(expected, str):
        digest = hashlib.md5(usedforsecurity=False)
        with open(dest, "rb") as f:
            while chunk := f.read(16 << 20):
                checkpoint()
                digest.update(chunk)
        actual = digest.hexdigest()
    else:
        actual = calculate_bsd_sum(str(dest))
    if actual != expected:
        dest.unlink()
        raise WGSExtractError(
            f"{dest.name} failed its checksum (expected {expected}, got "
            f"{actual}); run the download again."
        )
    Path(f"{dest}.ok").touch()


def _read_checksum_listing(url: str) -> str:
    attempts = 3
    for attempt in range(1, attempts + 1):
        checkpoint()
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data: bytes = response.read()
            return data.decode()
        except (URLError, ConnectionError, TimeoutError, IncompleteRead) as error:
            if isinstance(error, HTTPError):
                error.close()
                if not retryable_http_status(error.code):
                    raise
            if attempt == attempts:
                raise
            time.sleep(2 ** (attempt - 1))
    raise AssertionError("Checksum listing retry loop did not run")


def _expected_sums(
    listing_url: str, cache: dict[str, dict[str, _Sum]]
) -> dict[str, _Sum]:
    """A folder's checksum listing by file name: Ensembl's CHECKSUMS (BSD sum
    and blocks) or an md5sum.txt. An unreadable listing fails: it can't tell
    a missing file from a lost one."""
    if listing_url not in cache:
        location = redact_urls(listing_url)
        md5 = listing_url.endswith("md5sum.txt")
        sums: dict[str, _Sum] = {}
        try:
            for line in _read_checksum_listing(listing_url).splitlines():
                parts = line.split()
                if md5 and len(parts) == 2 and len(parts[0]) == 32:
                    sums[parts[1].lstrip("*")] = parts[0].lower()
                elif not md5 and len(parts) >= 3:
                    sums[parts[-1]] = (int(parts[0]), int(parts[1]))
        except (OSError, ValueError, IncompleteRead) as error:
            detail = (
                f"HTTP {error.code}"
                if isinstance(error, HTTPError)
                else redact_urls(str(error), sources=(listing_url,))
            )
            raise WGSExtractError(
                f"Could not read {location} ({detail}); try again, or use "
                "the other --mirror."
            ) from None
        if not sums:
            raise WGSExtractError(f"{location} lists no files.")
        cache[listing_url] = sums
    return cache[listing_url]


def _gunzip(src: Path, dest: Path) -> None:
    with gzip.open(src, "rb") as f_in, open(dest, "wb") as f_out:
        while chunk := f_in.read(16 << 20):
            checkpoint()
            f_out.write(chunk)


def build_vep_rs_cache(params: VepDownloadParams, mirror_root: str) -> None:
    """Build the vep-rs cache of ``params``'s species, release and assembly.

    Ensembl's GFF3, protein and genome FASTA, table dumps and, with
    ``variations``, variation VCFs are downloaded (resumably, checksummed)
    into a work folder kept until the build succeeds; vep-cache-builder then
    builds the cache beside the old one, which it replaces once complete."""
    from ._vep_resources import _require_in_scope

    verify_dependencies(["vep-cache-builder", "samtools"])
    version_dir = params.version_dir()
    engine_root = version_dir.parent.parent
    work = engine_root / f".download-{params.species}-{version_dir.name}"
    for path in (engine_root, work, version_dir):
        _require_in_scope(path)
    work.mkdir(parents=True, exist_ok=True)

    inputs, sources = _sources(mirror_root, params, work)
    estimate = estimate_vep_download(
        str(engine_root), params.variations and "vcfs" in inputs, params.assembly
    )
    preflight_resources(lambda: estimate)
    disk = estimate.resources[0]
    if disk.status == "LOW" and not (version_dir.exists() or any(work.iterdir())):
        smaller = " or use --no-variations" if "vcfs" in inputs else ""
        raise WGSExtractError(
            f"Building the vep-rs cache needs about "
            f"{(disk.estimated_bytes or 0) / GIB:.0f} GiB free in {engine_root} "
            f"(downloads, genome and cache), but only "
            f"{(disk.available_bytes or 0) / GIB:.0f} GiB is free. Free some "
            f"space, choose another --vep-cache{smaller}."
        )
    _fetch(sources)
    genome = work / "genome.fa"
    for path in (genome, Path(f"{genome}.fai")):
        _require_in_scope(path)
    if not Path(f"{genome}.fai").exists():
        _gunzip(inputs["genome"], genome)
        run_command(["samtools", "faidx", str(genome)])

    built = Path(tempfile.mkdtemp(prefix=".vep-rs-build-", dir=engine_root))
    try:
        cmd = [
            get_tool_path("vep-cache-builder") or "vep-cache-builder",
            "--species", params.species,
            "--release", params.vep_version,
            "--assembly", params.assembly,
            "--gff3", str(inputs["gff3"]),
            "--fasta", str(inputs["pep"]),
            "--genome-fasta", str(genome),
            "--output-dir", str(built / "cache"),
        ]  # fmt: skip
        if "core" in inputs:
            cmd += ["--core-dumps", str(inputs["core"])]
        if "gtf" in inputs:
            cmd += ["--gtf", str(inputs["gtf"])]
        if "vcfs" in inputs:
            cmd += [
                "--include-variations",
                "--variation-vcf-dir", str(inputs["vcfs"]),
                "--variation-dumps", str(inputs["variation"]),
            ]  # fmt: skip
        logging.info("Building the vep-rs cache (vep-cache-builder)")
        run_command(cmd)
        checkpoint()
        _replace_dir(built / "cache", version_dir)
    finally:
        shutil.rmtree(built, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    logging.info(f"vep-rs cache ready in {version_dir}")
    current_result().add_output(version_dir, "vep_cache", kind="directory")
    current_result().set_manifest_stem(version_dir)


def _replace_dir(new: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    old = None
    if dest.exists():
        old = Path(tempfile.mkdtemp(prefix=".vep-rs-old-", dir=dest.parent)) / "old"
        os.replace(dest, old)
    try:
        os.replace(new, dest)
    except OSError:
        if old is not None:
            os.replace(old, dest)
        raise
    if old is not None:
        shutil.rmtree(old.parent, ignore_errors=True)


def verify_vep_rs_cache(params: VepVerifyParams) -> None:
    """Check a vep-rs cache: its info.json names the species and assembly,
    each main chromosome has transcripts, and every file the builder listed
    in CHECKSUMS.sha256 is present and unchanged."""
    version_dir = params.version_dir()
    logging.info(f"Verifying the vep-rs cache in {version_dir}")
    if not version_dir.is_dir():
        raise WGSExtractError(
            f"No vep-rs cache in {version_dir}; build it with 'wgsextract vep "
            f"download --engine vep-rs --vep-version {params.vep_version} "
            f"--assembly {params.assembly}'."
        )
    try:
        info = json.loads((version_dir / "info.json").read_text())
    except (OSError, ValueError) as e:
        raise WGSExtractError(f"{version_dir}/info.json is unreadable: {e}") from e
    if info.get("assembly") != params.assembly or info.get("species") != params.species:
        raise WGSExtractError(
            f"{version_dir} holds {info.get('species')} {info.get('assembly')}, "
            f"not {params.species} {params.assembly}."
        )

    transcripts = version_dir / "transcripts"
    expected = _chromosomes(params.assembly)
    missing = [c for c in expected if not (transcripts / c).is_dir()]
    current_result().add_metrics(missing_chromosomes=len(missing))
    if missing:
        warn(
            "chromosomes_missing",
            f"No transcripts for chromosome(s) {', '.join(missing)}.",
            chromosomes=",".join(missing),
        )
    if len(missing) == len(expected):
        raise WGSExtractError(f"{version_dir} has no transcripts; build it again.")

    bad = _checksum_failures(version_dir)
    current_result().add_metrics(damaged_files=len(bad))
    if bad:
        raise WGSExtractError(
            f"{len(bad)} file(s) of {version_dir} are missing or changed (first: "
            f"{bad[0]}); build the cache again with 'wgsextract vep download "
            "--engine vep-rs'."
        )
    logging.info("vep-rs cache verification complete.")


def _checksum_failures(version_dir: Path) -> list[str]:
    """Files of the builder's manifest that are missing or changed. A
    manifest naming nothing, or a file outside the cache, fails."""
    from wgsextract_cli.core import read_scope

    manifest = version_dir / "CHECKSUMS.sha256"
    if not manifest.is_file():
        warn(
            "vep_checksums_missing",
            f"{manifest} is missing, so the cache's files weren't checked.",
            path=str(manifest),
        )
        return []
    entries = []
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        expected, sep, name = line.partition("  ")
        rel = Path(name)
        if (
            not sep
            or len(expected) != 64
            or rel.is_absolute()
            or rel.drive
            or ".." in rel.parts
        ):
            raise WGSExtractError(f"{manifest} has a malformed entry: {line!r}.")
        entries.append((expected, name))
    if not entries:
        raise WGSExtractError(f"{manifest} lists no files; build the cache again.")
    bad = []
    for expected, name in entries:
        checkpoint()
        path = version_dir / name
        if not read_scope.may_follow(path):
            raise WGSExtractError(
                f"{path} leads outside the folders this server may use."
            )
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while chunk := f.read(1 << 20):
                    digest.update(chunk)
        except OSError:
            bad.append(name)
            continue
        if digest.hexdigest() != expected:
            bad.append(name)
    return bad
