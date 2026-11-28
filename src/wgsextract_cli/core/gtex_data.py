"""Install and open the pinned GRCh38 GTEx v10 SuSiE eQTL summary."""

import json
import logging
import math
import re
import shlex
import sqlite3
import tarfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory

from wgsextract_cli.core import read_scope, resource_releases
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.download_progress import CancelEvent
from wgsextract_cli.core.ref_library.assets import resolve_reference_library
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.table_writer import require_pyarrow
from wgsextract_cli.core.utils import WGSExtractError

LABEL = "GTEx v10 SuSiE eQTL"
BUILD = "hg38"
_DATABASE = "gtex-v10-susie-eqtl-hg38.sqlite"
_SCHEMA = 1
_TISSUE = re.compile(r"([A-Za-z0-9_-]+)\.v10\.eQTLs\.SuSiE_summary\.parquet")
_COLUMNS = ["variant_id", "phenotype_id", "gene_name", "pip", "afc", "cs_id"]
SEQUENCE = re.compile(r"[ACGTN]+", re.IGNORECASE | re.ASCII)


def path(reflib: str | None) -> Path:
    return Path(resolve_reference_library(reflib), "downloads", _DATABASE)


def guarded_paths(ref: Path | None) -> Iterator[tuple[str, Path]]:
    from wgsextract_cli.core.ref_library.resolver import library_roots

    if ref is not None:
        # Authorize the configured FASTA before resolving its library.
        yield "ref", ref
        windows = PureWindowsPath(ref)
        if not ref.is_absolute() and windows.drive:
            downloads = windows / "downloads"
            yield "ref", Path(str(downloads))
            yield "ref", Path(str(downloads / _DATABASE))
            return
    table = path(library_roots(ref).root)
    yield "ref", table.parent
    yield "ref", table


def missing(table: Path) -> str:
    install = shlex.join(
        ["wgsextract", "ref", "gtex", "--ref", str(table.parent.parent), "--download"]
    )
    return f"{LABEL} (GRCh38) is missing or damaged. To fix this, run: {install}"


def _allowed(table: Path) -> bool:
    return read_scope.may_follow(table.parent) and read_scope.may_follow(table)


def _source() -> dict[str, str]:
    release = resource_releases.pinned_resource("gtex", BUILD)
    return {
        "release": release["version"],
        "build": BUILD,
        "source_url": release["upstream_url"],
        "source_sha256": release["sha256"],
        "terms": release["licence_url"],
        "credit": release["attribution"],
    }


@contextmanager
def open_track(
    table: Path, *, verify: bool = False
) -> Iterator[tuple[sqlite3.Connection, dict[str, str]]]:
    """Read one immutable generation; the installer replaces, never edits it."""
    if not _allowed(table):
        raise WGSExtractError(
            f"GTEx track is outside allowed reference folders: {table}"
        )
    try:
        uri = table.absolute().as_uri() + "?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            source = _source()
            receipt = db.execute("SELECT source, associations FROM receipt").fetchone()
            sample = db.execute(
                "SELECT chrom, pos, ref, alt, ordinal, tissue, gene, gene_name, "
                "cs, pip, afc FROM association LIMIT 1"
            ).fetchone()
            if (
                db.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA
                or not sample
                or not receipt
                or receipt[0] != json.dumps(source, sort_keys=True)
                or not isinstance(receipt[1], int)
                or receipt[1] < 1
                or verify
                and (
                    db.execute("PRAGMA quick_check(1)").fetchone() != ("ok",)
                    or db.execute("SELECT count(*) FROM association").fetchone()
                    != (receipt[1],)
                )
            ):
                raise WGSExtractError(missing(table))
            yield db, source
    except sqlite3.Error as exc:
        raise WGSExtractError(missing(table)) from exc


def installed(table: Path) -> bool:
    try:
        with open_track(table):
            return True
    except WGSExtractError:
        return False


def _association(
    variant: str,
    gene: str,
    gene_name: str | None,
    pip: float,
    afc: float | None,
    cs: int,
    tissue: str,
    ordinal: int,
) -> tuple[str, int, str, str, int, str, str, str | None, int, float, float | None]:
    chrom, pos_text, ref, alt, build = variant.rsplit("_", 4)
    pos = int(pos_text)
    if not (
        build == "b38"
        and chrom.startswith("chr")
        and pos > 0
        and SEQUENCE.fullmatch(ref)
        and SEQUENCE.fullmatch(alt)
        and gene
        and 0 <= pip <= 1
        and cs > 0
    ):
        raise ValueError(f"invalid SuSiE association for {variant}, {gene}, {tissue}")
    return (
        chrom, pos, ref.upper(), alt.upper(), ordinal, tissue, gene, gene_name, cs, pip,
        afc if afc is not None and math.isfinite(afc) else None,
    )  # fmt: skip


def _load(archive: Path, db: sqlite3.Connection, cancel: CancelEvent | None) -> None:
    from pyarrow.parquet import ParquetFile

    db.executescript(
        """
        CREATE TABLE association (
            chrom TEXT, pos INTEGER, ref TEXT, alt TEXT, ordinal INTEGER,
            tissue TEXT, gene TEXT, gene_name TEXT, cs INTEGER, pip REAL, afc REAL,
            PRIMARY KEY (chrom, pos, ref, alt, ordinal)
        ) WITHOUT ROWID;
        CREATE TABLE receipt (source TEXT NOT NULL, associations INTEGER NOT NULL);
        """
    )
    db.execute(f"PRAGMA user_version={_SCHEMA}")
    db.execute("PRAGMA cache_size=-131072")
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    ordinal = 0
    seen: set[str] = set()
    with tarfile.open(archive, "r:") as bundle:
        for member in bundle:
            name = _TISSUE.fullmatch(member.name)
            if not member.isfile() or not name or name[1] in seen:
                raise ValueError(f"unexpected archive member {member.name!r}")
            tissue = name[1]
            seen.add(tissue)
            stream = bundle.extractfile(member)
            assert stream is not None
            with stream, ParquetFile(stream) as parquet:
                for batch in parquet.iter_batches(columns=_COLUMNS, use_threads=False):
                    if cancel and cancel.is_set():
                        raise WGSExtractError("Cancelled.")
                    variants, genes, symbols, pips, afcs, sets = [
                        column.to_pylist() for column in batch.columns
                    ]
                    rows = [
                        _association(
                            variant,
                            gene,
                            symbol,
                            pip,
                            afc,
                            cs,
                            tissue,
                            ordinal + offset,
                        )
                        for offset, (variant, gene, symbol, pip, afc, cs) in enumerate(
                            zip(variants, genes, symbols, pips, afcs, sets, strict=True)
                        )
                    ]
                    db.executemany(
                        "INSERT INTO association VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
                    )
                    ordinal += len(rows)
    if not ordinal:
        raise ValueError("the archive contains no SuSiE associations")
    db.execute(
        "INSERT INTO receipt VALUES (?,?)",
        (json.dumps(_source(), sort_keys=True), ordinal),
    )
    db.commit()


def install(table: Path, *, cancel: CancelEvent | None = None) -> None:
    if not _allowed(table):
        raise WGSExtractError(
            f"GTEx track is outside allowed reference folders: {table}"
        )
    require_pyarrow("ref gtex --download")
    from pyarrow import ArrowException

    source = _source()
    table.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".gtex-v10-", dir=table.parent) as stage:
        archive = Path(stage, "GTEx_v10_SuSiE_eQTL.tar")
        logging.info(
            "Downloading public %s data from %s (174 MB).", LABEL, source["source_url"]
        )
        if not download_file(
            source["source_url"],
            str(archive),
            cancel_event=cancel,
            expected_sha256=source["source_sha256"],
            resolve_release_digest=False,
            use_mirrors=False,
        ):
            raise WGSExtractError(f"Could not download and verify {LABEL} (SHA-256).")
        logging.info("Indexing the GTEx tissue summaries for offline lookup.")
        with atomic_output(table, retire_indexes=False) as staged:
            try:
                with closing(sqlite3.connect(staged)) as db:
                    _load(archive, db, cancel)
            except (
                ArrowException,
                tarfile.TarError,
                sqlite3.Error,
                OSError,
                TypeError,
                ValueError,
            ) as exc:
                raise WGSExtractError(f"Could not prepare {LABEL}: {exc}") from exc
