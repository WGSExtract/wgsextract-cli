"""Population allele frequencies for `qc roh` (#1174).

bcftools roh reads frequencies from a tabix-indexed ``CHROM POS REF,ALT AF``
table whose contigs are named as the sample's. ``prepare`` makes that table
from a local population file (a gnomAD or 1000 Genomes sites VCF/BCF and an
INFO tag, or such a table) once, keeping biallelic SNVs with 0 < AF < 1, and
caches it in the reference library under ``roh/<key>/`` with the source's
identity. A later run reuses it while the source is unchanged and rebuilds it
when the source changes (a new release at the same path). Nothing is
downloaded: the file is the user's.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import logging
import os
import re
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.errors import CodedError
from wgsextract_cli.core.file_lock import build_lock
from wgsextract_cli.core.library_cache import short_key
from wgsextract_cli.core.panel import load_annotations
from wgsextract_cli.core.ref_library.resolver import header_contig_lengths
from wgsextract_cli.core.roh_regions import bare
from wgsextract_cli.core.step_documents import FileIdentity
from wgsextract_cli.core.step_identity import file_drift, file_identity
from wgsextract_cli.core.utils import popen, run_command

GUIDE = "docs/roh.md"
CACHE_FOLDER = "roh"
# Bump when the prepared table's content rules change, so old caches rebuild.
TABLE_VERSION = 1
_VCF_SUFFIXES = (".vcf", ".vcf.gz", ".vcf.bgz", ".bcf")
BUILDS = ("hg19", "hg38", "t2t")
AUTOSOMES = tuple(str(n) for n in range(1, 23))

Kind = Literal["vcf", "table"]
Prepared = Literal["built", "reused", "uncached"]


def header_contig_ids(header: str) -> list[str]:
    """Every contig a VCF header declares, with a length or without."""
    return re.findall(r"^##contig=<(?:[^>]*,)?ID=([^,>]+)", header, re.MULTILINE)


def build_of_lengths(lengths: dict[str, int]) -> str | None:
    """The build whose autosome lengths all the given autosomes match."""
    autosomes = {
        bare(name): n for name, n in lengths.items() if bare(name) in AUTOSOMES
    }
    if not autosomes:
        return None
    for build in BUILDS:
        known = {bare(k): v for k, v in load_annotations(build).contigs.items()}
        if all(known.get(name) == n for name, n in autosomes.items()):
            return build
    return None


@dataclass(frozen=True)
class AfSource:
    path: Path
    kind: Kind
    tag: str | None  # the INFO tag of a VCF source
    build: str
    release: str | None
    identity: FileIdentity


def _missing(path: Path) -> CodedError:
    return CodedError(
        "roh_af_missing",
        f"Population allele-frequency file {path} not found. qc roh needs local "
        "population frequencies on the sample's build: a gnomAD or 1000 Genomes "
        "sites VCF/BCF (--af-tag names its frequency field) or a bcftools roh "
        f"table (4 tab-separated columns: CHROM, POS, REF,ALT, AF). Nothing is downloaded for you; see "
        f"{GUIDE} for where to get one.",
    )


def inspect(
    path: Path, tag: str, declared_build: str | None, release: str | None
) -> AfSource:
    """Check the AF file and find its build (header contig lengths, else
    ``declared_build``)."""
    if not path.is_file():
        raise _missing(path)
    kind: Kind = "vcf" if path.name.lower().endswith(_VCF_SUFFIXES) else "table"
    declared = canonical_build(declared_build) if declared_build else None
    found = None
    if kind == "vcf":
        header = run_command(
            ["bcftools", "view", "-h", str(path)], capture_output=True
        ).stdout
        tags = re.findall(r"^##INFO=<ID=([^,>]+)", header, re.MULTILINE)
        if tag not in tags:
            frequency = [t for t in tags if "AF" in t.upper()][:12]
            raise CodedError(
                "roh_af_tag_missing",
                f"{path.name} has no INFO/{tag} field; choose its allele-frequency "
                f"field with --af-tag (it has: {', '.join(frequency) or 'none'}).",
            )
        found = build_of_lengths(header_contig_lengths(header))
    else:
        _check_table(path)
    if declared and found and declared != found:
        raise CodedError(
            "roh_af_build_mismatch",
            f"--af-build says {declared_build}, but {path.name}'s contig lengths "
            f"are {found}.",
        )
    build = found or declared
    if build is None:
        raise CodedError(
            "roh_af_build_unknown",
            f"Can't tell which build {path.name} is on (its header has no contig "
            "lengths); pass --af-build.",
        )
    return AfSource(path, kind, tag if kind == "vcf" else None, build, release,
                    file_identity(path))  # fmt: skip


def _open_text(path: Path) -> Iterator[str]:
    with open(path, "rb") as raw:
        gzipped = raw.read(2) == b"\x1f\x8b"
    opener = gzip.open if gzipped else open
    with opener(path, "rt", encoding="utf-8") as f:
        yield from f


def _check_table(path: Path) -> None:
    for line in _open_text(path):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.rstrip("\n").split("\t")
        if len(fields) != 4 or "," not in fields[2]:
            raise CodedError(
                "roh_af_unreadable",
                f"{path.name} is neither a VCF/BCF nor a bcftools roh frequency "
                "table (4 tab-separated columns: CHROM, POS, REF,ALT, AF).",
            )
        return


def _vcf_rows(
    source: AfSource, contigs: Iterable[str], work: Path
) -> Iterator[list[str]]:
    """CHROM, POS, REF,ALT, AF of the source's biallelic SNVs on ``contigs``
    (bare names), as the source names them."""
    header = run_command(
        ["bcftools", "view", "-h", str(source.path)], capture_output=True
    ).stdout
    named = {bare(c): c for c in header_contig_ids(header)}
    wanted = ",".join(named.get(c, c) for c in contigs)
    indexed = any(Path(str(source.path) + ext).is_file() for ext in (".tbi", ".csi"))
    query = ["bcftools", "query", "-i", 'TYPE="snp" && N_ALT=1',
             "-r" if indexed else "-t", wanted,
             "-f", f"%CHROM\t%POS\t%REF,%ALT\t%INFO/{source.tag}\n",
             str(source.path)]  # fmt: skip
    log = work / "af_source.stderr"
    with (
        open(log, "wb") as errors,
        popen(query, stdout=subprocess.PIPE, stderr=errors, text=True) as proc,
    ):
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n").split("\t")
    if proc.returncode:
        tail = log.read_text(errors="replace").strip()[-500:]
        raise CodedError(
            "roh_af_unreadable", f"bcftools couldn't read {source.path.name}: {tail}"
        )


def _table_rows(source: AfSource, contigs: Iterable[str]) -> Iterator[list[str]]:
    wanted = set(contigs)
    for line in _open_text(source.path):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.rstrip("\n").split("\t")
        if bare(fields[0]) in wanted:
            yield fields


def _write_table(
    source: AfSource, contigs: list[str], chr_prefix: bool, dest: Path, work: Path
) -> int:
    """Write the prepared table to ``dest`` (bgzipped, tabix-indexed); returns
    its row count."""
    rows = (
        _vcf_rows(source, contigs, work)
        if source.kind == "vcf"
        else _table_rows(source, contigs)
    )
    written = 0
    with (
        open(dest, "wb") as out,
        popen(["bgzip", "-c"], stdin=subprocess.PIPE, stdout=out, text=True) as bgzip,
    ):
        assert bgzip.stdin is not None
        for chrom, pos, alleles, af in rows:
            ref, _, alt = alleles.partition(",")
            if len(ref) != 1 or len(alt) != 1 or ref == alt:
                continue
            try:
                value = float(af)
            except ValueError:
                continue
            if not 0.0 < value < 1.0:
                continue
            name = f"chr{bare(chrom)}" if chr_prefix else bare(chrom)
            bgzip.stdin.write(f"{name}\t{pos}\t{ref},{alt}\t{af}\n")
            written += 1
        bgzip.stdin.close()
    if bgzip.returncode:
        raise CodedError("roh_af_unreadable", "bgzip failed on the prepared table.")
    done = run_command(
        ["tabix", "-f", "-s", "1", "-b", "2", "-e", "2", str(dest)],
        capture_output=True,
        check=False,
    )
    if done.returncode:
        raise CodedError(
            "roh_af_unsorted",
            f"{source.path.name} isn't sorted by position within each contig "
            f"(tabix: {done.stderr.strip()[-300:]}); sort it first.",
        )
    return written


@dataclass(frozen=True)
class PreparedTable:
    path: Path
    rows: int
    status: Prepared
    reason: str | None
    # The source as the table was made from it, read under the cache lock.
    source: FileIdentity


def cache_root() -> Path | None:
    """``<reference_library>/roh``, where prepared tables are kept, or None
    when no reference library is configured."""
    from wgsextract_cli.core.config import settings
    from wgsextract_cli.core.paths import expand_path

    library = settings.get("reference_library")
    return Path(expand_path(str(library)), CACHE_FOLDER) if library else None


def _key(source: AfSource, contigs: list[str], chr_prefix: bool) -> str:
    payload = json.dumps(
        [
            TABLE_VERSION,
            str(source.path.absolute()),
            source.tag,
            source.kind,
            chr_prefix,
            sorted(contigs),
        ]  # fmt: skip
    )
    return short_key(hashlib.sha256(payload.encode()))


def _reuse_reason(folder: Path, source: AfSource, current: FileIdentity) -> str | None:
    """None when the cached table can be reused, else why it is rebuilt."""
    record_path = folder / "source.json"
    table = folder / "af.tab.gz"
    if not record_path.is_file() or not table.is_file():
        return "no prepared table for this source yet"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "its record is unreadable"
    if drift := file_drift(record["source"], current):
        return f"{source.path.name} {drift}"
    if not Path(str(table) + ".tbi").is_file():
        return "the prepared table's index is missing"
    if drift := file_drift(record["table"], file_identity(table)):
        return f"the prepared table {drift}"
    return None


@contextlib.contextmanager
def prepared(
    source: AfSource,
    contigs: list[str],
    chr_prefix: bool,
    cache: Path | None,
    work: Path,
) -> Iterator[PreparedTable]:
    """The bcftools roh AF table for ``contigs`` (bare names), named as the
    sample names them: reused from ``cache`` while the source is unchanged,
    else built (into ``work`` when there is no cache). A cached table stays
    locked while the caller uses it, so another run can't rebuild it from a
    new release meanwhile, and the source identity it reports is the one the
    table was made from."""
    if cache is None:
        current = file_identity(source.path)
        dest = work / "af.tab.gz"
        rows = _write_table(source, contigs, chr_prefix, dest, work)
        yield PreparedTable(dest, rows, "uncached", None, current)
        return
    folder = cache / _key(source, contigs, chr_prefix)
    folder.mkdir(parents=True, exist_ok=True)
    table = folder / "af.tab.gz"
    with build_lock(str(folder / "prepare.lock")):
        current = file_identity(source.path)
        reason = _reuse_reason(folder, source, current)
        if reason is None:
            record = json.loads((folder / "source.json").read_text(encoding="utf-8"))
            yield PreparedTable(table, int(record["rows"]), "reused", None, current)
            return
        logging.info(
            f"Preparing allele frequencies from {source.path.name} ({reason})..."
        )
        (folder / "source.json").unlink(missing_ok=True)
        staged = folder / "af.partial.tab.gz"
        rows = _write_table(source, contigs, chr_prefix, staged, work)
        os.replace(str(staged) + ".tbi", str(table) + ".tbi")
        os.replace(staged, table)
        record = {
            "source": current,
            "path": str(source.path.absolute()),
            "tag": source.tag,
            "build": source.build,
            "contigs": contigs,
            "chr_prefix": chr_prefix,
            "rows": rows,
            "table": file_identity(table),
        }
        (folder / "source.json").write_text(json.dumps(record, indent=1), "utf-8")
        yield PreparedTable(table, rows, "built", reason, current)
