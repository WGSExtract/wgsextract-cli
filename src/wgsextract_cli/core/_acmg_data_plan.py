"""Sources, on-disk paths and installation plans for fastVEP databases."""

import logging
import re
import shutil
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from wgsextract_cli.core import read_scope
from wgsextract_cli.core.acmg_manifest import MANIFEST, load_manifest, site_data_of
from wgsextract_cli.core.dependency_checks import get_tool_version
from wgsextract_cli.core.download_progress import format_bytes
from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.ref_library.layout import download_path
from wgsextract_cli.core.ref_library.segmented import user_agent
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
)

from .acmg_regions import REGION_FLAGS, region_name, region_tracks, verified_region_bed

# The sources vcf acmg can read per run, at only the input's sites.
PER_RUN = ("gnomad", "spliceai")
# vcf acmg's cache of per-run databases, in the databases folder.
SITES_CACHE = "sites"
ASSEMBLY = "GRCh38"
CHROMOSOMES = tuple(f"chr{c}" for c in (*range(1, 23), "X", "Y"))
# GRCh38 lengths in megabases, to scale the estimates of the per-chromosome
# databases cut from one genome-wide file.
_MB = dict(
    zip(
        CHROMOSOMES,
        (248.96, 242.19, 198.3, 190.21, 181.54, 170.81, 159.35, 145.14, 138.39,
         133.8, 135.09, 133.28, 114.36, 107.04, 101.99, 90.34, 83.26, 80.37,
         58.62, 64.44, 46.71, 50.82, 156.04, 57.23),
        strict=True,
    )
)  # fmt: skip
_SUPPORTED_FASTVEP = re.compile(r"\b0\.5\.\d+\b")

ENSEMBL = "https://ftp.ensembl.org/pub/release-{release}/gff3/homo_sapiens/"
GFF3_NAME = "Homo_sapiens.GRCh38.{release}.gff3.gz"
CLINVAR_URL = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz"
VARIANT_SUMMARY_URL = (
    "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz"
)
_GNOMAD = "https://storage.googleapis.com/gcp-public-data--gnomad/release/4.1/"
GNOMAD_URL = _GNOMAD + "vcf/joint/gnomad.joint.v4.1.sites.{chrom}.vcf.bgz"
CONSTRAINT_URL = _GNOMAD + "constraint/gnomad.v4.1.constraint_metrics.tsv"
CLINGEN_URL = "https://search.clinicalgenome.org/kb/gene-validity/download"
REVEL_URL = "https://rothsj06.dmz.hpc.mssm.edu/revel-v1.3_all_chromosomes.zip"
PHYLOP_DIR = (
    "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/phyloP100way/"
    "hg38.100way.phyloP100way/"
)
PHYLOP_FILE = "{chrom}.phyloP100way.wigFix.gz"
REPEATMASKER_URL = (
    "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/database/rmsk.txt.gz"
)
_GNOMAD_RESOURCES = (
    "https://storage.googleapis.com/gcp-public-data--gnomad/resources/grch38/"
)
LCR_URL = _GNOMAD_RESOURCES + "lcr_intervals/LCRFromHengHg38.txt"
SEGDUP_URL = _GNOMAD_RESOURCES + "seg_dup_intervals/GRCh38_segdups.bed"
SPLICEAI_URL = (
    "https://ftp.ensembl.org/pub/data_files/homo_sapiens/GRCh38/"
    "variation_plugins/spliceai_scores.raw.snv.ensembl_mane_v1.4.grch38.vcf.gz"
)
# The INFO keys fastVEP's gnomad builder reads from the joint release (about
# 3% of a record's 11 KB): the counts, the filtering allele frequencies and
# each callset's failed filters.
GNOMAD_KEEP = ",".join(
    f"INFO/{key}"
    for key in (
        "AC_joint", "AN_joint", "AF_joint", "nhomalt_joint", "AC_joint_XY",
        "AN_joint_XY", "faf95_joint", "fafmax_faf95_max_joint", "exomes_filters",
        "genomes_filters", "lcr", "segdup",
        *(f"AF_joint_{group}" for group in (
            "afr", "amr", "asj", "eas", "fin", "mid", "nfe", "remaining", "sas")),
    )
)  # fmt: skip


@dataclass(frozen=True)
class Source:
    """A public source used to construct the ACMG reference library."""

    key: str
    label: str
    terms: str
    per_chromosome: bool = False
    # Terms that restrict commercial use, shown as a warning.
    restricted: bool = False
    chunk_bits: int | None = None


_NCBI = "NCBI ClinVar: public domain, no restrictions on use; cite ClinVar."
_GNOMAD_TERMS = "gnomAD: CC0 1.0, no restrictions; cite gnomAD."
SOURCES = (
    Source("clinvar", "ClinVar VCF", _NCBI),
    Source(
        "clinvar_protein",
        "ClinVar variant_summary (protein and splice index)",
        _NCBI,
    ),
    Source(
        "gnomad",
        "gnomAD v4.1 joint sites (streamed)",
        _GNOMAD_TERMS,
        per_chromosome=True,
        chunk_bits=16,
    ),
    Source("gnomad_genes", "gnomAD v4.1 gene constraint", _GNOMAD_TERMS),
    Source(
        "omim",
        "ClinGen gene-disease validity",
        "ClinGen: CC0 1.0; ClinGen asks you to credit it and give the access date.",
    ),
    Source(
        "revel",
        "REVEL v1.3",
        "REVEL: free for non-commercial use; other use needs the authors' "
        "permission (https://sites.google.com/site/revelgenomics/).",
        per_chromosome=True,
        restricted=True,
    ),
    Source(
        "spliceai",
        "SpliceAI scores (Illumina, MANE SNVs via Ensembl)",
        "SpliceAI: free for academic and not-for-profit use; other use needs a "
        "commercial licence from Illumina (https://github.com/Illumina/SpliceAI).",
        per_chromosome=True,
        restricted=True,
        chunk_bits=16,
    ),
    Source(
        "phylop",
        "UCSC phyloP 100-way",
        "UCSC phyloP100way: freely available for public use.",
        per_chromosome=True,
        chunk_bits=16,
    ),
    Source(
        "repeatmasker",
        "UCSC RepeatMasker (BP3)",
        "UCSC hg38 RepeatMasker track: cite UCSC and RepeatMasker; "
        "see https://genome.ucsc.edu/goldenPath/credits.html for data terms.",
    ),
    Source("lcr", "gnomAD low-complexity regions (LCRFromHengHg38)", _GNOMAD_TERMS),
    Source("segdup", "gnomAD segmental duplications (GRCh38_segdups)", _GNOMAD_TERMS),
)
BY_KEY = {source.key: source for source in SOURCES}


def source_url(key: str, chromosome: str = "") -> str:
    """Where a source is downloaded from (read when called)."""
    url = {
        "clinvar": CLINVAR_URL,
        "clinvar_protein": VARIANT_SUMMARY_URL,
        "gnomad": GNOMAD_URL,
        "gnomad_genes": CONSTRAINT_URL,
        "omim": CLINGEN_URL,
        "revel": REVEL_URL,
        "spliceai": SPLICEAI_URL,
        "phylop": PHYLOP_DIR + PHYLOP_FILE,
        "repeatmasker": REPEATMASKER_URL,
        "lcr": LCR_URL,
        "segdup": SEGDUP_URL,
    }[key]
    return url.format(chrom=chromosome or "{chrom}")


GFF3_TERMS = "Ensembl: no restrictions on use; cite Ensembl."

# Database bytes, measured on chromosome 22 with fastVEP 0.5 (issue 629): per
# byte fetched for the sources fetched per chromosome, per megabase for those
# cut from one genome-wide file, and whole for the rest.
_DB_PER_SOURCE_BYTE = {"gnomad": 0.0135, "phylop": 1.15}
_DB_PER_MB = {"spliceai": 4.9e6, "revel": 1.4e5}
_DB_BYTES = {
    "clinvar": 45e6,
    "clinvar_protein": 21e6,
    "gnomad_genes": 2e6,
    "omim": 1e6,
    "repeatmasker": 16e6,
    "lcr": 15e6,
    "segdup": 1e6,
}
# Staging bytes a per-chromosome build holds at its peak: gnomAD's reduced VCF
# per byte streamed, the SpliceAI cut and the REVEL rows per megabase.
_TEMP_PER_SOURCE_BYTE = {"gnomad": 0.09}
_TEMP_PER_MB = {"spliceai": 9.4e6, "revel": 2.1e6}
_TEMP_BYTES = {"repeatmasker": 25_000_000, "lcr": 65_000_000, "segdup": 1_000_000}


def fastvep_version() -> str:
    """The resolved fastVEP version: the 0.5 series this was validated with."""
    raw = get_tool_version("fastvep") or ""
    match = _SUPPORTED_FASTVEP.search(raw)
    if not match:
        raise WGSExtractError(
            f"fastVEP 0.5 is needed (found: {raw.strip() or 'unknown'}); its "
            "classifier and messages are what WGS Extract was validated with."
        )
    return match.group(0)


def folder(reflib: str | Path) -> Path:
    """The library's ACMG folder: the GFF3, ``sources/`` and ``sa/``."""
    return download_path(reflib, "acmg")


def default_sa_dir(reflib: str | Path) -> Path:
    return folder(reflib) / "sa"


def acmg_sa_dir(
    library_root: str | Path | None, sa_dir: str | Path | None
) -> Path | None:
    """The databases ``vcf acmg`` reads: ``sa_dir`` when given (or
    configured), else the library's ``downloads/acmg/sa`` when it is there."""
    if sa_dir:
        return Path(expand_path(sa_dir))
    built = default_sa_dir(library_root) if library_root else None
    return built if built and built.is_dir() else None


def recorded_gff3(sa_dir: str | Path) -> Path | None:
    """The GFF3 ``ref acmg-data`` recorded beside the databases in
    ``sa_dir``, when it is still there and the run may follow it."""
    if not read_scope.may_follow(sa_dir):
        return None
    recorded = (load_manifest(sa_dir).get("gff3") or {}).get("file")
    if not isinstance(recorded, str) or not recorded:
        return None
    path = Path(recorded)
    return path if read_scope.may_follow(path) and path.is_file() else None


def parse_chromosomes(text: str | None) -> list[str]:
    """``chr22``, ``1-22``, ``chr21,chrX`` or ``all`` (the default) as
    ``chrN`` names of the nuclear chromosomes."""
    if not text or text.strip().lower() == "all":
        return list(CHROMOSOMES)
    names: set[str] = set()
    for item in (part.strip() for part in text.split(",")):
        bare = item.removeprefix("chr").upper()
        lo, dash, hi = bare.partition("-")
        span = [bare]
        if dash and (lo + hi).isascii() and lo.isdigit() and hi.isdigit():
            # Checked before it is expanded: 1-1000000000 is not a billion names.
            if max(len(lo), len(hi)) > 2 or not 1 <= int(lo) <= int(hi) <= 22:
                raise UsageError(
                    f"No ACMG data for chromosomes {item!r}: a range runs upwards "
                    "within chr1-22 (e.g. --chromosomes chr1-22)."
                )
            span = [str(n) for n in range(int(lo), int(hi) + 1)]
        for n in span:
            if f"chr{n}" not in CHROMOSOMES:
                raise UsageError(
                    f"No ACMG data for chromosome {item!r}: give chr1-22, X or Y "
                    "(e.g. --chromosomes chr22), or all."
                )
            names.add(f"chr{n}")
    return sorted(names, key=CHROMOSOMES.index)


def _built(sa_dir: Path, manifest: dict[str, Any]) -> set[str]:
    """The databases the manifest records that are still in place, unchanged in
    size; one a source has no data for counts as built."""
    found = set()
    for name, entry in (manifest.get("databases") or {}).items():
        path = sa_dir / str(entry.get("file", ""))
        if entry.get("absent") or (
            path.is_file() and path.stat().st_size == entry.get("bytes")
        ):
            found.add(name)
    return found


def installed(sa_dir: str | Path) -> dict[str, Any]:
    """What ``ref status`` shows: whether the folder holds a database of every
    source and the GFF3, and the chromosomes every per-chromosome source covers."""
    manifest = load_manifest(sa_dir)
    by_source: dict[str, set[str]] = defaultdict(set)
    for name in _built(Path(sa_dir), manifest):
        entry = manifest["databases"][name]
        by_source[entry["source"]].add(entry.get("chromosome") or "")
    gff3 = str((manifest.get("gff3") or {}).get("file") or "")
    site_data = site_data_of(manifest)
    regions = region_tracks(Path(sa_dir), manifest)
    built = [
        s
        for s in SOURCES
        if s.key not in ("repeatmasker", *REGION_FLAGS)
        and (site_data == "library" or s.key not in PER_RUN)
    ]
    per_chromosome = [s.key for s in built if s.per_chromosome]
    return {
        "installed": bool(
            gff3 and Path(gff3).is_file() and all(by_source[s.key] for s in built)
        ),
        "folder": str(sa_dir),
        "siteData": site_data,
        "gff3": gff3,
        "chromosomes": [
            c for c in CHROMOSOMES if all(c in by_source[k] for k in per_chromosome)
        ],
        "releases": {
            key: entry.get("release", "")
            for key, entry in (manifest.get("sources") or {}).items()
        },
        "repeatMasker": bool(by_source["repeatmasker"]),
        "gnomadRegionTracks": sorted(regions),
        "gnomadRegionFlagChromosomes": [
            c
            for c in CHROMOSOMES
            if c in by_source["gnomad"]
            and all(
                flag in manifest["databases"][f"gnomad_{c}"].get("region_tracks", {})
                for flag in REGION_FLAGS
            )
        ],
    }


@dataclass
class Step:
    """One database to build (or the GFF3 to fetch), with what it costs."""

    name: str
    source: str
    chromosome: str | None = None
    download: int | None = 0  # bytes kept on disk; None when unknown
    stream: int | None = 0  # bytes read and not kept (gnomAD)
    database: int = 0
    temp: int = 0
    repair_index: bool = False


@dataclass
class Plan:
    reflib: Path
    sa_dir: Path
    release: str
    chromosomes: list[str]
    spliceai: Path | None  # an existing SpliceAI VCF; None downloads Ensembl's
    site_data: str = "library"  # per-run: gnomAD and SpliceAI aren't built
    steps: list[Step] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    # Genome-wide files the per-chromosome steps are cut from, still to fetch.
    downloads: dict[str, int | None] = field(default_factory=dict)

    @property
    def sources_dir(self) -> Path:
        return folder(self.reflib) / "sources"

    @property
    def gff3(self) -> Path:
        return folder(self.reflib) / GFF3_NAME.format(release=self.release)

    def needs(self, workers: int) -> dict[Path, int]:
        """Free space each folder written needs at the plan's peak (unknown
        sizes count as 0): the GFF3, the sources and the SpliceAI download
        go to the library, the databases and their staging to ``sa_dir``."""
        need: dict[Path, int] = defaultdict(int)
        for step in self.steps:
            # phyloP is downloaded into the staging folder and removed once built.
            kept = {"gff3": folder(self.reflib), "phylop": self.sa_dir}
            need[kept.get(step.source, self.sources_dir)] += step.download or 0
            need[self.sa_dir] += step.database
        # At most `workers` builds of each kind at a time: REVEL's CSVs are cut
        # beside the builds, at most `workers` of them waiting at once.
        for revel in (True, False):
            temps = sorted(
                (s.temp for s in self.steps if (s.source == "revel") == revel),
                reverse=True,
            )
            need[self.sa_dir] += sum(temps[: max(1, workers)])
        need[self.sources_dir] += self.downloads.get("revel") or 0
        need[library_spliceai_path(self.reflib).parent] += (
            self.downloads.get("spliceai") or 0
        )
        return {path: size for path, size in need.items() if size}

    def need_bytes(self, workers: int) -> int:
        """Free space the plan needs at its peak, all folders together."""
        return sum(self.needs(workers).values())


def make_plan(
    reflib: str | Path,
    sa_dir: str | Path,
    chromosomes: list[str],
    release: str,
    spliceai: Path | None,
    *,
    site_data: str | None = None,
    probe: bool = False,
) -> Plan:
    """What a build of ``chromosomes`` still has to do; ``probe`` asks the
    servers for download sizes. ``site_data`` per-run leaves gnomAD and
    SpliceAI out (None: as the folder was built, else library)."""
    manifest = load_manifest(sa_dir)
    if spliceai is None and manifest.get("spliceai_file"):
        spliceai = Path(manifest["spliceai_file"])
    plan = Plan(
        Path(reflib),
        Path(sa_dir),
        release,
        chromosomes,
        spliceai,
        site_data or site_data_of(manifest),
    )
    built = _built(plan.sa_dir, manifest)
    regions = region_tracks(plan.sa_dir, manifest)
    repair = {
        flag: digest
        for flag in REGION_FLAGS
        if flag not in regions
        and (digest := verified_region_bed(plan.sa_dir, manifest, flag))
    }
    rebuild_regions = regions | repair

    def size(url: str) -> int | None:
        return _remote_size(url) if probe else None

    # Only a GFF3 the manifest records was checked: one an interrupted run
    # left is checked and recorded again (fetch reuses the file).
    gff3 = manifest.get("gff3") or {}
    if (
        gff3.get("file") == str(plan.gff3)
        and plan.gff3.is_file()
        and plan.gff3.stat().st_size == gff3.get("bytes")
    ):
        plan.done.append("gff3")
    else:
        url = ENSEMBL.format(release=release) + plan.gff3.name
        plan.steps.append(Step("gff3", "gff3", download=size(url)))
    for source in SOURCES:
        if plan.site_data == "per-run" and source.key in PER_RUN:
            continue
        if not source.per_chromosome:
            if source.key in built and (
                source.key not in REGION_FLAGS or source.key in regions
            ):
                plan.done.append(source.key)
            elif source.key in repair:
                # Only a staged replacement index, not a new mask or joint VCF.
                plan.steps.append(
                    Step(
                        source.key,
                        source.key,
                        database=1_000_000,
                        temp=(plan.sa_dir / region_name(source.key)).stat().st_size,
                        repair_index=True,
                    )
                )
            else:
                plan.steps.append(
                    Step(
                        source.key,
                        source.key,
                        download=size(source_url(source.key)),
                        database=int(_DB_BYTES[source.key]),
                        temp=_TEMP_BYTES.get(source.key, 0),
                    )
                )
            continue
        for chrom in chromosomes:
            name = f"{source.key}_{chrom}"
            if name in built and (
                source.key != "gnomad"
                or (
                    len(rebuild_regions) == len(REGION_FLAGS)
                    and manifest["databases"][name].get("region_tracks")
                    == rebuild_regions
                )
            ):
                plan.done.append(name)
                continue
            step = Step(name, source.key, chrom)
            if source.key in _DB_PER_SOURCE_BYTE:
                remote = size(source_url(source.key, chrom))
                if source.key == "gnomad":
                    step.download, step.stream = 0, remote
                else:
                    step.download = remote
                step.database = int((remote or 0) * _DB_PER_SOURCE_BYTE[source.key])
                step.temp = int(
                    (remote or 0) * _TEMP_PER_SOURCE_BYTE.get(source.key, 0)
                )
            else:
                step.database = int(_MB[chrom] * _DB_PER_MB[source.key])
                step.temp = int(_MB[chrom] * _TEMP_PER_MB[source.key])
            plan.steps.append(step)
    todo = {s.source for s in plan.steps}
    if "revel" in todo and not (plan.sources_dir / Path(REVEL_URL).name).is_file():
        plan.downloads["revel"] = size(REVEL_URL)
    if "spliceai" in todo and spliceai is None and not library_spliceai(reflib):
        plan.downloads["spliceai"] = size(SPLICEAI_URL)
    return plan


def library_spliceai_path(reflib: str | Path) -> Path:
    """Where ``ref spliceai`` installs the library's GRCh38 SpliceAI VCF."""
    return download_path(reflib, "spliceai_hg38.vcf.gz")


def library_spliceai(reflib: str | Path) -> Path | None:
    """The library's GRCh38 SpliceAI VCF, when installed and indexed."""
    path = library_spliceai_path(reflib)
    return path if path.is_file() and Path(f"{path}.tbi").is_file() else None


def destinations(
    reflib: str | Path, sa_dir: str | Path, release: str
) -> tuple[list[Path], list[Path]]:
    """The folders ``ref acmg-data`` writes in, and the files it names there
    (a server checks each, links resolved, against its allowed folders)."""
    acmg, sa = folder(reflib), Path(sa_dir)
    spliceai = library_spliceai_path(reflib)
    return (
        [acmg, acmg / "sources", spliceai.parent, sa, sa / ".building"],
        [
            acmg / GFF3_NAME.format(release=release),
            acmg / "acmg_data.manifest.json",
            spliceai,
            sa / MANIFEST,
        ],
    )


def _remote_size(url: str) -> int | None:
    try:
        length = _head(url).get("Content-Length") or ""
    except (OSError, HTTPException) as e:
        logging.debug("No size for %s: %s", url, e)
        return None
    return int(length) if length.isdigit() else None


def _head(url: str) -> Any:
    request = Request(url, method="HEAD", headers={"User-Agent": user_agent(url)})
    with urlopen(request, timeout=60) as response:
        return response.headers


def _text(url: str) -> str:
    request = Request(url, headers={"User-Agent": user_agent(url)})
    with urlopen(request, timeout=60) as response:
        return str(response.read().decode())


def describe(plan: Plan, workers: int) -> Iterator[str]:
    """The plan as text: each source, its terms, and the sizes."""
    by_source: dict[str, list[Step]] = defaultdict(list)
    for step in plan.steps:
        by_source[step.source].append(step)

    def total(values: list[int | None]) -> str:
        return (
            "unknown" if None in values else format_bytes(sum(v or 0 for v in values))
        )

    gff3 = by_source.get("gff3")
    yield f"ACMG databases: {plan.sa_dir}"
    yield (
        f"  Ensembl GFF3 release {plan.release}: "
        + (f"download {total([gff3[0].download])}" if gff3 else "present")
    )
    yield f"    {GFF3_TERMS}"
    for source in SOURCES:
        steps = by_source.get(source.key, [])
        if plan.site_data == "per-run" and source.key in PER_RUN:
            yield (
                f"  {source.label}: not built; vcf acmg reads each input's own "
                "sites per run"
            )
        elif not steps:
            yield f"  {source.label}: built"
        else:
            if source.key == "gnomad":
                fetch = f"stream {total([s.stream for s in steps])}"
            elif source.key == "spliceai" and "spliceai" not in plan.downloads:
                fetch = f"read {plan.spliceai or library_spliceai(plan.reflib)}"
            elif source.key in plan.downloads:
                fetch = f"download {total([plan.downloads[source.key]])}"
            else:
                fetch = f"download {total([s.download for s in steps])}"
            todo = [s.chromosome for s in steps if s.chromosome]
            scope = (
                ""
                if not todo
                else " (all chromosomes)"
                if todo == list(CHROMOSOMES)
                else f" ({', '.join(todo)})"
            )
            yield (
                f"  {source.label}{scope}: {fetch}, build about "
                f"{format_bytes(sum(s.database for s in steps))}"
            )
        yield f"    {source.terms}"
    yield (
        f"Disk needed: about {format_bytes(plan.need_bytes(workers))} "
        f"({workers} chromosome build(s) at a time)."
    )
    needs = plan.needs(workers)
    if len(needs) > 1:
        for path, size in needs.items():
            yield f"  {path}: {format_bytes(size)}"


def _existing(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def check_disk(plan: Plan, workers: int) -> None:
    """Refuse a build the free space can't hold, on each filesystem written:
    the library's (the GFF3, the sources, the SpliceAI download) and the
    databases' (``--sa-dir`` may be on another drive)."""
    by_device: dict[int, tuple[list[Path], int, int]] = {}
    for path, need in plan.needs(workers).items():
        probe = _existing(path)
        device = probe.stat().st_dev
        folders, total, free = by_device.get(
            device, ([], 0, shutil.disk_usage(probe).free)
        )
        by_device[device] = ([*folders, path], total + need, free)
    for folders, need, free in by_device.values():
        if free < need:
            raise WGSExtractError(
                f"ref acmg-data needs about {format_bytes(need)} free for "
                f"{', '.join(map(str, folders))} and {format_bytes(free)} is free "
                "there. Free some space, build fewer --chromosomes, or fewer at a "
                "time (--threads)."
            )
