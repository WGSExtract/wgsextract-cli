"""gnomAD and SpliceAI databases of only an input's sites, for one ``vcf
acmg`` run (issue 629).

Whole-genome databases of gnomAD v4.1 (877 GB streamed to build) and SpliceAI
(a 29 GB download) are a lot of disk for one genome. Instead, the records at
the input's sites are read from the publishers' indexed files
(``tabix_sites``) and built into small databases with the same ``fastvep
sa-build`` as ``ref acmg-data``, so each variant finds what it would find in
the full database: fastVEP looks gnomAD and SpliceAI up by the variant's own
position and alleles, never a neighbour's. A site is each record's span, a
base wider on each side, both as written and left-aligned (``bcftools norm``),
since fastVEP left-aligns an indel before its gnomAD lookup.

fastVEP reads "no gnomAD record" as "absent from gnomAD" only on a chromosome
a gnomAD database holds; a chromosome whose sites have no records gets its
first record, which no site can match, so that it is held. The databases are
kept in a cache, keyed on the sites and the file's identity (ETag or size and
date), and reused by a later run with the same sites.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from array import array
from collections import defaultdict
from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

import numpy as np

from wgsextract_cli.core import acmg_data, read_scope, tabix_sites
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.download_progress import CancelEvent, format_bytes
from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor
from wgsextract_cli.core.tabix_sites import TabixFile
from wgsextract_cli.core.utils import WGSExtractError, popen

SOURCES = ("gnomad", "spliceai")
CACHE = acmg_data.SITES_CACHE
META = "site_db.json"
# Bump when what a cached database holds would change.
FORMAT = 1
# Databases the cache keeps (a genome's are 48), the most recently used.
_KEEP = 200
_IN_USE = 2 * 86400  # seconds since its last use a database may be in use
_PAD = 1


def contig_key(name: str) -> str:
    key = name.removeprefix("chr")
    return "MT" if key == "M" else key


def location(source: str, chromosome: str, reflib: str | None) -> str:
    """Where a source's records of ``chromosome`` (``chrN``) are read: gnomAD's
    per-chromosome file, or the library's SpliceAI VCF (``ref spliceai``),
    else Ensembl's."""
    if source == "gnomad":
        return acmg_data.source_url("gnomad", chromosome)
    local = acmg_data.library_spliceai(reflib) if reflib else None
    if local and not read_scope.may_follow(local):
        raise WGSExtractError(f"{local} leads outside the folders this run may use.")
    return str(local) if local else acmg_data.SPLICEAI_URL


def _spans(lines: Iterable[str], spans: dict[str, array[int]]) -> None:
    for line in lines:
        chrom, pos_text, ref = line.rstrip("\n").split("\t")
        pos = int(pos_text)
        found = spans.setdefault(chrom, array("q"))
        found.append(max(1, pos - _PAD))
        found.append(pos + len(ref) - 1 + _PAD)


def _query(commands: list[list[str]], spans: dict[str, array[int]]) -> None:
    """Spans of the records the pipeline ``commands`` ends in printing."""
    procs: list[subprocess.Popen[str]] = []
    errors: list[IO[bytes]] = []
    upstream: IO[str] | None = None
    try:
        for command in commands:
            # A file, not a pipe: bcftools norm warns for every REF mismatch.
            errors.append(tempfile.TemporaryFile())
            proc = popen(
                command, stdin=upstream, stdout=subprocess.PIPE, stderr=errors[-1],
                text=True,
            )  # fmt: skip
            if upstream is not None:
                upstream.close()
            upstream = proc.stdout
            procs.append(proc)
        assert upstream is not None
        _spans(upstream, spans)
        failed = []
        for command, proc, err in zip(commands, procs, errors, strict=True):
            if proc.wait() != 0:
                err.seek(0)
                detail = err.read().decode(errors="replace").strip()[-300:]
                failed.append(f"bcftools {command[1]}: {detail}")
        if failed:
            raise WGSExtractError(
                "Reading the input's sites failed: " + "; ".join(failed)
            )
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        for err in errors:
            err.close()


def input_sites(vcf: str, fasta: str) -> dict[str, np.ndarray]:
    """Per contig, the sites to look up as sorted, merged ``(first, last)``
    1-based positions: each record's span a base wider on each side, as
    written and left-aligned."""
    spans: dict[str, array[int]] = {}
    query = ["bcftools", "query", "-f", "%CHROM\t%POS\t%REF\n"]
    _query([[*query, vcf]], spans)
    _query(
        [
            ["bcftools", "view", "--no-version", "-G", "-Ou", vcf],
            ["bcftools", "annotate", "--no-version", "-x", "INFO", "-Ou", "-"],
            [
                "bcftools",
                "norm",
                "--no-version",
                "-f",
                fasta,
                "-m",
                "-any",
                "-c",
                "w",
                "-Ou",
                "-",
            ],
            [*query, "-"],
        ],  # fmt: skip
        spans,
    )
    return {chrom: merge_spans(np.frombuffer(flat, dtype=np.int64).reshape(-1, 2))
            for chrom, flat in spans.items()}  # fmt: skip


def merge_spans(spans: np.ndarray) -> np.ndarray:
    """Overlapping or touching ``(first, last)`` spans joined, sorted."""
    if not len(spans):
        return spans
    spans = spans[np.argsort(spans[:, 0], kind="stable")]
    reach = np.maximum.accumulate(spans[:, 1])
    starts = np.flatnonzero(np.r_[True, spans[1:, 0] > reach[:-1] + 1])
    return np.column_stack((spans[starts, 0], np.maximum.reduceat(spans[:, 1], starts)))


@dataclass
class SiteDatabase:
    """A database of one source on one chromosome, as built or cached."""

    source: str
    chromosome: str
    folder: Path
    files: list[str]
    meta: dict[str, Any] = field(default_factory=dict)
    cached: bool = False

    @property
    def paths(self) -> list[Path]:
        return [self.folder / name for name in self.files]


def _key(
    source: str, chromosome: str, file: TabixFile, sites: np.ndarray, version: str
) -> str:
    digest = hashlib.sha256()
    identity = {
        "format": FORMAT,
        "source": source,
        "chromosome": chromosome,
        "location": file.location,
        "size": file.source.size,
        "etag": file.source.etag,
        "modified": file.source.last_modified
        or ("" if file.source.remote else str(Path(file.location).stat().st_mtime_ns)),
        "keep": acmg_data.GNOMAD_KEEP if source == "gnomad" else "",
        "chunk_bits": acmg_data.BY_KEY[source].chunk_bits,
        "fastvep": version,
    }
    digest.update(json.dumps(identity, sort_keys=True).encode())
    digest.update(np.ascontiguousarray(sites, dtype=np.int64).tobytes())
    return digest.hexdigest()


def _cached(entry: Path) -> SiteDatabase | None:
    try:
        meta = json.loads((entry / META).read_text())
    except (OSError, ValueError):
        return None
    files = list(meta.get("files") or ())
    if not files or any(Path(name).name != name for name in files):
        return None
    found = SiteDatabase(meta["source"], meta["chromosome"], entry, files, meta, True)
    # A server run uses only entries inside the folders it may read.
    if not all(p.is_file() and read_scope.may_follow(p) for p in found.paths):
        return None
    os.utime(entry)  # most recently used
    return found


def _build(
    source: str,
    chromosome: str,
    file: TabixFile,
    contig: str,
    sites: np.ndarray,
    entry: Path,
    connections: int,
    cancel: CancelEvent | None,
    version: str,
    before: tuple[int, int],
) -> SiteDatabase:
    """Read the records at ``sites`` and build their database into ``entry``
    (staged beside it); ``before`` is what had been read of ``file`` (bytes,
    requests)."""
    stage = entry.with_name(f".{entry.name}.{os.getpid()}")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        meta = _fill(
            stage, source, chromosome, file, contig, sites, connections, cancel
        )
        meta.update(
            fastvep=version,
            bytes_read=file.bytes_read - before[0],
            requests=file.source.requests - before[1],
        )
        (stage / META).write_text(json.dumps(meta, indent=1) + "\n", newline="\n")
        shutil.rmtree(entry, ignore_errors=True)  # an incomplete one
        stage.replace(entry)
    except OSError:
        # Another run built it meanwhile: use that one.
        if (other := _cached(entry)) is not None:
            return other
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return SiteDatabase(source, chromosome, entry, list(meta["files"]), meta)


def _fill(
    stage: Path,
    source: str,
    chromosome: str,
    file: TabixFile,
    contig: str,
    sites: np.ndarray,
    connections: int,
    cancel: CancelEvent | None,
) -> dict[str, Any]:
    """Write the records at ``sites`` and build their database in ``stage``;
    returns what it holds."""
    name = f"{source}_{chromosome}"
    started = time.monotonic()
    header = file.header()
    lines = header.decode(errors="replace").splitlines()
    if source == "spliceai":
        acmg_data.require_grch38_header(lines, file.location)
    release = (
        "4.1"
        if source == "gnomad"
        else next((h[11:] for h in lines if h.startswith("##fileDate=")), "")
    )
    records_vcf = stage / f"{name}.vcf.gz"
    # gnomAD keeps only the INFO keys fastVEP reads, as ref acmg-data does.
    command = (
        ["bcftools", "annotate", "--no-version", "-x", f"^{acmg_data.GNOMAD_KEEP}",
         "-Oz", "-o", str(records_vcf), "-"]
        if source == "gnomad"
        else ["bgzip", "-c"]
    )  # fmt: skip
    count = 0
    writes = records_vcf.open("wb") if source != "gnomad" else nullcontext()
    with writes as out:
        proc = popen(
            command,
            stdin=subprocess.PIPE,
            stdout=out or subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        assert proc.stdin is not None
        sink = proc.stdin

        def write(found: list[bytes]) -> None:
            nonlocal count
            if found:
                sink.write(b"\n".join(found) + b"\n")
                count += len(found)

        anchor = None
        try:
            sink.write(header)
            file.records_at(
                contig,
                (tuple(span) for span in sites.tolist()),
                connections,
                on_group=write,
            )
            if not count:
                anchor = file.first_record(contig)
                write([anchor] if anchor else [])
            sink.close()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        _, err = proc.communicate()
    if proc.returncode != 0:
        raise WGSExtractError(
            f"Writing the {name} records failed: {err.decode(errors='replace')[-500:]}"
        )
    if not count:
        raise _NoRecords
    fastvep = get_tool_path("fastvep") or "fastvep"
    built, records = acmg_data.sa_build(
        fastvep, source, records_vcf, stage / "build", name, cancel
    )
    files = []
    for path in built:
        path.replace(stage / path.name)
        files.append(path.name)
    shutil.rmtree(stage / "build", ignore_errors=True)
    records_vcf.unlink()
    return {
        "format": FORMAT,
        "source": source,
        "chromosome": chromosome,
        "files": files,
        "location": file.location,
        "release": release,
        "etag": file.source.etag,
        "last_modified": file.source.last_modified,
        "size_bytes": file.source.size,
        "sites": len(sites),
        "records": records,
        "first_record_added": anchor is not None,
        "seconds": round(time.monotonic() - started, 1),
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class _NoRecords(Exception):
    """A chromosome the source's file holds nothing of."""


def build_databases(
    wanted: dict[str, list[str]],
    sites: dict[str, np.ndarray],
    reflib: str | None,
    cache: Path,
    connections: int,
    cancel: CancelEvent | None = None,
) -> list[SiteDatabase]:
    """The databases of each source's ``wanted`` input contigs, from the
    cache or built, the sources side by side (they are on different
    servers); a contig a source holds nothing of gets none."""
    cache.mkdir(parents=True, exist_ok=True)
    version = acmg_data.fastvep_version()
    # Ctrl-C stops the other source's reads and builds too.
    stop = acmg_data.Stop(cancel)
    pool = ContextThreadPoolExecutor(max_workers=len(SOURCES))
    try:
        futures = [
            pool.submit(
                _source_databases,
                source,
                wanted.get(source, []),
                sites,
                reflib,
                cache,
                connections,
                stop,
                version,
            )  # fmt: skip
            for source in SOURCES
        ]
        found = [db for future in futures for db in future.result()]
    except BaseException:
        stop.set()
        raise
    finally:
        pool.shutdown(cancel_futures=True)
    _prune(cache)
    return found


def _source_databases(
    source: str,
    contigs: list[str],
    sites: dict[str, np.ndarray],
    reflib: str | None,
    cache: Path,
    connections: int,
    cancel: CancelEvent | None,
    version: str,
) -> list[SiteDatabase]:
    found = []
    opened: dict[str, TabixFile] = {}
    for contig in contigs:
        spans = sites.get(contig)
        if spans is None or not len(spans):
            continue
        chromosome = f"chr{contig_key(contig)}"
        where = location(source, chromosome, reflib)
        try:
            file = opened.get(where) or TabixFile(where, cancel)
        except FileNotFoundError:
            logging.info("%s has no %s.", source, chromosome)
            continue
        opened[where] = file
        key = _key(source, chromosome, file, spans, version)
        entry = cache / f"{source}_{chromosome}_{key[:24]}"
        database = _cached(entry)
        if database is not None:
            logging.info("%s %s: reusing %s.", source, chromosome, entry)
            found.append(database)
            continue
        before = (file.bytes_read, file.source.requests)
        names = {contig_key(c): c for c in file.index.contigs}
        remote = names.get(contig_key(contig))
        if remote is None:
            logging.info("%s holds nothing of %s.", where, chromosome)
            continue
        logging.info("Reading %s at %s's %d sites...", source, chromosome, len(spans))
        try:
            database = _build(
                source, chromosome, file, remote, spans, entry,
                tabix_sites.connections(where, connections), cancel, version, before,
            )  # fmt: skip
        except _NoRecords:
            continue
        logging.info(
            "%s %s: %s records, read %s in %s requests (%.0f s).",
            source, chromosome, f"{database.meta['records']:,}",
            format_bytes(database.meta["bytes_read"]),
            f"{database.meta['requests']:,}", database.meta["seconds"],
        )  # fmt: skip
        found.append(database)
    return found


def _prune(cache: Path) -> None:
    """Keep the most recently used databases, and none an interrupted run
    left staged; nothing used in the last two days, which a run going on
    beside this one may be reading."""
    old = time.time() - _IN_USE
    entries = [p for p in cache.iterdir() if p.is_dir()]
    staged = [p for p in entries if p.name.startswith(".")]
    kept = sorted(
        (p for p in entries if p not in staged),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in [*staged, *kept[_KEEP:]]:
        if stale.stat().st_mtime < old:
            shutil.rmtree(stale, ignore_errors=True)


def link_folder(
    library: Path, leave_out: set[Path], databases: list[SiteDatabase], dest: Path
) -> None:
    """A new folder ``dest`` holding the library's databases but
    ``leave_out`` (and their ``.idx``), and the per-run ones, as links (copies
    where links can't be made). A per-run database replaces a library file of
    its name; nothing is ever written through to either. The per-run ones are
    hard links where they can be, which outlive their cache entry."""
    dest.mkdir(parents=True)
    skip = {p.name for p in leave_out} | {f"{p.name}.idx" for p in leave_out}
    chosen = {
        p.name: (p, False)
        for p in sorted(library.iterdir())
        if p.is_file() and not p.name.startswith(".") and p.name not in skip
    }
    for database in databases:
        chosen.update((p.name, (p, True)) for p in database.paths)
    for name, (path, per_run) in chosen.items():
        link = dest / name
        ways = (_hard_link, _soft_link) if per_run else (_soft_link, _hard_link)
        for way in ways:
            try:
                way(path, link)
                break
            except OSError:
                continue
        else:
            shutil.copyfile(path, link)
        if not link.is_file():
            raise WGSExtractError(f"{path} went missing while the run was set up.")


def _hard_link(path: Path, link: Path) -> None:
    os.link(path, link)


def _soft_link(path: Path, link: Path) -> None:
    link.symlink_to(path.resolve())


def summary(databases: list[SiteDatabase]) -> dict[str, Any]:
    """What the run read, per source and chromosome, for acmg_summary.json."""
    by_source: dict[str, Any] = defaultdict(dict)
    for db in databases:
        by_source[db.source][db.chromosome] = {
            key: db.meta.get(key)
            for key in ("location", "release", "etag", "last_modified", "size_bytes", "sites",
                        "records", "first_record_added", "bytes_read", "requests",
                        "seconds", "built")
        } | {"cached": db.cached, "folder": str(db.folder)}  # fmt: skip
    return dict(by_source)
