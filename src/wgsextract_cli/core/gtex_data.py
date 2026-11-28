"""Install and open the pinned GRCh38 GTEx v10 SuSiE eQTL summary."""

import hashlib
import json
import logging
import math
import re
import shlex
import sqlite3
import stat
import tarfile
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path, PurePath, PureWindowsPath
from tempfile import TemporaryDirectory

from wgsextract_cli.core import read_scope, resource_releases
from wgsextract_cli.core.atomic_output import publish_output_set
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


def receipt_path(table: PurePath) -> Path:
    return Path(str(table) + ".json")


def _paths(table: PurePath) -> tuple[Path, ...]:
    return Path(str(table.parent)), Path(str(table)), receipt_path(table)


def guarded_paths(ref: Path | None) -> Iterator[tuple[str, Path]]:
    from wgsextract_cli.core.ref_library.resolver import library_roots

    if ref is not None:
        # Authorize the configured FASTA before resolving its library.
        yield "ref", ref
        windows = PureWindowsPath(ref)
        if not ref.is_absolute() and windows.drive:
            yield from (("ref", p) for p in _paths(windows / "downloads" / _DATABASE))
            return
    yield from (("ref", p) for p in _paths(path(library_roots(ref).root)))


def missing(table: Path) -> str:
    install = shlex.join(
        ["wgsextract", "ref", "gtex", "--ref", str(table.parent.parent), "--download"]
    )
    return f"{LABEL} (GRCh38) is missing or damaged. To fix this, run: {install}"


def _allowed(table: Path) -> bool:
    return all(read_scope.may_follow(item) for item in _paths(table))


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


def _generation(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"{path} is not a regular file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _sha256(path: Path, cancel: CancelEvent | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b""):
            if cancel and cancel.is_set():
                raise WGSExtractError("Cancelled.")
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def open_track(
    table: Path, *, verify: bool = False, cancel: CancelEvent | None = None
) -> Iterator[tuple[sqlite3.Connection, dict[str, str]]]:
    """Read one immutable generation; the installer replaces, never edits it."""
    if not _allowed(table):
        raise WGSExtractError(
            f"GTEx track is outside allowed reference folders: {table}"
        )
    receipt = receipt_path(table)

    def generations() -> tuple[tuple[int, int, int, int, int], ...]:
        try:
            return _generation(table), _generation(receipt)
        except OSError as exc:
            raise WGSExtractError(missing(table)) from exc

    with ExitStack() as connection:
        try:
            generation = generations()
            source = _source()
            document = json.loads(receipt.read_text(encoding="utf-8"))
            digest = (
                document.get("database_sha256") if isinstance(document, dict) else None
            )
            if (
                not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or any(document.get(key) != value for key, value in source.items())
                or verify
                and digest != _sha256(table, cancel)
            ):
                raise WGSExtractError(missing(table))
            uri = table.absolute().as_uri() + "?mode=ro&immutable=1"
            db = connection.enter_context(closing(sqlite3.connect(uri, uri=True)))
            db.execute("PRAGMA trusted_schema=OFF")
            sample = db.execute(
                "SELECT chrom, pos, ref, alt, ordinal, tissue, gene, gene_name, "
                "cs, pip, afc FROM association LIMIT 1"
            ).fetchone()
            if (
                db.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA
                or not sample
                or generation != generations()
            ):
                raise WGSExtractError(missing(table))
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise WGSExtractError(missing(table)) from exc
        try:
            yield db, {**source, "database_sha256": digest}
        except sqlite3.Error as exc:
            raise WGSExtractError(missing(table)) from exc
        if generation != generations():
            raise WGSExtractError(missing(table))


def installed(table: Path, *, verify: bool = False) -> bool:
    try:
        with open_track(table, verify=verify):
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
        staged = Path(stage, table.name)
        receipt = receipt_path(staged)
        try:
            with closing(sqlite3.connect(staged)) as db:
                _load(archive, db, cancel)
            document = {**source, "database_sha256": _sha256(staged, cancel)}
            receipt.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        except (
            ArrowException,
            tarfile.TarError,
            sqlite3.Error,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            raise WGSExtractError(f"Could not prepare {LABEL}: {exc}") from exc
        publish_output_set({receipt: receipt_path(table), staged: table})
