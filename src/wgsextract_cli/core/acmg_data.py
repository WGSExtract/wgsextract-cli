"""``ref acmg-data``: download the sources of ``vcf acmg``'s fastVEP databases
from their publishers and build the databases (issue 629).

Each download is checked against the checksum its publisher gives where there
is one (NCBI and UCSC MD5 files, Google Cloud Storage MD5 hashes, Ensembl's
CHECKSUMS), and every file's SHA-256 goes into the folder's
``acmg_data.json``. The sources are the user's own downloads under their
publishers' terms, which the command shows; WGS Extract never mirrors them.
gnomAD's 877 GB of joint sites are streamed, one chromosome at a time, and
only the INFO keys fastVEP's builder reads are kept. Each database is built in
a staging folder and moved in when complete, and the manifest records it, so
a rerun resumes with what is missing.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterator
from concurrent.futures import Future, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from typing import IO, Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from wgsextract_cli.core import read_scope
from wgsextract_cli.core.acmg_manifest import MANIFEST as MANIFEST
from wgsextract_cli.core.acmg_manifest import SITE_DATA as SITE_DATA
from wgsextract_cli.core.acmg_manifest import load_manifest as load_manifest
from wgsextract_cli.core.acmg_manifest import record_site_data as record_site_data
from wgsextract_cli.core.acmg_manifest import save_manifest
from wgsextract_cli.core.acmg_manifest import site_data_of as site_data_of
from wgsextract_cli.core.builds import CHR1_LENGTH_BUILDS
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import get_tool_version
from wgsextract_cli.core.download_progress import CancelEvent, format_bytes
from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor
from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.ref_library import download_file
from wgsextract_cli.core.ref_library.segmented import user_agent
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    popen,
    run_command,
    wait_with_cancel,
)

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
        "genomes_filters",
        *(f"AF_joint_{group}" for group in (
            "afr", "amr", "asj", "eas", "fin", "mid", "nfe", "remaining", "sas")),
    )
)  # fmt: skip


@dataclass(frozen=True)
class Source:
    """One of the eight sources: ``key`` is its ``fastvep sa-build --source``."""

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
}
# Staging bytes a per-chromosome build holds at its peak: gnomAD's reduced VCF
# per byte streamed, the SpliceAI cut and the REVEL rows per megabase.
_TEMP_PER_SOURCE_BYTE = {"gnomad": 0.03}
_TEMP_PER_MB = {"spliceai": 9.4e6, "revel": 2.1e6}


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
    return Path(reflib, "ref", "acmg")


def default_sa_dir(reflib: str | Path) -> Path:
    return folder(reflib) / "sa"


def acmg_sa_dir(
    library_root: str | Path | None, sa_dir: str | Path | None
) -> Path | None:
    """The databases ``vcf acmg`` reads: ``sa_dir`` when given (or
    configured), else the library's ``ref/acmg/sa`` when it is there."""
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
    built = [s for s in SOURCES if site_data == "library" or s.key not in PER_RUN]
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
    probe: bool = True,
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
            if source.key in built:
                plan.done.append(source.key)
            else:
                plan.steps.append(
                    Step(
                        source.key,
                        source.key,
                        download=size(source_url(source.key)),
                        database=int(_DB_BYTES[source.key]),
                    )
                )
            continue
        for chrom in chromosomes:
            name = f"{source.key}_{chrom}"
            if name in built:
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
    return Path(reflib, "ref", "spliceai_hg38.vcf.gz")


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


def _gcs_md5(headers: Any) -> str | None:
    """The MD5 Google Cloud Storage gives in x-goog-hash, as hex."""
    for value in headers.get_all("x-goog-hash") or []:
        for part in value.split(","):
            kind, _, digest = part.strip().partition("=")
            if kind == "md5":
                return base64.b64decode(digest).hex()
    return None


def _http_date(headers: Any) -> str:
    stamp = headers.get("Last-Modified")
    try:
        return parsedate_to_datetime(stamp).date().isoformat() if stamp else ""
    except (TypeError, ValueError):
        return ""


def bsd_sum(path: Path) -> tuple[int, int]:
    """``sum``'s default (BSD) checksum and 1 KB block count, which Ensembl's
    CHECKSUMS files give."""
    checksum = size = 0
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            size += len(chunk)
            for byte in chunk:
                checksum = (((checksum >> 1) | ((checksum & 1) << 15)) + byte) & 0xFFFF
    return checksum, -(-size // 1024)


def _digests(path: Path) -> tuple[str, str]:
    """(SHA-256, MD5) of a file, read once."""
    sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        while chunk := f.read(8 << 20):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


def _sha256(path: Path, cancel: CancelEvent | None = None) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8 << 20):
            if cancel and cancel.is_set():
                raise WGSExtractError("Cancelled.")
            sha.update(chunk)
    return sha.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Stop:
    """Set when the run stops (Ctrl-C, SIGTERM) or its caller cancels it: the
    builds still running stop at their next check and their tools are killed."""

    def __init__(self, outer: CancelEvent | None) -> None:
        self.outer = outer
        self.event = threading.Event()

    def set(self) -> None:
        self.event.set()

    def is_set(self) -> bool:
        return self.event.is_set() or bool(self.outer and self.outer.is_set())


def run_tool(command: list[str], cancel: CancelEvent | None) -> str:
    """Run a tool, cancellably: its output, or its failure."""
    with tempfile.TemporaryFile() as log:
        process = popen(command, stdout=log, stderr=subprocess.STDOUT)
        ok = wait_with_cancel(process, cancel)
        log.seek(0)
        output = log.read().decode(errors="replace")
    if not ok:
        raise WGSExtractError(
            "Cancelled."
            if cancel and cancel.is_set()
            else f"{Path(command[0]).name} {command[1]} failed "
            f"(exit {process.returncode}): {output.strip()[-1500:]}"
        )
    return output


def sa_build(
    fastvep: str,
    key: str,
    inputs: Path,
    work: Path,
    name: str,
    cancel: CancelEvent | None,
) -> tuple[list[Path], int]:
    """``fastvep sa-build`` of source ``key`` from ``inputs`` into a fresh
    ``work`` folder as ``name``: the files it wrote, and its record count."""
    source = BY_KEY[key]
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    command = [
        fastvep, "sa-build", "--source", source.key, "-i", str(inputs),
        "-o", str(work / name), "--assembly", ASSEMBLY, "--no-progress",
    ]  # fmt: skip
    if source.chunk_bits:
        command += ["--chunk-bits", str(source.chunk_bits)]
    logging.info("Building %s...", name)
    output = run_tool(command, cancel)
    records = re.findall(r"Wrote: .*\(([\d,]+) (?:records|genes)", output)
    built = sorted(work.iterdir())
    if not records or not built:
        raise WGSExtractError(
            f"fastvep sa-build wrote no {name} database: {output[-500:]}"
        )
    return built, int(records[-1].replace(",", ""))


class _Build:
    """A run of a plan: the manifest is saved as each step completes."""

    def __init__(
        self,
        plan: Plan,
        cancel: CancelEvent | None,
        on_output: Callable[[Path], None],
        workers: int = 1,
    ) -> None:
        self.plan = plan
        self.fastvep = get_tool_path("fastvep") or "fastvep"
        self.cancel = Stop(cancel)
        self.on_output = on_output
        self.lock = threading.Lock()
        self.manifest = load_manifest(plan.sa_dir)
        self.manifest.update(format=1, assembly=ASSEMBLY, site_data=plan.site_data)
        if plan.spliceai is not None:
            self.manifest["spliceai_file"] = str(plan.spliceai.resolve())
        self.manifest.setdefault("sources", {})
        self.manifest.setdefault("databases", {})
        self.staging = plan.sa_dir / ".building"
        self.phylop_md5: dict[str, str] | None = None
        # REVEL CSVs cut and not yet built: at most one per worker.
        self.revel_slots = threading.Semaphore(max(1, workers))
        self.spliceai: Path | None = plan.spliceai

    def _save(self) -> None:
        self.manifest["updated"] = _now()
        path = self.plan.sa_dir / MANIFEST
        save_manifest(path, self.manifest)

    def record_source(
        self,
        key: str,
        file: str,
        entry: dict[str, Any],
        *,
        label: str | None = None,
        url: str | None = None,
    ) -> None:
        """Record a source file; ``label`` and ``url`` say where a file that
        isn't the publisher download came from."""
        source = BY_KEY[key]
        with self.lock:
            info = self.manifest["sources"].setdefault(key, {})
            info.update(
                label=source.label if label is None else label,
                url=source_url(key) if url is None else url,
                terms=source.terms,
            )
            if "release" in entry:
                info["release"] = entry.pop("release")
            info.setdefault("files", {})[file] = entry
            self._save()

    def record_database(self, step: Step, entry: dict[str, Any]) -> None:
        with self.lock:
            self.manifest["databases"][step.name] = {
                "source": step.source,
                "chromosome": step.chromosome,
                **entry,
            }
            self._save()

    def fetch(
        self,
        url: str,
        dest: Path,
        *,
        md5: str | None = None,
        check: Callable[[Path], str] | None = None,
    ) -> dict[str, Any]:
        """Download ``url`` to ``dest`` (resuming a ``.partial``), check it
        against ``md5`` or with ``check`` (which says what it verified), and
        return its manifest entry."""
        if not read_scope.may_follow(dest):
            raise WGSExtractError(f"{dest} leads outside the folders this run may use.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            logging.info("Downloading %s...", url)
            # Not the download cache: it is keyed by the URL, and most of these
            # URLs name the latest release, checked only after the download.
            if not download_file(
                url,
                str(dest),
                None,
                self.cancel,
                resolve_release_digest=False,
                use_cache=False,
            ):
                raise WGSExtractError(
                    f"Downloading {url} failed; rerun the command to resume it."
                )
        sha256, actual_md5 = _digests(dest)
        verified = "none published"
        if md5 is not None:
            if actual_md5 != md5.lower():
                dest.unlink()
                raise WGSExtractError(
                    f"{dest.name} doesn't match the MD5 its publisher gives "
                    f"({actual_md5}, expected {md5}), so it was removed: it may "
                    "have changed during the download. Rerun to fetch it again."
                )
            verified = f"MD5 {md5.lower()}"
        elif check is not None:
            verified = check(dest)
        return {
            "file": str(dest),
            "bytes": dest.stat().st_size,
            "sha256": sha256,
            "verified": verified,
            "downloaded": _now(),
        }

    def database(self, step: Step, inputs: Path, *, keep_input: bool = True) -> None:
        """``fastvep sa-build`` into the staging folder, then moved in."""
        work = self.staging / step.name
        built, records = sa_build(
            self.fastvep, step.source, inputs, work, step.name, self.cancel
        )
        for path in built:
            path.replace(self.plan.sa_dir / path.name)
            self.on_output(self.plan.sa_dir / path.name)
        shutil.rmtree(work, ignore_errors=True)
        if not keep_input:
            inputs.unlink(missing_ok=True)
        main = self.plan.sa_dir / built[0].name
        self.record_database(
            step,
            {
                "file": main.name,
                "files": [p.name for p in built],
                "records": records,
                "bytes": main.stat().st_size,
                "sha256": _digests(main)[0],
                "built": _now(),
            },
        )

    def absent(self, step: Step, reason: str) -> None:
        logging.warning("No %s database: %s.", step.name, reason)
        self.record_database(step, {"absent": True, "reason": reason})

    def run(self, step: Step) -> None:
        if self.cancel and self.cancel.is_set():
            raise WGSExtractError("Cancelled.")
        getattr(self, f"step_{step.source}")(step)

    def step_gff3(self, step: Step) -> None:
        base = ENSEMBL.format(release=self.plan.release)
        name = self.plan.gff3.name
        listed = {
            parts[2]: (int(parts[0]), int(parts[1]))
            for line in _text(base + "CHECKSUMS").splitlines()
            if len(parts := line.split()) == 3 and parts[0].isdigit()
        }
        if name not in listed:
            raise WGSExtractError(
                f"Ensembl release {self.plan.release} has no {name}; check "
                "--ensembl-release (it should match your vep-rs cache)."
            )

        def check(path: Path) -> str:
            found = bsd_sum(path)
            if found != listed[name]:
                path.unlink()
                raise WGSExtractError(
                    f"{name} doesn't match Ensembl's CHECKSUMS {listed[name]} "
                    f"(got {found}), so it was removed; rerun to fetch it again."
                )
            return f"Ensembl CHECKSUMS sum {found[0]} {found[1]}"

        entry = self.fetch(base + name, self.plan.gff3, check=check)
        with self.lock:
            self.manifest["gff3"] = {
                **entry,
                "url": base + name,
                "release": self.plan.release,
                "terms": GFF3_TERMS,
            }
            self._save()
        self.on_output(self.plan.gff3)

    def step_clinvar(self, step: Step) -> None:
        digest, _, remote = _text(CLINVAR_URL + ".md5").strip().partition(" ")
        release = re.search(r"(\d{8})", remote)
        dest = self.plan.sources_dir / Path(remote.strip()).name
        entry = self.fetch(CLINVAR_URL, dest, md5=digest)
        entry["release"] = release.group(1) if release else ""
        self.record_source("clinvar", dest.name, entry)
        self.database(step, dest)

    def step_clinvar_protein(self, step: Step) -> None:
        digest = _text(VARIANT_SUMMARY_URL + ".md5").split()[0]
        release = _http_date(_head(VARIANT_SUMMARY_URL))
        dest = self.plan.sources_dir / Path(VARIANT_SUMMARY_URL).name
        entry = self.fetch(VARIANT_SUMMARY_URL, dest, md5=digest)
        entry["release"] = release
        self.record_source("clinvar_protein", dest.name, entry)
        self.database(step, dest)

    def step_gnomad_genes(self, step: Step) -> None:
        dest = self.plan.sources_dir / Path(CONSTRAINT_URL).name
        entry = self.fetch(CONSTRAINT_URL, dest, md5=_gcs_md5(_head(CONSTRAINT_URL)))
        entry["release"] = "4.1"
        self.record_source("gnomad_genes", dest.name, entry)
        self.database(step, dest)

    def step_omim(self, step: Step) -> None:
        dest = self.plan.sources_dir / "clingen_gene_validity.csv"
        entry = self.fetch(CLINGEN_URL, dest)
        entry["release"] = entry["downloaded"][:10]
        self.record_source("omim", dest.name, entry)
        converted = self.staging / "clingen_gdv.tsv"
        converted.parent.mkdir(parents=True, exist_ok=True)
        genes = clingen_to_genemap(dest, converted)
        logging.info("ClinGen gene-disease validity: %d genes.", genes)
        self.database(step, converted, keep_input=False)

    def step_gnomad(self, step: Step) -> None:
        assert step.chromosome
        url = source_url("gnomad", step.chromosome)
        headers = _head(url)
        size = int(headers.get("Content-Length") or 0)
        md5 = _gcs_md5(headers)
        reduced = self.staging / f"{step.name}.vcf.gz"
        reduced.parent.mkdir(parents=True, exist_ok=True)
        logging.info("Streaming gnomAD %s (%s)...", step.chromosome, format_bytes(size))
        streamed = stream_reduced(url, size, reduced, self.cancel)
        if md5 and streamed != md5:
            reduced.unlink(missing_ok=True)
            raise WGSExtractError(
                f"gnomAD {step.chromosome} didn't match the MD5 Google Cloud "
                f"Storage gives ({streamed}, expected {md5}); rerun to stream it "
                "again."
            )
        self.record_source(
            "gnomad",
            Path(url).name,
            {
                "bytes": size,
                "md5": streamed,
                "verified": f"MD5 {md5}" if md5 else "none published",
                "streamed": _now(),
                "release": "4.1",
            },
        )
        self.database(step, reduced, keep_input=False)

    def step_phylop(self, step: Step) -> None:
        assert step.chromosome
        url = source_url("phylop", step.chromosome)
        name = Path(url).name
        with self.lock:
            if self.phylop_md5 is None:
                self.phylop_md5 = {
                    parts[1]: parts[0]
                    for line in _text(PHYLOP_DIR + "md5sum.txt").splitlines()
                    if len(parts := line.split()) == 2
                }
            md5 = self.phylop_md5.get(name)
        dest = self.staging / name
        entry = self.fetch(url, dest, md5=md5)
        del entry["file"]  # removed once built
        entry["release"] = "hg38.phyloP100way"
        self.record_source("phylop", name, entry)
        self.database(step, dest, keep_input=False)

    def produce_revel(
        self, steps: list[Step], submit: Callable[[Step, Path], None]
    ) -> None:
        """Cut REVEL into one CSV per chromosome being built, which bounds
        sa-build's memory, handing each to ``submit`` once complete. A CSV is
        started only when one of the ``workers`` slots is free, so at most that
        many wait on disk; the build of each frees its slot."""
        dest = self.plan.sources_dir / Path(REVEL_URL).name
        recorded = self.manifest["sources"].get("revel", {}).get("files", {})
        if dest.name not in recorded or not dest.is_file():
            entry = self.fetch(REVEL_URL, dest)
            entry["release"] = "1.3"
            self.record_source("revel", dest.name, entry)

        def slot() -> None:
            while not self.revel_slots.acquire(timeout=1):
                if self.cancel.is_set():
                    raise WGSExtractError("Cancelled.")

        by_chromosome = {s.chromosome: s for s in steps}
        logging.info("Splitting REVEL by chromosome...")
        for chromosome, part in split_revel(
            dest, [c for c in by_chromosome if c], self.staging, slot, self.cancel
        ):
            submit(by_chromosome.pop(chromosome), part)
        for step in by_chromosome.values():
            self.absent(step, f"REVEL has no rows on {step.chromosome}")

    def build_revel(self, step: Step, part: Path) -> None:
        try:
            self.database(step, part, keep_input=False)
        finally:
            part.unlink(missing_ok=True)
            self.revel_slots.release()

    def prepare_spliceai(self) -> None:
        """The SpliceAI VCF to cut: the one given, else the library's (as
        ``ref spliceai`` installs it), downloaded from Ensembl when missing.
        Its SHA-256 and where it came from go into the manifest."""
        downloaded = False
        if self.spliceai is None:
            from wgsextract_cli.core.annotation_resources import download_spliceai

            reflib = self.plan.reflib
            if not library_spliceai(reflib):
                # An unindexed file already there is indexed, not downloaded.
                downloaded = not library_spliceai_path(reflib).is_file()
                if not download_spliceai(str(reflib), self.cancel, builds=("hg38",)):
                    raise WGSExtractError(
                        "Downloading the SpliceAI scores failed; rerun to resume."
                    )
            self.spliceai = library_spliceai(reflib)
            if self.spliceai is None:
                raise WGSExtractError("The SpliceAI download left no indexed file.")
        path = self.spliceai
        if not read_scope.may_follow(path):
            raise WGSExtractError(f"{path} leads outside the folders this run may use.")
        header = require_grch38_vcf(path)
        date = next(
            (h.split("=", 1)[1] for h in header if h.startswith("##fileDate=")), ""
        )
        if not any(Path(f"{path}{ext}").is_file() for ext in (".tbi", ".csi")):
            raise WGSExtractError(
                f"{path} has no tabix index; index it with 'tabix -p vcf {path}'."
            )
        stat = path.stat()
        info = self.manifest["sources"].get("spliceai", {})
        before = info.get("files", {}).get(path.name, {})
        unchanged = bool(before.get("sha256")) and (
            before.get("file"),
            before.get("bytes"),
            before.get("mtime_ns"),
        ) == (str(path), stat.st_size, stat.st_mtime_ns)
        # Where the file came from is recorded with it: the source's label is
        # that of the file used last, which may have been another.
        if self.plan.spliceai:
            origin = {
                "label": "SpliceAI scores (given with --spliceai-file)",
                "url": "",
            }
            verified = "given with --spliceai-file"
        elif downloaded:
            origin = {"label": BY_KEY["spliceai"].label, "url": SPLICEAI_URL}
            verified = "BGZF end-of-file block and tabix index (ref spliceai)"
        elif unchanged and "label" in before:
            # The file an earlier run recorded, untouched since.
            origin = {"label": before["label"], "url": before.get("url", "")}
            verified = str(before.get("verified", ""))
        else:
            origin = {
                "label": f"SpliceAI scores (the library's {path.name}, installed "
                "before this run; origin not recorded)",
                "url": "",
            }
            verified = "found in the library"
        if unchanged:
            sha256 = str(before["sha256"])
        else:
            logging.info("Hashing %s...", path.name)
            sha256 = _sha256(path, self.cancel)
        self.record_source(
            "spliceai",
            path.name,
            {
                "file": str(path),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256,
                "verified": verified,
                "release": date,
                **origin,
            },
            **origin,
        )

    def step_spliceai(self, step: Step) -> None:
        assert step.chromosome and self.spliceai is not None
        contigs = tabix_contigs(self.spliceai)
        contig = next(
            (c for c in (step.chromosome, step.chromosome[3:]) if c in contigs), None
        )
        if contig is None:
            self.absent(step, f"{self.spliceai.name} has no {step.chromosome}")
            return
        cut = self.staging / f"{step.name}.vcf.gz"
        cut.parent.mkdir(parents=True, exist_ok=True)
        with cut.open("wb") as out:
            tabix = popen(
                ["tabix", "-h", str(self.spliceai), contig], stdout=subprocess.PIPE
            )
            bgzip = popen(["bgzip", "-c"], stdin=tabix.stdout, stdout=out)
            assert tabix.stdout is not None
            tabix.stdout.close()
            ok = wait_with_cancel(bgzip, self.cancel) and tabix.wait() == 0
        if not ok:
            cut.unlink(missing_ok=True)
            raise WGSExtractError(f"Cutting {contig} out of {self.spliceai} failed.")
        self.database(step, cut, keep_input=False)


def stream_reduced(
    url: str, size: int, dest: Path, cancel: CancelEvent | None, attempts: int = 6
) -> str:
    """Stream a gnomAD VCF through bcftools into ``dest``, keeping only
    ``GNOMAD_KEEP``. A dropped connection resumes at the byte it stopped at, so
    bcftools reads the file whole; returns the MD5 of what was streamed."""
    partial = dest.with_name(dest.name + ".partial")
    command = [
        "bcftools", "annotate", "--no-version", "-x", f"^{GNOMAD_KEEP}",
        "-Oz", "-o", str(partial), "-",
    ]  # fmt: skip
    digest = hashlib.md5(usedforsecurity=False)
    done = failures = 0
    with tempfile.TemporaryFile() as err:
        process = popen(command, stdin=subprocess.PIPE, stderr=err)
        assert process.stdin is not None
        sink = process.stdin
        try:
            while True:
                headers = {"User-Agent": user_agent(url)}
                if done:
                    headers["Range"] = f"bytes={done}-"
                try:
                    with urlopen(Request(url, headers=headers), timeout=120) as r:
                        if done and r.status != 206:
                            raise WGSExtractError(
                                f"{url} ignored a byte-range request, so its "
                                "stream can't resume."
                            )
                        while chunk := r.read(1 << 20):
                            if cancel and cancel.is_set():
                                raise WGSExtractError("Cancelled.")
                            sink.write(chunk)
                            digest.update(chunk)
                            done += len(chunk)
                    if done < size:
                        raise ConnectionError(f"the server sent {done} of {size} bytes")
                    break
                except BrokenPipeError:
                    break  # bcftools stopped: its exit status and stderr say why
                except HTTPError as e:
                    if e.code != 429 and e.code < 500:
                        raise WGSExtractError(f"Streaming {url} failed: {e}") from e
                    failures = _retry(url, done, size, e, failures, attempts)
                except (OSError, HTTPException) as e:
                    failures = _retry(url, done, size, e, failures, attempts)
        except BaseException:
            process.kill()
            process.wait()
            partial.unlink(missing_ok=True)
            raise
        try:
            sink.close()
        except BrokenPipeError:
            pass
        ok = wait_with_cancel(process, cancel)
        err.seek(0)
        detail = err.read().decode(errors="replace").strip()
    if not ok or done != size:
        partial.unlink(missing_ok=True)
        raise WGSExtractError(
            f"Reducing {Path(url).name} failed after {done} of {size} bytes: "
            f"{detail[-800:] or f'bcftools exited with {process.returncode}'}"
        )
    partial.replace(dest)
    return digest.hexdigest()


def _retry(
    url: str, done: int, size: int, error: Exception, failures: int, attempts: int
) -> int:
    failures += 1
    if failures >= attempts:
        raise WGSExtractError(
            f"Streaming {url} failed at byte {done} of {size}: {error}"
        ) from error
    logging.warning(
        "Streaming %s stopped at %s (%s); resuming...",
        Path(url).name,
        format_bytes(done),
        error,
    )
    time.sleep(min(30, 2**failures))
    return failures


def split_revel(
    zipped: Path,
    chromosomes: list[str],
    out_dir: Path,
    before_each: Callable[[], None] = lambda: None,
    cancel: CancelEvent | None = None,
) -> Iterator[tuple[str, Path]]:
    """Write the REVEL v1.3 rows of each of ``chromosomes`` to its own CSV,
    with the header, in ``out_dir``, yielding each (chromosome, CSV) as it is
    complete. REVEL's rows come a chromosome at a time, so one CSV is written
    at a time; ``before_each`` runs before each is started (and may wait)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = {c.removeprefix("chr"): c for c in chromosomes}
    finished: set[str] = set()
    current: tuple[str, Path, IO[str]] | None = None
    try:
        with zipfile.ZipFile(zipped) as archive:
            members = [m for m in archive.namelist() if not m.endswith("/")]
            if len(members) != 1:
                raise WGSExtractError(
                    f"{zipped.name} should hold one REVEL CSV; it holds {members}."
                )
            with archive.open(members[0]) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                header = text.readline()
                if not header.startswith("chr,hg19_pos,grch38_pos,"):
                    raise WGSExtractError(
                        f"{zipped.name} isn't REVEL v1.3's CSV (its header is "
                        f"{header.strip()[:80]!r})."
                    )
                for number, line in enumerate(text):
                    if number & 0xFFFFF == 0 and cancel and cancel.is_set():
                        raise WGSExtractError("Cancelled.")
                    chrom = keys.get(line.split(",", 1)[0])
                    if current and chrom != current[0]:
                        name, path, out = current
                        current = None
                        out.close()
                        finished.add(name)
                        yield name, path
                    if chrom is None:
                        continue
                    if current is None:
                        if chrom in finished:
                            raise WGSExtractError(
                                f"{zipped.name} has {chrom} rows apart from the "
                                "others; REVEL v1.3 lists them together."
                            )
                        before_each()
                        path = out_dir / f"revel_{chrom}.csv"
                        current = (chrom, path, path.open("w", newline=""))
                        current[2].write(header)
                    current[2].write(line)
        if current:
            name, path, out = current
            current = None
            out.close()
            yield name, path
    except zipfile.BadZipFile as e:
        zipped.unlink(missing_ok=True)
        raise WGSExtractError(
            f"{zipped.name} is not a complete zip ({e}); it was removed, rerun "
            "to fetch it again."
        ) from e
    finally:
        if current:
            current[2].close()
            current[1].unlink(missing_ok=True)


def tabix_contigs(path: Path) -> set[str]:
    listed = run_command(["tabix", "-l", str(path)], capture_output=True).stdout
    return set(listed.split())


def require_grch38_vcf(path: Path) -> list[str]:
    """Refuse a VCF whose header says another assembly (see
    ``require_grch38_header``). Returns the header."""
    header = run_command(["bcftools", "view", "-h", str(path)], capture_output=True)
    lines = header.stdout.splitlines()
    require_grch38_header(lines, str(path))
    return lines


def require_grch38_header(lines: list[str], name: str) -> None:
    """Refuse a SpliceAI VCF header that says another assembly: its chromosome
    1 length, else a ``##reference`` line naming GRCh38 or hg38."""
    for line in lines:
        match = re.match(r"##contig=<ID=(?:chr)?1,.*length=(\d+)", line)
        if match:
            build = CHR1_LENGTH_BUILDS.get(int(match.group(1)))
            if build == "hg38":
                return
            raise WGSExtractError(
                f"{name} is for {build or 'an unknown assembly'}; the databases "
                "are GRCh38, so give a GRCh38 SpliceAI VCF."
            )
    if any(
        line.startswith("##reference") and re.search(r"GRCh38|hg38", line)
        for line in lines
    ):
        return
    raise WGSExtractError(
        f"{name}'s header doesn't say it is GRCh38 (no chromosome 1 length or "
        "##reference line); give a GRCh38 SpliceAI VCF."
    )


# ClinGen gene-disease validity as the genemap2-style table fastVEP's `omim`
# source reads, after fastVEP's analysis/acmg_benchmark/scripts/sa_sources/
# clingen_gdv_to_oga.py (Apache-2.0). Every classification is kept, the weak
# and negative ones too: the classifier tells "curated and found wanting"
# apart from "not curated".
_CLINGEN_COLUMNS = (
    "GENE SYMBOL",
    "DISEASE LABEL",
    "DISEASE ID (MONDO)",
    "MOI",
    "CLASSIFICATION",
)
_CLINGEN_MOI = {"AD", "AR", "XL", "SD", "MT", "UD"}


def clingen_to_genemap(src: Path, dst: Path) -> int:
    """Convert ClinGen's gene-validity CSV export; returns the gene count."""
    by_gene: dict[str, list[str]] = defaultdict(list)
    columns: dict[str, int] | None = None
    unknown_moi: dict[str, int] = defaultdict(int)
    with src.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row:
                continue
            if row[0] == "GENE SYMBOL":
                missing = [c for c in _CLINGEN_COLUMNS if c not in row]
                if missing:
                    raise WGSExtractError(
                        f"{src.name}'s header has no column {missing}; ClinGen's "
                        "export may have changed."
                    )
                columns = {c: row.index(c) for c in _CLINGEN_COLUMNS}
                continue
            gene = row[0].strip('"')
            if not gene or gene.startswith(("+", "FILE", "WEBPAGE", "CLINGEN")):
                continue
            if columns is None or len(row) <= max(columns.values()):
                continue
            classification = row[columns["CLASSIFICATION"]].strip('"')
            if not classification:
                continue
            moi = row[columns["MOI"]].strip('"')
            if moi not in _CLINGEN_MOI:
                unknown_moi[moi] += 1
            disease = row[columns["DISEASE LABEL"]].strip('"')
            mondo = row[columns["DISEASE ID (MONDO)"]].strip('"') or "."
            by_gene[gene].append(f"{disease} (ClinGen {classification}/{moi}, {mondo})")
    if columns is None:
        raise WGSExtractError(
            f"{src.name} has no GENE SYMBOL header row, so it isn't a ClinGen "
            "gene-disease validity export."
        )
    if unknown_moi:
        logging.info(
            "ClinGen curations with a mode of inheritance fastVEP doesn't know "
            "are read as having none: %s",
            dict(sorted(unknown_moi.items())),
        )
    with dst.open("w", newline="\n") as out:
        out.write(
            "# Synthetic genemap2 from ClinGen Gene-Disease Validity\n"
            "# col5=mim_proxy (always 0), col8=gene_symbol, col12=phenotypes\n"
        )
        for gene, phenotypes in sorted(by_gene.items()):
            cols = [""] * 13
            cols[5], cols[8], cols[12] = "0", gene, "; ".join(phenotypes)
            out.write("\t".join(cols) + "\n")
    return len(by_gene)


def build(
    plan: Plan,
    workers: int,
    cancel: CancelEvent | None,
    on_output: Callable[[Path], None],
) -> None:
    """Carry out ``plan``: the GFF3, ClinVar and the gene-level sources first,
    then the per-chromosome databases ``workers`` at a time. A failed step
    leaves the others to finish; the run then fails, naming it. Stopped
    (Ctrl-C, SIGTERM), it starts nothing more and stops what is running."""
    plan.sa_dir.mkdir(parents=True, exist_ok=True)
    run = _Build(plan, cancel, on_output, workers)
    failed: dict[str, str] = {}

    def attempt(name: str, func: Callable[..., None], *args: Any) -> bool:
        try:
            func(*args)
            return True
        except (OSError, HTTPException, WGSExtractError) as e:
            logging.error("%s failed: %s", name, e)
            failed[name] = str(e)
            return False

    by_source: dict[str, list[Step]] = defaultdict(list)
    for step in plan.steps:
        by_source[step.source if step.chromosome else ""].append(step)
    for steps in by_source.values():
        steps.sort(key=lambda s: -_MB[s.chromosome or "chr1"])
    # SpliceAI's download and REVEL's split run beside the other builds, which
    # go first, the largest first so the last to finish is a short one; each
    # hands its builds over as their input is ready.
    revel = by_source.pop("revel", [])
    revel.sort(key=lambda s: CHROMOSOMES.index(s.chromosome or "chr1"))
    spliceai = by_source.pop("spliceai", [])
    pool = ContextThreadPoolExecutor(max_workers=max(1, workers))
    preparing = ContextThreadPoolExecutor(max_workers=2)
    futures: list[Future[Any]] = []
    submitting = threading.Lock()
    succeeded = False

    def submit(func: Callable[..., Any], *args: Any) -> None:
        with submitting:
            futures.append(pool.submit(func, *args))

    def prepare_spliceai() -> None:
        if attempt("spliceai", run.prepare_spliceai):
            for step in spliceai:
                submit(attempt, step.name, run.run, step)

    def prepare_revel() -> None:
        attempt(
            "revel",
            run.produce_revel,
            revel,
            lambda step, part: submit(attempt, step.name, run.build_revel, step, part),
        )

    def finish(future: Future[Any]) -> None:
        # An unbounded Future.result() cannot deliver pending Python signals
        # while its lock is waiting on Windows.
        while not future.done():
            wait((future,), timeout=0.1)
        future.result()

    try:
        for step in by_source.pop("", []):
            attempt(step.name, run.run, step)
        per_chromosome = sorted(
            (s for steps in by_source.values() for s in steps),
            key=lambda s: -_MB[s.chromosome or "chr1"],
        )
        for step in per_chromosome:
            submit(attempt, step.name, run.run, step)
        prepared = [
            preparing.submit(prepare)
            for prepare, steps in ((prepare_spliceai, spliceai), (prepare_revel, revel))
            if steps
        ]
        for future in prepared:
            finish(future)
        # Every build is submitted once the preparations are done.
        for future in list(futures):
            finish(future)
        succeeded = not failed
    except BaseException:
        run.cancel.set()
        raise
    finally:
        preparing.shutdown(cancel_futures=True)
        pool.shutdown(cancel_futures=True)
        _tidy_staging(run.staging, keep_downloads=not succeeded)
    if failed:
        raise WGSExtractError(
            "ref acmg-data didn't finish: "
            + "; ".join(f"{name}: {why.rstrip('.')}" for name, why in failed.items())
            + ". Rerun the command to resume; what was built and downloaded is kept."
        )


def _tidy_staging(staging: Path, *, keep_downloads: bool) -> None:
    """Remove the staging folder, or after a failure all of it but the phyloP
    downloads (and their resumable ``.partial`` files) a rerun picks up."""
    if not keep_downloads:
        shutil.rmtree(staging, ignore_errors=True)
        return
    for path in staging.iterdir() if staging.is_dir() else ():
        if PHYLOP_FILE.format(chrom="") in path.name:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


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
