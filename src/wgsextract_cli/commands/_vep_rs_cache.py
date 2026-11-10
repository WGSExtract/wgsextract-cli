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
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from wgsextract_cli.api._runtime import cancel_event, checkpoint
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.ref_library import download_file
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import WGSExtractError, run_command

if TYPE_CHECKING:
    from wgsextract_cli.api.vep import VepDownloadParams, VepVerifyParams

_CHROMOSOMES = [str(c) for c in range(1, 23)] + ["X", "Y", "MT"]


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


def _sources(
    mirror_root: str, params: VepDownloadParams, work: Path
) -> tuple[dict[str, Path], list[_Source]]:
    """The Ensembl files a vep-rs cache is built from, and the builder's
    inputs among them. GRCh37's files live under pub/grch37, with the
    release-87 gene set Ensembl keeps for it."""
    species, release, assembly = params.species, params.vep_version, params.assembly
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
        _Source(f"{base}/mysql/{species}_core_{db}/{t}.txt.gz", core / f"{t}.txt.gz")
        for t in _CORE_TABLES
    ]
    # The gene phenotype counts (GENE_PHENO) come from the variation tables.
    sources += [
        _Source(
            f"{base}/mysql/{species}_variation_{db}/{t}.txt.gz", core / f"{t}.txt.gz"
        )
        for t in ("phenotype", "phenotype_feature")
    ]
    inputs = {"gff3": gff3, "pep": pep, "genome": genome, "core": core}
    if params.variations:
        sources += [
            _Source(
                f"{base}/mysql/{species}_variation_{db}/{t}.txt.gz",
                variation / f"{t}.txt.gz",
            )
            for t in _VARIATION_TABLES
        ]
        vcfs = work / "variation_vcfs"
        names = [f"{species}-chr{c}.vcf.gz" for c in _CHROMOSOMES]
        names += [n.format(species=species) for n in _GENOME_WIDE_VCFS]
        for vcf in names:
            optional = not vcf.startswith(f"{species}-chr")
            for file in (vcf, vcf + ".csi"):
                url = f"{base}/variation/vcf/{species}/{file}"
                sources.append(_Source(url, vcfs / file, optional))
        inputs |= {"variation": variation, "vcfs": vcfs}
    return inputs, sources


def _fetch(sources: list[_Source]) -> None:
    """Download each file not already complete, resuming a partial one, and
    check it against its folder's Ensembl CHECKSUMS."""
    from ._vep_resources import calculate_bsd_sum

    checksums: dict[str, dict[str, tuple[int, int]]] = {}
    cancel = cancel_event()
    for source in sources:
        checkpoint()
        folder, filename = source.url.rsplit("/", 1)
        done = Path(f"{source.dest}.ok")
        if done.exists() and source.dest.exists():
            continue
        source.dest.parent.mkdir(parents=True, exist_ok=True)
        expected = _expected_sums(folder, checksums).get(filename)
        if expected is None and source.optional:
            warn(
                "vep_source_missing",
                f"{source.url} isn't published; the cache is built without it.",
                url=source.url,
            )
            continue
        logging.info(f"Downloading {source.url}")
        if not download_file(source.url, str(source.dest), None, cancel):
            checkpoint()
            raise WGSExtractError(f"Download failed: {source.url}")
        if expected is None:
            warn(
                "vep_checksum_unlisted",
                f"{filename} is not listed in {folder}/CHECKSUMS; not verified.",
                url=source.url,
            )
        elif (actual := calculate_bsd_sum(str(source.dest))) != expected:
            source.dest.unlink()
            raise WGSExtractError(
                f"{filename} failed its checksum (expected {expected}, got "
                f"{actual}); run the download again."
            )
        done.touch()


def _expected_sums(
    folder: str, cache: dict[str, dict[str, tuple[int, int]]]
) -> dict[str, tuple[int, int]]:
    """``folder``'s Ensembl CHECKSUMS (BSD sum and blocks by file name)."""
    if folder not in cache:
        sums: dict[str, tuple[int, int]] = {}
        try:
            with urllib.request.urlopen(f"{folder}/CHECKSUMS", timeout=60) as r:
                for line in r.read().decode().splitlines():
                    parts = line.split()
                    if len(parts) >= 3:
                        sums[parts[-1]] = (int(parts[0]), int(parts[1]))
        except (OSError, ValueError) as e:
            logging.warning(f"Could not read {folder}/CHECKSUMS: {e}")
        cache[folder] = sums
    return cache[folder]


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
    _fetch(sources)
    genome = work / "genome.fa"
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
            "--core-dumps", str(inputs["core"]),
            "--genome-fasta", str(genome),
            "--output-dir", str(built / "cache"),
        ]  # fmt: skip
        if params.variations:
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
    missing = [c for c in _CHROMOSOMES if not (transcripts / c).is_dir()]
    current_result().add_metrics(missing_chromosomes=len(missing))
    if missing:
        warn(
            "chromosomes_missing",
            f"No transcripts for chromosome(s) {', '.join(missing)}.",
            chromosomes=",".join(missing),
        )
    if len(missing) == len(_CHROMOSOMES):
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
    manifest = version_dir / "CHECKSUMS.sha256"
    if not manifest.is_file():
        warn(
            "vep_checksums_missing",
            f"{manifest} is missing, so the cache's files weren't checked.",
            path=str(manifest),
        )
        return []
    bad = []
    for line in manifest.read_text().splitlines():
        expected, _, name = line.partition("  ")
        if not name:
            continue
        checkpoint()
        path = version_dir / name
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
