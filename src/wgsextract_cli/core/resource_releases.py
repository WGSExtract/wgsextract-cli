"""Which release of a public resource a run used (#1179).

The release is read offline, from the resource itself: ClinVar's VCF names
its release in ``##fileDate`` (with ``##source=ClinVar``), and a built-in gene
list is identified by its SHA-256 pin in the package. A resource that doesn't
say keeps ``release: null``: it is never guessed from a file name, date or
size. Terms come from the audited resource catalog
(``assets/resource_mirrors.json``); recording them grants no redistribution
rights (see #864, source rights and licence notices).
"""

from __future__ import annotations

import gzip
import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any, get_args

from wgsextract_cli.core.builds import build_from_reference_header
from wgsextract_cli.core.provenance_records import ReleaseBasis, ResourceRecord
from wgsextract_cli.core.results import current_result, fingerprint

NAMES = {
    "clinvar": "NCBI ClinVar",
    "revel": "REVEL",
    "spliceai": "SpliceAI",
    "alphamissense": "AlphaMissense",
    "pharmgkb": "PharmGKB (ClinPGx)",
    "phylop": "UCSC phyloP 100-way",
    "gnomad": "gnomAD",
    "cadd": "CADD",
    "gerp": "GERP++",
    "gene-list": "Report gene list",
}
# Resources a report section's findings rest on.
SECTION_RESOURCES = {
    "clinvar": ("clinvar", "gene-list"),
    "acmg_research": ("gene-list",),
}
_HEADER_LINES = 10_000
_ISO_DATE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")


def iso_date(text: str) -> str | None:
    """``2026-11-15`` from ``2026-11-15`` or ``20261115``; None otherwise."""
    match = _ISO_DATE.match(text.strip())
    return "-".join(match.groups()) if match else None


def vcf_header(path: str | Path) -> dict[str, str]:
    """The first value of each ``##key=value`` meta line of a (b)gzipped or
    plain VCF; empty when the file isn't one."""
    meta: dict[str, str] = {}
    opener: Any = gzip.open if str(path).lower().endswith(("gz", "bgz")) else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
            for number, line in enumerate(stream):
                if not line.startswith("##") or number > _HEADER_LINES:
                    break
                key, sep, value = line[2:].rstrip("\r\n").partition("=")
                if sep and not value.startswith("<"):
                    meta.setdefault(key, value)
    except (OSError, EOFError, UnicodeDecodeError):
        return {}
    return meta


def _entry(catalog_id: str) -> dict[str, Any] | None:
    from wgsextract_cli.core.resource_mirrors import resources

    return next((e for e in resources().values() if e["id"] == catalog_id), None)


def _catalog(resource_id: str, build: str | None) -> dict[str, Any] | None:
    """The audited catalog entry of this resource and build; for terms only,
    any of the resource's entries when the build is unknown."""
    from wgsextract_cli.core.resource_mirrors import resources

    return _entry(f"{resource_id}-{build}") or next(
        (e for e in resources().values() if str(e["id"]).startswith(f"{resource_id}-")),
        None,
    )


def _terms(record: ResourceRecord, entry: dict[str, Any] | None) -> None:
    if entry is not None:
        record.licence = entry.get("licence")
        record.licence_url = entry.get("licence_url")
        record.attribution = entry.get("attribution")


def _with_terms(record: ResourceRecord) -> ResourceRecord:
    entry = _catalog(record.id, record.build)
    _terms(record, entry)
    if entry is not None and entry["id"] == f"{record.id}-{record.build}":
        record.source_url = entry.get("upstream_url")
    return record


def describe(
    resource_id: str, path: str | Path, role: str = "annotation"
) -> ResourceRecord:
    """The identity of the resource file ``path``: its build and release when
    the file states them, fingerprint and audited terms. Reads only the file."""
    absolute = Path(path).expanduser().absolute()
    record = ResourceRecord(
        id=resource_id,
        name=NAMES.get(resource_id, resource_id),
        role=role,
        path=str(absolute),
        fingerprint=fingerprint(absolute),
    )
    if str(absolute).lower().endswith((".vcf", ".vcf.gz", ".vcf.bgz")):
        meta = vcf_header(absolute)
        reference = meta.get("reference")
        record.build = build_from_reference_header(reference) if reference else None
        if resource_id == "clinvar" and meta.get("source", "").strip() == "ClinVar":
            release = iso_date(meta.get("fileDate", ""))
            if release:
                record.release, record.release_basis = release, "file_header"
    return _with_terms(record)


def record(resource_id: str, path: str | Path | None, role: str = "annotation") -> None:
    """Record the resource on the running command's result. Never fails a run:
    an unreadable file is recorded with what is known."""
    if not path:
        return
    try:
        current_result().add_resource(describe(resource_id, path, role))
    except (OSError, ValueError) as error:  # identity is best effort
        logging.debug("Could not describe resource %s: %s", path, error)


def record_gene_list(list_id: str, path: Path, meta: dict[str, Any] | None) -> None:
    """Record the gene list a run read: a built-in one is the release its
    SHA-256 pin names; a user's file has no release."""
    entry = ResourceRecord(
        id="gene-list",
        name=meta["name"] if meta else path.name,
        role="gene_list",
        path=str(path.absolute()),
        fingerprint=fingerprint(path),
    )
    if meta is not None:
        entry.release, entry.release_basis = list_id, "pinned_sha256"
        entry.source_url = meta.get("download")
        _terms(entry, _entry(f"gene-list-{list_id}"))
    current_result().add_resource(entry)


def from_document(data: object) -> ResourceRecord | None:
    """A manifest's ``resources`` entry as a record; None when malformed."""
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return None
    names = {f.name for f in fields(ResourceRecord)}
    values = {k: v for k, v in data.items() if k in names}
    values.setdefault("name", data["id"])
    values.setdefault("role", "annotation")
    if values.get("release_basis") not in get_args(ReleaseBasis):
        values["release_basis"] = "unrecorded"
    if not values.get("release"):
        values["release"], values["release_basis"] = None, "unrecorded"
    try:
        return ResourceRecord(**values)
    except TypeError:
        return None


def manifest_resources(document: Mapping[str, Any]) -> list[ResourceRecord] | None:
    """The resources a manifest recorded; None for a manifest written before
    resources were recorded (so which releases it used is unknown)."""
    raw = document.get("resources")
    if not isinstance(raw, list):
        return None
    return [r for r in map(from_document, raw) if r is not None]


def cited_resources(
    folder: Path, report: Mapping[str, Any], sources: Iterable[Any]
) -> list[ResourceRecord]:
    """The resources of the runs a report shows (its sections' sources), so the
    report's manifest keeps the releases its findings rest on."""
    cited = {
        str(s.get("manifest"))
        for section in (report.get("sections") or {}).values()
        if isinstance(section, Mapping)
        for s in section.get("sources") or []
        if isinstance(s, Mapping) and s.get("manifest")
    }
    found: list[ResourceRecord] = []
    for source in sources:
        absolute = os.path.abspath(source.manifest)
        try:
            relative = Path(os.path.relpath(absolute, folder)).as_posix()
        except ValueError:
            relative = Path(absolute).as_posix()
        if relative in cited:
            found.extend(manifest_resources(source.document) or [])
    return found
