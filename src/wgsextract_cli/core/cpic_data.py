"""The CPIC database release ``report --pgx-context`` reads its guidance from.

CPIC (Clinical Pharmacogenetics Implementation Consortium) publishes its
curated content under CC0 1.0, including a versioned PostgreSQL export of its
database. The release pinned in ``assets/report/cpic.json`` is downloaded once,
with ``--download`` only, into ``<reference library>/report/cpic`` and is
checked against its pinned SHA-256 whenever it is read. Only the drug, gene-drug
pair, guideline, publication and recommendation tables are parsed.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from wgsextract_cli.core.file_lock import build_lock
from wgsextract_cli.core.network_policy import NetworkRestricted, require_download
from wgsextract_cli.core.ref_library.assets import resolve_reference_library
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.utils import WGSExtractError

PIN: dict[str, Any] = json.loads(
    resources.files("wgsextract_cli")
    .joinpath("assets/report/cpic.json")
    .read_text(encoding="utf-8")
)
_TABLES = ("drug", "pair", "guideline", "publication", "recommendation")
# CPIC levels whose gene-drug pairs carry prescribing recommendations.
PRESCRIBING_LEVELS = ("A", "A/B", "B")


class CpicUnavailable(WGSExtractError):
    """The pinned CPIC release is neither in the library nor downloadable."""


@dataclass(frozen=True)
class Drug:
    id: str
    name: str
    guideline_id: str | None


@dataclass(frozen=True)
class Pair:
    gene: str
    drug_id: str
    level: str
    used_for_recommendation: bool


@dataclass(frozen=True)
class Guideline:
    id: str
    name: str
    url: str
    # The newest publication: (year, PMID, DOI).
    publication: tuple[str, str | None, str | None] | None


@dataclass(frozen=True)
class Recommendation:
    id: str
    guideline_id: str
    drug_id: str
    text: str
    classification: str
    implications: Mapping[str, str]
    phenotypes: Mapping[str, str]
    lookup: Mapping[str, str]
    population: str
    comments: str


@dataclass(frozen=True)
class CpicData:
    release: str
    sha256: str
    drugs: Mapping[str, Drug]
    pairs: tuple[Pair, ...]
    guidelines: Mapping[str, Guideline]
    recommendations: tuple[Recommendation, ...]
    _by_key: Mapping[str, Drug] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        index = {d.name.lower(): d for d in self.drugs.values()}
        index.update({d.id.lower(): d for d in self.drugs.values()})
        object.__setattr__(self, "_by_key", index)

    def drug(self, name: str) -> Drug | None:
        """A drug by CPIC's (generic) name or its CPIC id (``RxNorm:2670``),
        in any case."""
        return self._by_key.get(name.strip().lower())

    def pairs_of(self, drug_id: str) -> list[Pair]:
        return [p for p in self.pairs if p.drug_id == drug_id]

    def recommendations_of(self, drug_id: str) -> list[Recommendation]:
        return [r for r in self.recommendations if r.drug_id == drug_id]


def describe() -> dict[str, str]:
    """The pinned release's identity, for manifests and the report."""
    return {
        "release": str(PIN["release"]),
        "published": str(PIN["published"]),
        "sha256": str(PIN["sha256"]),
        "url": str(PIN["url"]),
        "license": str(PIN["license"]),
        "attribution": str(PIN["attribution"]),
    }


def ensure(library: Path | None = None) -> Path:
    """The pinned CPIC export in the reference library, downloaded (with
    ``--download`` only) and checked against its SHA-256 if missing or changed."""
    folder = Path(library or resolve_reference_library()) / "report" / "cpic"
    path = folder / Path(PIN["url"]).name
    if _verified(path):
        return path
    require_download(f"CPIC database {PIN['release']}")
    folder.mkdir(parents=True, exist_ok=True)
    with build_lock(str(folder / f"{path.name}.lock")):
        if _verified(path):
            return path
        logging.info(f"Downloading the CPIC database {PIN['release']}...")
        try:
            ok = download_file(
                PIN["url"],
                str(path),
                expected_sha256=PIN["sha256"],
                resolve_release_digest=False,
            )
        except NetworkRestricted:
            raise
        except (OSError, WGSExtractError) as error:
            ok = False
            logging.debug("CPIC download failed: %s", error)
        if not ok or not _verified(path):
            raise CpicUnavailable(
                f"Cannot download the verified CPIC database {PIN['release']}. "
                "If offline, connect once to populate the reference library."
            )
    return path


def _verified(path: Path) -> bool:
    if not path.is_file():
        return False
    return bool(hashlib.sha256(path.read_bytes()).hexdigest() == PIN["sha256"])


def load(library: Path | None = None) -> CpicData:
    path = ensure(library)
    return _load(str(path), path.stat().st_mtime_ns)


@cache
def _load(path: str, _mtime: int) -> CpicData:
    tables = dict(_copy_tables(path))
    missing = [name for name in _TABLES if name not in tables]
    if missing:
        raise CpicUnavailable(f"{path} has no {', '.join(missing)} table.")
    publications: dict[str, tuple[str, str | None, str | None]] = {}
    for row in tables["publication"]:
        guideline = row.get("guidelineid")
        entry = (row.get("year", ""), row.get("pmid") or None, row.get("doi") or None)
        if guideline and entry > publications.get(guideline, ("", None, None)):
            publications[guideline] = entry
    return CpicData(
        release=str(PIN["release"]),
        sha256=str(PIN["sha256"]),
        drugs={
            row["drugid"]: Drug(row["drugid"], row["name"], row.get("guidelineid"))
            for row in tables["drug"]
        },
        pairs=tuple(
            Pair(
                row["genesymbol"],
                row["drugid"],
                row.get("cpiclevel") or "",
                row.get("usedforrecommendation") == "t",
            )
            for row in tables["pair"]
            if row.get("removed") != "t"
        ),
        guidelines={
            row["id"]: Guideline(
                row["id"],
                row["name"],
                row.get("url") or "",
                publications.get(row["id"]),
            )
            for row in tables["guideline"]
        },
        recommendations=tuple(
            Recommendation(
                id=row["id"],
                guideline_id=row.get("guidelineid") or "",
                drug_id=row["drugid"],
                text=row.get("drugrecommendation") or "",
                classification=row.get("classification") or "",
                implications=_json_map(row.get("implications", "")),
                phenotypes=_json_map(row.get("phenotypes", "")),
                lookup=_json_map(row.get("lookupkey", "")),
                population=(row.get("population") or "general").strip(),
                comments=row.get("comments") or "",
            )
            for row in tables["recommendation"]
        ),
    )


def _json_map(text: str) -> dict[str, str]:
    value = json.loads(text) if text else {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def _copy_tables(path: str) -> Iterator[tuple[str, list[dict[str, str]]]]:
    """The ``COPY cpic.<table> (...) FROM stdin`` blocks of a pg_dump, with
    NULL as an empty string."""
    with gzip.open(path, "rt", encoding="utf-8", newline="\n") as stream:
        for line in stream:
            if not line.startswith("COPY cpic."):
                continue
            name = line[len("COPY cpic.") : line.index(" ", len("COPY cpic."))]
            columns = line[line.index("(") + 1 : line.index(")")].split(", ")
            rows: list[dict[str, str]] = []
            for record in stream:
                if record.rstrip("\n") == "\\.":
                    break
                if name in _TABLES:
                    fields = record.rstrip("\n").split("\t")
                    rows.append(dict(zip(columns, map(_unescape, fields), strict=True)))
            if name in _TABLES:
                yield name, rows


_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "b": "\b", "f": "\f", "v": "\v"}


def _unescape(field: str) -> str:
    """A pg_dump COPY text field: ``\\N`` (NULL) is empty; backslash escapes."""
    if field == "\\N":
        return ""
    if "\\" not in field:
        return field
    out, i = [], 0
    while i < len(field):
        char = field[i]
        if char == "\\" and i + 1 < len(field):
            following = field[i + 1]
            out.append(_ESCAPES.get(following, following))
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)
