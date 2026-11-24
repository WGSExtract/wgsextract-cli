"""NCBI dbSNP b151 common human rsIDs (~37M SNPs) and offline SQLite lookup index."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import logging
import os
import re
import sqlite3
import subprocess
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path

from wgsextract_cli.core.annotation_resources import _check_indexed
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.download_progress import CancelEvent, ProgressCallback
from wgsextract_cli.core.file_lock import build_lock
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.ref_library.layout import (
    download_dirs,
    download_path,
    downloads_dir,
)
from wgsextract_cli.core.utils import (
    WGSExtractError,
    get_resource_threads,
    popen,
    run_command,
)

# Archived NCBI dbSNP build 151 common human SNPs (MAF >= 1% in >= 1 1000G
# population, ~37M variants per assembly). Static since April 2018; published
# with adjacent .tbi and .md5 files.
DBSNP_SOURCES: dict[str, tuple[str, str]] = {
    "hg38": (
        "https://ftp.ncbi.nih.gov/snp/organisms/human_9606_b151_GRCh38p7/VCF/common_all_20180418.vcf.gz",
        "a274dcecff9cfe6084eaef848080ad8d",
    ),
    "hg19": (
        "https://ftp.ncbi.nih.gov/snp/organisms/human_9606_b151_GRCh37p13/VCF/common_all_20180423.vcf.gz",
        "29b5ddb6f4da121de8fcfd061dc9000c",
    ),
}

DBSNP_TERMS = (
    "NCBI dbSNP data are public domain "
    "(https://www.ncbi.nlm.nih.gov/home/about/policies/)."
)

DBSNP_SCHEMA_VERSION = "1"
_RSID_BYTES_RE = re.compile(rb"^[rR][sS]([0-9]+)$")
_RSID_STR_RE = re.compile(r"^rs([0-9]+)$", re.IGNORECASE)
_BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
_BATCH_SIZE = 50_000


def dbsnp_vcf_path(reflib: str | Path, build: str) -> Path:
    build = _require_build(build)
    return download_path(reflib, f"dbsnp_common_{build}.vcf.gz")


def dbsnp_sqlite_path(reflib: str | Path, build: str) -> Path:
    build = _require_build(build)
    return download_path(reflib, f"dbsnp_common_{build}.rsid.sqlite")


def _require_build(build: str) -> str:
    from wgsextract_cli.core.builds import canonical_build

    resolved = canonical_build(build) if build else None
    if resolved not in DBSNP_SOURCES:
        raise WGSExtractError(
            f"dbSNP common rsIDs are available for GRCh38 and GRCh37 only, not {build!r}."
        )
    return resolved


def _search_dirs(reflib: str | Path) -> tuple[Path, ...]:
    return (*download_dirs(reflib), Path(reflib))


def find_dbsnp_vcf(reflib: str | Path, build: str) -> Path | None:
    build = _require_build(build)
    name = f"dbsnp_common_{build}.vcf.gz"
    for directory in _search_dirs(reflib):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def find_dbsnp_sqlite(reflib: str | Path, build: str) -> Path | None:
    build = _require_build(build)
    name = f"dbsnp_common_{build}.rsid.sqlite"
    for directory in _search_dirs(reflib):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def dbsnp_status(reflib: str | Path) -> dict[str, dict[str, object]]:
    """Installed status for hg19 and hg38 dbSNP common VCF + rsID SQLite index."""
    result: dict[str, dict[str, object]] = {}
    for build in ("hg19", "hg38"):
        vcf = find_dbsnp_vcf(reflib, build)
        indexed = bool(
            vcf and any(Path(f"{vcf}{ext}").is_file() for ext in (".tbi", ".csi"))
        )
        sqlite_file = find_dbsnp_sqlite(reflib, build)
        sqlite_ok = bool(sqlite_file and validate_rsid_sqlite(sqlite_file, build))
        result[build] = {
            "vcf": str(vcf) if vcf else "",
            "indexed": indexed,
            "rsidIndex": str(sqlite_file) if sqlite_file else "",
            "rsidIndexValid": sqlite_ok,
            "installed": indexed and sqlite_ok,
        }
    return result


def validate_rsid_sqlite(sqlite_path: Path, build: str | None = None) -> bool:
    """Return True if ``sqlite_path`` is a complete, readable rsID lookup DB."""
    if not sqlite_path.is_file():
        return False
    try:
        uri = f"file:{sqlite_path.resolve().as_posix()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            if meta.get("schema_version") != DBSNP_SCHEMA_VERSION:
                return False
            if build is not None and meta.get("build") != build:
                return False
            count = int(meta.get("count", "0"))
            if count < 1:
                return False
            row = conn.execute(
                "SELECT c.name, r.pos FROM rsids r JOIN chroms c ON c.cid = r.cid LIMIT 1"
            ).fetchone()
            return row is not None
    except (OSError, ValueError, sqlite3.Error):
        return False


def lookup_rsid(sqlite_path: Path, rsid: str) -> tuple[str, int] | None:
    """Look up ``rs<digits>`` in ``sqlite_path``, returning ``(chrom, 1-based pos)``."""
    match = _RSID_STR_RE.fullmatch(rsid.strip())
    if not match:
        return None
    numeric_id = int(match.group(1))
    uri = f"file:{sqlite_path.resolve().as_posix()}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        row = conn.execute(
            "SELECT c.name, r.pos FROM rsids r JOIN chroms c ON c.cid = r.cid WHERE r.rsid = ?",
            (numeric_id,),
        ).fetchone()
    if row is None:
        return None
    return str(row[0]), int(row[1])


@contextlib.contextmanager
def _stream_vcf_lines(vcf_path: Path) -> Iterator[Iterator[bytes]]:
    """Yield an iterator of raw lines from a BGZF/gzip VCF."""
    bgzip = get_tool_path("bgzip")
    if bgzip:
        threads = max(1, min(4, int(get_resource_threads(None))))
        proc = popen(
            ["bgzip", "-dc", "-@", str(threads), str(vcf_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None and proc.stderr is not None
        try:
            yield iter(proc.stdout)
            error = proc.stderr.read().decode("utf-8", errors="replace").strip()
            rc = proc.wait()
            if rc != 0:
                raise WGSExtractError(
                    f"bgzip failed while reading {vcf_path.name}: {error}"
                )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            proc.stderr.close()
    else:
        with gzip.open(vcf_path, "rb") as handle:
            yield iter(handle)


def build_rsid_sqlite(
    vcf_path: Path,
    sqlite_path: Path,
    build: str,
    *,
    cancel_event: CancelEvent | None = None,
) -> int:
    """Build an offline rsID -> (chrom, pos) SQLite lookup index atomically."""
    build = _require_build(build)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = sqlite_path.parent / f".{sqlite_path.name}.{uuid.uuid4().hex}.tmp"
    chrom_ids: dict[str, int] = {}
    batch: list[tuple[int, int, int]] = []
    inserted = 0

    def cancelled() -> bool:
        return bool(cancel_event and cancel_event.is_set())

    logging.info("Building offline rsID search index for %s...", build)
    try:
        conn = sqlite3.connect(tmp_path)
        try:
            conn.execute("PRAGMA page_size = 4096")
            conn.execute("PRAGMA journal_mode = OFF")
            conn.execute("PRAGMA synchronous = OFF")
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute("PRAGMA cache_size = -131072")
            conn.execute(
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID"
            )
            conn.execute(
                "CREATE TABLE chroms (cid INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)"
            )
            conn.execute(
                "CREATE TABLE rsids (rsid INTEGER PRIMARY KEY, cid INTEGER NOT NULL, pos INTEGER NOT NULL)"
            )
            cur = conn.cursor()
            with _stream_vcf_lines(vcf_path) as lines:
                for raw_line in lines:
                    if not raw_line or raw_line[0] == 35:  # '#'
                        continue
                    parts = raw_line.split(b"\t", 3)
                    if len(parts) < 3:
                        continue
                    raw_chrom, raw_pos, raw_ids = parts[0], parts[1], parts[2]
                    if not raw_ids or raw_ids == b".":
                        continue
                    try:
                        pos = int(raw_pos)
                    except ValueError:
                        continue
                    if pos < 1:
                        continue
                    for token in raw_ids.split(b";"):
                        match = _RSID_BYTES_RE.match(token)
                        if not match:
                            continue
                        chrom = raw_chrom.decode("ascii", errors="ignore")
                        if not chrom:
                            continue
                        cid = chrom_ids.get(chrom)
                        if cid is None:
                            cid = len(chrom_ids) + 1
                            chrom_ids[chrom] = cid
                            cur.execute(
                                "INSERT INTO chroms (cid, name) VALUES (?, ?)",
                                (cid, chrom),
                            )
                        batch.append((int(match.group(1)), cid, pos))
                    if len(batch) >= _BATCH_SIZE:
                        if cancelled():
                            raise WGSExtractError("dbSNP index build cancelled.")
                        cur.executemany(
                            "INSERT OR IGNORE INTO rsids (rsid, cid, pos) VALUES (?, ?, ?)",
                            batch,
                        )
                        inserted += len(batch)
                        batch.clear()
                        if inserted % 5_000_000 == 0:
                            logging.info(
                                "Indexed %s rsIDs for %s...", f"{inserted:,}", build
                            )
            if cancelled():
                raise WGSExtractError("dbSNP index build cancelled.")
            if batch:
                cur.executemany(
                    "INSERT OR IGNORE INTO rsids (rsid, cid, pos) VALUES (?, ?, ?)",
                    batch,
                )
                batch.clear()
            actual_count = int(cur.execute("SELECT COUNT(*) FROM rsids").fetchone()[0])
            if actual_count < 1:
                raise WGSExtractError(
                    f"No valid rsID records found in {vcf_path.name}."
                )
            cur.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                [
                    ("schema_version", DBSNP_SCHEMA_VERSION),
                    ("build", build),
                    ("source", "dbSNP_b151_common"),
                    ("count", str(actual_count)),
                ],
            )
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_path, sqlite_path)
        logging.info(
            "Built offline rsID search index for %s (%s rsIDs): %s",
            build,
            f"{actual_count:,}",
            sqlite_path,
        )
        return actual_count
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _verify_md5(path: Path, expected_md5: str) -> None:
    if not expected_md5:
        return
    logging.info("Verifying MD5 of %s...", path.name)
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(8 << 20):
            digest.update(chunk)
    actual = digest.hexdigest().lower()
    if actual != expected_md5.lower():
        raise WGSExtractError(
            f"MD5 mismatch for {path.name}: expected {expected_md5}, got {actual}."
        )


def _check_vcf_complete(vcf_path: Path) -> None:
    """Verify BGZF EOF and, when tabix is installed, index readability."""
    size = vcf_path.stat().st_size
    if size < len(_BGZF_EOF):
        raise WGSExtractError(f"{vcf_path.name} is truncated (too small for BGZF).")
    with vcf_path.open("rb") as handle:
        handle.seek(-len(_BGZF_EOF), 2)
        if handle.read() != _BGZF_EOF:
            raise WGSExtractError(
                f"{vcf_path.name} is truncated (no BGZF end-of-file block)."
            )
    if get_tool_path("tabix"):
        _check_indexed(str(vcf_path))


def _vcf_ready(vcf_path: Path) -> bool:
    tbi_path = Path(f"{vcf_path}.tbi")
    if not (vcf_path.is_file() and tbi_path.is_file()):
        return False
    try:
        _check_vcf_complete(vcf_path)
        return True
    except (OSError, subprocess.SubprocessError, WGSExtractError):
        return False


def install_dbsnp(
    reflib: str | Path,
    builds: Sequence[str] = ("hg38", "hg19"),
    *,
    cancel_event: CancelEvent | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[Path]:
    """Download dbSNP common SNPs VCF + .tbi and build the offline rsID SQLite index."""
    builds = [_require_build(build) for build in builds]
    logging.info(DBSNP_TERMS)
    target_dir = downloads_dir(reflib)
    target_dir.mkdir(parents=True, exist_ok=True)
    if not target_dir.resolve().is_relative_to(Path(reflib).resolve()):
        raise WGSExtractError("dbSNP resource folder escapes the reference library.")

    installed: list[Path] = []
    for build in builds:
        if cancel_event and cancel_event.is_set():
            raise WGSExtractError("dbSNP installation cancelled.")
        vcf_path = dbsnp_vcf_path(reflib, build)
        tbi_path = Path(f"{vcf_path}.tbi")
        sqlite_path = dbsnp_sqlite_path(reflib, build)
        url, expected_md5 = DBSNP_SOURCES[build]

        with build_lock(f"{vcf_path}.lock"):
            if not _vcf_ready(vcf_path):
                logging.info("Downloading dbSNP common rsIDs (%s)...", build)
                if not download_file(
                    url, str(vcf_path), progress_callback, cancel_event
                ):
                    raise WGSExtractError(
                        f"Failed to download dbSNP common rsIDs ({build}); rerun to resume."
                    )
                if cancel_event and cancel_event.is_set():
                    raise WGSExtractError("dbSNP installation cancelled.")
                try:
                    _verify_md5(vcf_path, expected_md5)
                except WGSExtractError:
                    vcf_path.unlink(missing_ok=True)
                    raise
                logging.info("Downloading dbSNP common rsIDs index (%s)...", build)
                tbi_ok = download_file(
                    f"{url}.tbi", str(tbi_path), progress_callback, cancel_event
                )
                if not tbi_ok and get_tool_path("tabix"):
                    logging.info("Building tabix index for %s...", vcf_path.name)
                    run_command(
                        ["tabix", "-f", "-p", "vcf", str(vcf_path)],
                        capture_output=True,
                    )
                elif not tbi_ok:
                    raise WGSExtractError(
                        f"Failed to download tabix index for {vcf_path.name}."
                    )
                try:
                    _check_vcf_complete(vcf_path)
                except (OSError, subprocess.SubprocessError, WGSExtractError) as exc:
                    vcf_path.unlink(missing_ok=True)
                    tbi_path.unlink(missing_ok=True)
                    raise WGSExtractError(
                        f"dbSNP common rsIDs ({build}) failed verification: {exc}"
                    ) from exc

            if not validate_rsid_sqlite(sqlite_path, build):
                build_rsid_sqlite(
                    vcf_path, sqlite_path, build, cancel_event=cancel_event
                )

        installed.extend([vcf_path, tbi_path, sqlite_path])
    return installed
