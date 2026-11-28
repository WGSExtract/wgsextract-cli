"""Read-only lookups the server answers itself, over HTTP (``GET /v1/<name>``)
and MCP (tools of the same name): a result manifest, rows of a ``vcf table``
output, and sections of a ``report``. They read files in the allowed folders
and change nothing, so MCP offers them without ``--allow-jobs``.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
import sqlite3
import zlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeGuard, TypeVar

from wgsextract_cli.core import acmg, personal_report
from wgsextract_cli.core.paths import sqlite_readonly_uri
from wgsextract_cli.core.results import MANIFEST_SUFFIX
from wgsextract_cli.core.results import SCHEMA_ID as RESULT_SCHEMA
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.core.variant_table import COLUMN_NAMES, COLUMNS, INTEGER, REAL

from .lookup_inputs import (
    CONFIDENCE_RANK,
    LookupInputs,
    ManifestInputs,
    ReportInputs,
    VariantInputs,
)
from .operations import PathGuard, RequestError
from .transport import VariantPage

_FAR = 2**62
_TYPES = {c.name: c.type for c in COLUMNS}
_REGION = re.compile(
    r"(?P<chrom>[^:\s]+)(?::(?P<start>\d[\d,]*)(?:-(?P<end>\d[\d,]*))?)?"
)
_RSID = re.compile(r"rs\d+")
_TRUE = ("1", "true", "yes")
_FALSE = ("0", "false", "no")
_SIGNIFICANCE_SPLIT = re.compile(r"[/|,]")
_ACMG_COLUMNS = tuple(name for name in COLUMN_NAMES if name.startswith("acmg_"))
InputT = TypeVar("InputT", bound=LookupInputs)


@dataclass(frozen=True)
class Query(Generic[InputT]):
    """A lookup: MCP tool ``name``, HTTP ``GET /v1/<route>``."""

    name: str
    route: str
    summary: str
    inputs: type[InputT]
    answer: Callable[[PathGuard, InputT], Mapping[str, object]]

    @property
    def properties(self) -> dict[str, dict[str, object]]:
        return self.inputs.properties()

    @property
    def required(self) -> tuple[str, ...]:
        return self.inputs.required()

    @property
    def schema(self) -> dict[str, object]:
        return self.inputs.schema()

    def run(
        self, guard: PathGuard, arguments: Mapping[str, object]
    ) -> dict[str, object]:
        return dict(self.answer(guard, self.inputs.parse(arguments)))

    def from_query_string(self, query: Mapping[str, list[str]]) -> dict[str, object]:
        """HTTP query parameters as JSON values of this query's schema."""
        arguments: dict[str, object] = {}
        properties = self.properties
        for name, values in query.items():
            spec = properties.get(name)
            value = values[-1]
            if spec is not None and spec["type"] == "array":
                # Comma-separated, repeated, or both.
                arguments[name] = [v for text in values for v in text.split(",") if v]
            elif spec is None:
                arguments[name] = value
            elif spec["type"] == "integer":
                try:
                    arguments[name] = int(value)
                except ValueError:
                    raise RequestError(
                        400, "usage", f"{name} must be an integer."
                    ) from None
            elif spec["type"] == "boolean":
                text = value.lower()
                if text not in _TRUE + _FALSE:
                    raise RequestError(400, "usage", f"{name} must be true or false.")
                arguments[name] = text in _TRUE
            else:
                arguments[name] = value
        return arguments


def _path(guard: PathGuard, arguments: LookupInputs) -> Path:
    text = arguments.path
    guard.check_path("path", text)
    return Path(os.path.expanduser(text))


def _read_json(path: Path) -> object:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (OSError, ValueError) as e:
        raise RequestError(404, "unreadable", f"Can't read {path}: {e}") from None


# Manifests -------------------------------------------------------------------


def _is_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def read_manifest(guard: PathGuard, arguments: ManifestInputs) -> dict[str, object]:
    path = _path(guard, arguments)
    if not path.name.endswith(MANIFEST_SUFFIX):
        raise RequestError(
            400, "usage", f"path must be a result manifest (*{MANIFEST_SUFFIX})."
        )
    document = _read_json(path)
    if not _is_object(document) or document.get("schema") != RESULT_SCHEMA:
        raise RequestError(400, "usage", f"{path} is not a {RESULT_SCHEMA} manifest.")
    if not arguments.full:
        document.pop("provenance", None)
    return document


# Variants --------------------------------------------------------------------


def _region(text: str) -> tuple[str, int, int]:
    match = _REGION.fullmatch(text.strip())
    if not match:
        raise RequestError(400, "usage", f"region {text!r} isn't chrom[:start[-end]].")
    # Beyond any chromosome, and within SQLite's integers.
    start = min(int((match["start"] or "1").replace(",", "")), _FAR)
    end = min(int((match["end"] or str(_FAR)).replace(",", "")), _FAR)
    if match["start"] and not match["end"]:
        end = start
    if start > end:
        raise RequestError(400, "usage", f"region {text!r} ends before it starts.")
    return match["chrom"], start, end


def _typed(name: str, text: str) -> str | int | float | None:
    if text == "":
        return None
    kind = _TYPES.get(name)
    try:
        if kind == INTEGER:
            return int(text)
        if kind == REAL:
            return float(text)
    except ValueError:
        return text
    return text


def _tsv_rows(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    stream = (
        gzip.open(path, "rt", encoding="utf-8", newline="")
        if path.name.lower().endswith(".gz")
        else open(path, encoding="utf-8", newline="")  # noqa: SIM115
    )
    with stream:
        reader = csv.reader(stream, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader, [])
        for number, values in enumerate(reader, 1):
            yield (
                number,
                {k: _typed(k, v) for k, v in zip(header, values, strict=False)},
            )


def _parquet_rows(
    path: Path, columns: set[str]
) -> Iterator[tuple[int, dict[str, Any]]]:
    from wgsextract_cli.core.table_writer import require_pyarrow

    try:
        pq = require_pyarrow()
    except WGSExtractError as e:
        raise RequestError(400, "usage", str(e)) from None
    from pyarrow import ArrowException

    number = 0
    try:
        with pq.ParquetFile(path) as parquet:
            present = [name for name in parquet.schema_arrow.names if name in columns]
            for batch in parquet.iter_batches(columns=present):
                for row in batch.to_pylist():
                    number += 1
                    yield number, row
    except ArrowException as e:
        raise RequestError(400, "usage", f"Can't read {path}: {e}") from None


def _json_value(value: Any) -> Any:
    """SQLite's typing is per value: a BLOB (or anything else that isn't
    JSON) in any column is returned as text."""
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _sqlite_rows(
    path: Path, after: int, where: list[str], values: list[Any]
) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        db = sqlite3.connect(sqlite_readonly_uri(path.resolve()), uri=True)
    except sqlite3.Error as e:
        raise RequestError(400, "usage", f"Can't open {path}: {e}") from None
    try:
        db.row_factory = sqlite3.Row
        sql = " AND ".join(["rowid > ?", *where])
        for row in db.execute(
            f"SELECT rowid, * FROM variants WHERE {sql} ORDER BY rowid",  # noqa: S608
            [after, *values],
        ):
            values_ = {k: _json_value(v) for k, v in dict(row).items()}
            yield values_.pop("rowid"), values_
    except sqlite3.Error as e:
        raise RequestError(
            400, "usage", f"{path} isn't a variant database: {e}"
        ) from None
    finally:
        db.close()


def query_variants(guard: PathGuard, arguments: VariantInputs) -> VariantPage:
    path = _path(guard, arguments)
    if not path.is_file():
        raise RequestError(404, "not_found", f"No variant table {path}.")
    gene = arguments.gene
    rsid = arguments.rsid
    if rsid is not None and not _RSID.fullmatch(rsid):
        raise RequestError(400, "usage", f"rsid {rsid!r} isn't an rsID (rs123).")
    region = _region(arguments.region) if arguments.region else None
    clinvar = (arguments.clinvar or "").lower()
    least = CONFIDENCE_RANK.get(arguments.min_confidence or "", -1)
    limit = arguments.limit or 50
    cursor = arguments.cursor or "0"
    # ASCII digits only (str.isdigit accepts "²"), within SQLite's integers.
    if not re.fullmatch(r"[0-9]+", cursor) or int(cursor) > _FAR:
        raise RequestError(400, "usage", "cursor must be a next_cursor value.")
    after = int(cursor)

    def keep(row: Mapping[str, object]) -> bool:
        if gene is not None and row.get("gene") != gene:
            return False
        if rsid is not None and rsid not in str(row.get("rsid") or "").split(";"):
            return False
        if region is not None:
            chrom, start, end = region
            pos = row.get("pos")
            if row.get("chrom") != chrom or not isinstance(pos, int):
                return False
            if not start <= pos <= end:
                return False
        if clinvar:
            terms = _SIGNIFICANCE_SPLIT.split(
                str(row.get("clinvar_significance") or "")
            )
            if clinvar not in (t.lower() for t in terms):
                return False
        rank = CONFIDENCE_RANK.get(str(row.get("confidence") or ""), -1)
        return least < 0 or rank >= least

    wanted = arguments.columns or COLUMN_NAMES
    name = path.name.lower()
    if name.endswith(".sqlite"):
        # Narrow with the database's indexes; keep() decides. An rsID can be
        # one of several in a cell (rs1;rs2), so that filter scans the table.
        where: list[str] = []
        values: list[Any] = []
        if gene is not None:
            where.append("gene = ?")
            values.append(gene)
        if rsid is not None:
            where.append("rsid LIKE ?")
            values.append(f"%{rsid}%")
        if region is not None:
            where.append("chrom = ? AND pos BETWEEN ? AND ?")
            values.extend(region)
        rows = _sqlite_rows(path, after, where, values)
    elif name.endswith(".parquet"):
        columns = set(wanted)
        if gene is not None:
            columns.add("gene")
        if rsid is not None:
            columns.add("rsid")
        if region is not None:
            columns.update(("chrom", "pos"))
        if clinvar:
            columns.add("clinvar_significance")
        if least >= 0:
            columns.add("confidence")
        rows = _parquet_rows(path, columns)
    elif name.endswith((".tsv", ".tsv.gz")):
        rows = _tsv_rows(path)
    else:
        raise RequestError(
            400,
            "usage",
            "path must be a vcf table output: *_variants.tsv, .parquet or .sqlite.",
        )
    found: list[dict[str, object]] = []
    next_cursor = None
    last = after
    try:
        for number, row in rows:
            if number <= after or not keep(row):
                continue
            if len(found) == limit:
                next_cursor = str(last)
                break
            found.append({k: row[k] for k in wanted if row.get(k) not in (None, "")})
            last = number
    except (OSError, EOFError, ValueError, csv.Error, zlib.error) as e:
        raise RequestError(400, "usage", f"Can't read {path}: {e}") from None
    finally:
        close = getattr(rows, "close", None)
        if close:
            close()
    page: VariantPage = {"path": str(path), "rows": found, "next_cursor": next_cursor}
    if any(key in row for row in found for key in _ACMG_COLUMNS):
        page["disclaimer"] = acmg.DISCLAIMER
    return page


# Report ----------------------------------------------------------------------

_REPORT_SUFFIX = "_personal_report.json"


def _report_file(guard: PathGuard, path: Path) -> Path:
    if path.is_dir():
        # A dangling link, or a report deleted since the glob, is skipped.
        dated = []
        for candidate in path.glob(f"*{_REPORT_SUFFIX}"):
            try:
                dated.append((candidate.stat().st_mtime, candidate))
            except OSError:
                continue
        found = [p for _, p in sorted(dated)]
        if not found:
            raise RequestError(
                404,
                "no_report",
                f"{path} has no report; run the report operation on it first.",
            )
        path = found[-1]
        guard.check_path("path", path)
    return path


def report_section(guard: PathGuard, arguments: ReportInputs) -> dict[str, object]:
    path = _report_file(guard, _path(guard, arguments))
    document = _read_json(path)
    if not _is_object(document) or document.get("schema") != (
        personal_report.SCHEMA_ID
    ):
        raise RequestError(
            400, "usage", f"{path} is not a {personal_report.SCHEMA_ID} report."
        )
    sections = document.get("sections") or {}
    if not _is_object(sections) or not all(_is_object(s) for s in sections.values()):
        raise RequestError(400, "usage", f"{path} has malformed report sections.")
    section_objects = {
        key: section for key, section in sections.items() if _is_object(section)
    }
    head: dict[str, object] = {
        "path": str(path),
        "generated": document.get("generated"),
        "sample": document.get("sample"),
        "disclaimer": document.get("disclaimer"),
    }
    name = arguments.section
    if not name:
        return {
            **head,
            "sections": {
                key: {"status": s.get("status"), "summary": s.get("summary")}
                for key, s in section_objects.items()
            },
        }
    if name not in section_objects:
        raise RequestError(
            404,
            "no_section",
            f"No section {name!r}; the report has {', '.join(section_objects)}.",
        )
    return {**head, "section": name, **section_objects[name]}


LookupQuery = Query[ManifestInputs] | Query[VariantInputs] | Query[ReportInputs]
QUERIES: dict[str, LookupQuery] = {
    q.name: q
    for q in (
        Query[ManifestInputs](
            "read_manifest",
            "manifest",
            "Read a result manifest (*.manifest.json) a WGS Extract command "
            "wrote: its outputs, metrics, warnings and details.",
            ManifestInputs,
            read_manifest,
        ),
        Query[VariantInputs](
            "query_variants",
            "variants",
            "Look up a sample's variants in a `vcf table` output (*_variants "
            ".sqlite, .tsv or .parquet) by gene, rsID, region, ClinVar "
            "significance or confidence. Returns at most `limit` rows and a "
            "next_cursor to continue. A page with automated ACMG classes "
            "(acmg_* columns, research only) carries their disclaimer.",
            VariantInputs,
            query_variants,
        ),
        Query[ReportInputs](
            "report_section",
            "report",
            "Read a sample's personal report (from the report operation): each "
            "section's status and summary, or one section in full.",
            ReportInputs,
            report_section,
        ),
    )
}
