"""Install and open the pinned GRCh38 GTEx v10 eQTL tracks."""

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

from wgsextract_cli.core import gtex_pairs, read_scope, resource_releases
from wgsextract_cli.core.atomic_output import publish_output_set
from wgsextract_cli.core.download_progress import CancelEvent
from wgsextract_cli.core.paths import sqlite_readonly_uri
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


def resource(pairs: str | None) -> str:
    return "gtex-pairs" if pairs is not None else "gtex"


def _database(pairs: str | None) -> str:
    if pairs is None:
        return _DATABASE
    gtex_pairs.validate_tissue(pairs)
    return f"gtex-v10-pairs-{pairs}-hg38.sqlite"


def path(reflib: str | None, pairs: str | None = None) -> Path:
    return Path(resolve_reference_library(reflib), "downloads", _database(pairs))


def receipt_path(table: PurePath) -> Path:
    return Path(str(table) + ".json")


def _paths(table: PurePath) -> tuple[Path, ...]:
    return Path(str(table.parent)), Path(str(table)), receipt_path(table)


def guarded_paths(
    ref: Path | None, pairs: str | None = None
) -> Iterator[tuple[str, Path]]:
    from wgsextract_cli.core.ref_library.resolver import library_roots

    if ref is not None:
        # Authorize the configured FASTA before resolving its library.
        yield "ref", ref
        windows = PureWindowsPath(ref)
        if not ref.is_absolute() and windows.drive:
            yield from (
                ("ref", p) for p in _paths(windows / "downloads" / _database(pairs))
            )
            return
    yield from (("ref", p) for p in _paths(path(library_roots(ref).root, pairs)))


def install_flags(pairs: str | None) -> list[str]:
    return ["--download"] if pairs is None else ["--pairs", pairs, "--download"]


def label(pairs: str | None) -> str:
    return LABEL if pairs is None else f"{gtex_pairs.LABEL} ({pairs})"


def missing(table: Path, pairs: str | None = None) -> str:
    install = shlex.join(
        ["wgsextract", "ref", "gtex", "--ref", str(table.parent.parent), *install_flags(pairs)]
    )  # fmt: skip
    return f"{label(pairs)} (GRCh38) is missing or damaged. To fix this, run: {install}"


def _allowed(table: Path) -> bool:
    return all(read_scope.may_follow(item) for item in _paths(table))


def _source(pairs: str | None) -> dict[str, str]:
    release = resource_releases.pinned_resource(resource(pairs), BUILD)
    source = {
        "release": release["version"],
        "build": BUILD,
        "source_url": release["upstream_url"],
        "source_sha256": release["sha256"],
        "terms": release["licence_url"],
        "credit": release["attribution"],
    }
    if pairs is not None:
        source["tissue"] = pairs
        source["retrieved_at"] = release["retrieved_at"]
    return source


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
    table: Path,
    *,
    pairs: str | None = None,
    verify: bool = False,
    cancel: CancelEvent | None = None,
) -> Iterator[tuple[sqlite3.Connection, dict[str, str]]]:
    """Read one immutable generation; the installer replaces, never edits it."""
    if not _allowed(table):
        raise WGSExtractError(
            f"GTEx track is outside allowed reference folders: {table}"
        )
    receipt = receipt_path(table)
    error = missing(table, pairs)

    def generations() -> tuple[tuple[int, int, int, int, int], ...]:
        try:
            return _generation(table), _generation(receipt)
        except OSError as exc:
            raise WGSExtractError(error) from exc

    with ExitStack() as connection:
        try:
            generation = generations()
            source = _source(pairs)
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
                raise WGSExtractError(error)
            uri = sqlite_readonly_uri(table.absolute(), immutable=True)
            db = connection.enter_context(closing(sqlite3.connect(uri, uri=True)))
            db.execute("PRAGMA trusted_schema=OFF")
            fields = (
                "cs, pip, afc" if pairs is None else ", ".join(gtex_pairs.TSV_COLUMNS)
            )
            sample = db.execute(
                f"SELECT chrom, pos, ref, alt, ordinal, tissue, gene, gene_name, "
                f"{fields} FROM association LIMIT 1"
            ).fetchone()
            if (
                db.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA
                or not sample
                or generation != generations()
            ):
                raise WGSExtractError(error)
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise WGSExtractError(error) from exc
        try:
            yield db, {**source, "database_sha256": digest}
        except sqlite3.Error as exc:
            raise WGSExtractError(error) from exc
        if generation != generations():
            raise WGSExtractError(error)


def installed(table: Path, *, pairs: str | None = None, verify: bool = False) -> bool:
    try:
        with open_track(table, pairs=pairs, verify=verify):
            return True
    except WGSExtractError:
        return False


def parse_variant(variant: str) -> tuple[str, int, str, str]:
    chrom, pos_text, ref, alt, build = variant.rsplit("_", 4)
    pos = int(pos_text)
    if not (
        build == "b38"
        and chrom.startswith("chr")
        and pos > 0
        and SEQUENCE.fullmatch(ref)
        and SEQUENCE.fullmatch(alt)
    ):
        raise ValueError(f"invalid GRCh38 GTEx variant ID {variant!r}")
    return chrom, pos, ref.upper(), alt.upper()


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
    if not (gene and 0 <= pip <= 1 and cs > 0):
        raise ValueError(f"invalid SuSiE association for {variant}, {gene}, {tissue}")
    return (
        *parse_variant(variant), ordinal, tissue, gene, gene_name, cs, pip,
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


def install(
    table: Path, *, pairs: str | None = None, cancel: CancelEvent | None = None
) -> None:
    if not _allowed(table):
        raise WGSExtractError(
            f"GTEx track is outside allowed reference folders: {table}"
        )
    require_pyarrow(shlex.join(["ref", "gtex", *install_flags(pairs)]))
    from pyarrow import ArrowException

    source = _source(pairs)
    table.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".gtex-v10-", dir=table.parent) as stage:
        archive = Path(stage, "gtex-v10.tar")
        logging.info(
            "Downloading public %s data from %s (%s).",
            label(pairs),
            source["source_url"],
            "174 MB"
            if pairs is None
            else "2.56 GB upstream archive; one tissue installed",
        )
        if not download_file(
            source["source_url"],
            str(archive),
            cancel_event=cancel,
            expected_sha256=source["source_sha256"],
            resolve_release_digest=False,
            use_mirrors=False,
        ):
            raise WGSExtractError(
                f"Could not download and verify {label(pairs)} (SHA-256)."
            )
        logging.info("Indexing the GTEx tissue summaries for offline lookup.")
        staged = Path(stage, table.name)
        receipt = receipt_path(staged)
        try:
            with closing(sqlite3.connect(staged)) as db:
                if pairs is None:
                    _load(archive, db, cancel)
                else:
                    gtex_pairs.load(archive, db, pairs, _SCHEMA, cancel, parse_variant)
            document = {**source, "database_sha256": _sha256(staged, cancel)}
            receipt.write_text(
                json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
        except (
            ArrowException,
            EOFError,
            tarfile.TarError,
            sqlite3.Error,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            raise WGSExtractError(f"Could not prepare {label(pairs)}: {exc}") from exc
        publish_output_set({receipt: receipt_path(table), staged: table})
