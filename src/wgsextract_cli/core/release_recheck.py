"""Recheck the resource releases result manifests recorded (#1179).

Compares each resource's recorded release with the newest release, taken
from an explicit answers file (offline) or, only when asked (``online``),
from the resource's documented public release listing. The online query is a
plain GET of a fixed public URL: no sample data, paths, positions or file
contents are sent. Nothing is downloaded and no analysis is rerun; a failed
lookup is ``lookup_error``, never ``up_to_date``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict
from urllib.error import URLError
from urllib.request import Request, urlopen

from wgsextract_cli.core import read_scope, resource_releases
from wgsextract_cli.core.errors import CodedError
from wgsextract_cli.core.output_schema import Constraints
from wgsextract_cli.core.provenance_records import ResourceRecord
from wgsextract_cli.core.utils import UsageError

SCHEMA: Literal["wgsextract.recheck/1"] = "wgsextract.recheck/1"
ANSWERS_SCHEMA: Literal["wgsextract.release_answers/1"] = "wgsextract.release_answers/1"
# The documented public listings of ClinVar's weekly VCF releases
# (https://ftp.ncbi.nlm.nih.gov/pub/clinvar/README.txt); `ref clinvar`
# installs the newest of them.
RELEASE_LISTINGS: dict[tuple[str, str], str] = {
    ("clinvar", "hg38"): "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/weekly/",
    ("clinvar", "hg19"): "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/weekly/",
}
_LISTED_RELEASE = {"clinvar": re.compile(r"clinvar_(\d{8})\.vcf\.gz\b")}
_MAX_LISTING = 4 << 20
_TIMEOUT = 30
# `ref <id>` installs these resources (an explicit download).
_INSTALLERS = {
    "clinvar", "revel", "gnomad", "spliceai", "alphamissense", "pharmgkb",
    "phylop", "cadd", "gerp",
}  # fmt: skip
NOTHING_RUN = (
    "Nothing was downloaded and no analysis was rerun; install a newer release "
    "yourself, then rerun the commands listed."
)
# How to give an analysis a resource file of your choosing.
_FILE_OPTIONS = {"clinvar": "vcf clinvar --clinvar-file"}

Status = Literal[
    "up_to_date", "newer_available", "different_release", "unknown", "lookup_error"
]
Installed = Literal["same", "changed", "missing", "unknown"]


class RecheckUseDocument(TypedDict):
    command: str
    manifest: str
    finished: str | None


class RecheckResourceDocument(TypedDict):
    id: str
    name: str
    build: str | None
    used_release: Annotated[
        str | None,
        Constraints(description="The release the runs recorded; null when unrecorded."),
    ]
    release_basis: str
    latest_release: Annotated[
        str | None,
        Constraints(description="The newest release the answers name, or null."),
    ]
    status: Status
    reason: str
    installed: Annotated[
        Installed,
        Constraints(
            description="Whether the file at the recorded path is still the one "
            "the runs used (by fingerprint)."
        ),
    ]
    installed_release: str | None
    used_by: list[RecheckUseDocument]
    next_step: str | None
    source_url: str | None
    licence: str | None
    licence_url: str | None
    attribution: str | None


class RecheckAnswersDocument(TypedDict):
    mode: Literal["answers_file", "online"]
    path: Annotated[str | None, Constraints(description="The answers file read.")]
    sha256: Annotated[
        str | None, Constraints(description="SHA-256 of the answers file read.")
    ]
    retrieved: Annotated[
        str | None,
        Constraints(description="When the answers were looked up (ISO 8601)."),
    ]
    source: str | None
    contacted: Annotated[
        list[str],
        Constraints(
            description="Public URLs queried (online only); each request carried "
            "no sample data."
        ),
    ]


class RecheckDocument(TypedDict):
    schema: Literal["wgsextract.recheck/1"]
    manifests: list[str]
    unrecorded_manifests: Annotated[
        list[str],
        Constraints(
            description="Manifests written before releases were recorded: their "
            "resources are unknown."
        ),
    ]
    answers: RecheckAnswersDocument
    resources: list[RecheckResourceDocument]
    counts: dict[str, int]
    note: str


class AnswerDocument(TypedDict, total=False):
    id: str
    build: str | None
    latest: str | None
    error: str


class AnswersDocument(TypedDict):
    schema: Literal["wgsextract.release_answers/1"]
    retrieved: str
    source: str
    answers: list[AnswerDocument]


@dataclass
class _Used:
    record: ResourceRecord
    uses: list[RecheckUseDocument] = field(default_factory=list)
    # The commands to rerun to use a newer release, analyses before reports.
    rerun: list[str] = field(default_factory=list)


def _rerun_commands(document: Mapping[str, Any], resource_id: str) -> list[str]:
    """A report shows other runs' resources: rerun the runs of the sections
    resting on it, then the report itself."""
    command = str(document.get("command") or "")
    details = document.get("details")
    if command != "report" or not isinstance(details, Mapping):
        return [command] if command else []
    commands = [
        str(source.get("command"))
        for name, section in (details.get("sections") or {}).items()
        if resource_id in resource_releases.SECTION_RESOURCES.get(name, ())
        and isinstance(section, Mapping)
        for source in section.get("sources") or []
        if isinstance(source, Mapping) and source.get("command")
    ]
    return [*dict.fromkeys(commands), command]


def load_manifest(path: Path) -> dict[str, Any]:
    """A result manifest, or a coded error naming what is wrong with it."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CodedError(
            "manifest_unreadable", f"Cannot read result manifest {path}: {error}"
        ) from error
    if (
        not isinstance(document, dict)
        or document.get("schema") != "wgsextract.result/1"
    ):
        raise CodedError(
            "unsupported_schema",
            f"{path} is not a wgsextract.result/1 manifest (schema "
            f"{document.get('schema') if isinstance(document, dict) else None!r}); "
            "give the run's .manifest.json.",
        )
    return document


def used_resources(
    manifests: Sequence[Path],
) -> tuple[list[_Used], list[str]]:
    """Resources the manifests recorded, merged by resource, build and
    release; and the manifests that predate recording them."""
    merged: dict[tuple[str, str | None, str | None, str | None], _Used] = {}
    unrecorded: list[str] = []
    for path in manifests:
        document = load_manifest(path)
        records = resource_releases.manifest_resources(document)
        if records is None:
            unrecorded.append(str(path))
            continue
        finished = (document.get("provenance") or {}).get("finished") or None
        for record in records:
            key = (record.id, record.build, record.release, record.fingerprint)
            used = merged.setdefault(key, _Used(record))
            use = RecheckUseDocument(
                command=str(document.get("command") or ""),
                manifest=str(path),
                finished=finished,
            )
            if use not in used.uses:
                used.uses.append(use)
            for rerun in _rerun_commands(document, record.id):
                if rerun not in used.rerun:
                    used.rerun.append(rerun)
    return sorted(merged.values(), key=_order), unrecorded


def _order(used: _Used) -> tuple[str, str, str]:
    r = used.record
    return r.id, r.build or "", r.release or ""


def read_answers(path: Path) -> tuple[AnswersDocument, str]:
    """An answers file and its SHA-256; a coded error when it isn't one."""
    try:
        data = path.read_bytes()
        document = json.loads(data)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CodedError(
            "answers_unreadable", f"Cannot read release answers {path}: {error}"
        ) from error
    if not isinstance(document, dict) or document.get("schema") != ANSWERS_SCHEMA:
        raise CodedError(
            "unsupported_schema",
            f"{path} is not a {ANSWERS_SCHEMA} answers file; see "
            "docs/recheck-and-delta.md for its format.",
        )
    answers = document.get("answers")
    if not isinstance(answers, list) or not all(
        isinstance(a, dict)
        and isinstance(a.get("id"), str)
        and isinstance(a.get("build"), (str, type(None)))
        and isinstance(a.get("latest"), (str, type(None)))
        and isinstance(a.get("error", ""), str)
        for a in answers
    ):
        raise CodedError(
            "unsupported_schema",
            f"{path}: 'answers' must be a list of {{id, build, latest}} entries.",
        )
    return (
        AnswersDocument(
            schema=ANSWERS_SCHEMA,
            retrieved=str(document.get("retrieved") or ""),
            source=str(document.get("source") or ""),
            answers=[_answer(a) for a in answers],
        ),
        hashlib.sha256(data).hexdigest(),
    )


def _answer(data: Mapping[str, Any]) -> AnswerDocument:
    answer = AnswerDocument(id=data["id"], build=data["build"], latest=data["latest"])
    if data.get("error"):
        answer["error"] = data["error"]
    return answer


def online_targets(used: Iterable[_Used]) -> list[tuple[tuple[str, str], str]]:
    """The listings an online recheck of these resources queries."""
    keys = sorted(
        {
            (u.record.id, u.record.build)
            for u in used
            if (u.record.id, u.record.build) in RELEASE_LISTINGS
        },
        key=str,
    )
    return [
        ((rid, str(build)), RELEASE_LISTINGS[(rid, str(build))]) for rid, build in keys
    ]


def _read_listing(response: Any) -> bytes:
    """The whole listing; a truncated or oversized one is an error, never a
    partial list of releases."""
    length = response.headers.get("Content-Length")
    if length is not None and int(length) > _MAX_LISTING:
        raise ValueError("the listing is unexpectedly large")
    body: bytes = response.read() if length else response.read(_MAX_LISTING + 1)
    if len(body) > _MAX_LISTING or (length and len(body) != int(length)):
        raise ValueError("the listing was truncated or too large")
    return body


def lookup_online(used: Sequence[_Used]) -> AnswersDocument:
    """Ask each resource's public release listing for its newest release.

    Each request is a GET of the listing URL alone, with no query or body."""
    answers: list[AnswerDocument] = []
    contacted: list[str] = []
    for (resource_id, build), url in online_targets(used):
        contacted.append(url)
        logging.info("Asking %s for the newest %s release.", url, resource_id)
        try:
            request = Request(url, headers={"User-Agent": "wgsextract-cli"})
            with urlopen(request, timeout=_TIMEOUT) as response:
                text = _read_listing(response).decode("utf-8", "replace")
            releases = sorted(
                {
                    date
                    for match in _LISTED_RELEASE[resource_id].findall(text)
                    if (date := resource_releases.iso_date(match))
                }
            )
            if not releases:
                raise ValueError("the listing names no release")
            answers.append(
                AnswerDocument(id=resource_id, build=build, latest=releases[-1])
            )
        except (OSError, URLError, ValueError, HTTPException) as error:
            answers.append(
                AnswerDocument(
                    id=resource_id, build=build, latest=None, error=str(error)
                )
            )
    return AnswersDocument(
        schema=ANSWERS_SCHEMA,
        retrieved=datetime.now(timezone.utc).isoformat(),
        source="; ".join(contacted) or "no public release listing applies",
        answers=answers,
    )


def missing_answers_message(used: Sequence[_Used], command: str) -> str:
    """The error for a recheck given neither answers nor online permission."""
    names = ", ".join(
        f"{u.record.name} ({u.record.build or 'build unknown'}, used "
        f"{u.record.release or 'release unrecorded'})"
        for u in used
    )
    hosts = sorted({url.split("/")[2] for _key, url in online_targets(used)})
    online = (
        f"or rerun '{command} --online' to ask {', '.join(hosts)} for the newest "
        "release (sends no sample data: only a request for the public listing)"
        if hosts
        else "no public release listing is known for these resources"
    )
    return (
        f"Release answers are missing for: {names}. Give --answers FILE to "
        f"check offline, {online}."
    )


def _status(
    record: ResourceRecord, answer: AnswerDocument | None
) -> tuple[Status, str]:
    if answer is not None and answer.get("error"):
        return "lookup_error", f"The release lookup failed: {answer['error']}"
    if record.release is None:
        return "unknown", "The run didn't record which release it used."
    latest = answer.get("latest") if answer else None
    if not latest:
        return "unknown", "The answers name no newer release for this resource."
    if latest == record.release:
        return "up_to_date", "The run used the newest release."
    used, newest = (
        resource_releases.iso_date(record.release),
        resource_releases.iso_date(latest),
    )
    if used and newest and newest > used:
        return "newer_available", f"Release {latest} is newer than {record.release}."
    if used and newest and newest < used:
        return (
            "unknown",
            f"The answers name {latest}, older than the release used: they are "
            "out of date.",
        )
    return "different_release", f"The answers name release {latest}."


def _installed(record: ResourceRecord) -> tuple[Installed, str | None]:
    if not record.path or record.fingerprint is None:
        return "unknown", None
    path = Path(record.path)
    if not read_scope.may_follow(path):
        return "unknown", None  # a server job follows only allowed folders
    if not path.is_file():
        return "missing", None
    now = resource_releases.describe(record.id, path, record.role)
    if now.fingerprint == record.fingerprint:
        return "same", record.release
    return "changed", now.release


def _next_step(
    record: ResourceRecord,
    status: Status,
    installed: Installed,
    commands: Sequence[str],
    latest: str | None,
) -> str | None:
    rerun = f"rerun {', '.join(commands)}" if commands else "rerun the runs above"
    if installed == "changed":
        return f"The installed file has changed since these runs; {rerun} to use it."
    if status not in ("newer_available", "different_release"):
        return None
    wanted = f"release {latest}" if latest else "the newer release"
    # `ref <id>` installs the release this WGS Extract pins (often an audited
    # mirror snapshot), which may be older than the newest one.
    pinned = (
        f" ('wgsextract ref {record.id}' installs the release this WGS Extract "
        "pins, which may be older)"
        if record.id in _INSTALLERS
        else ""
    )
    option = _FILE_OPTIONS.get(record.id)
    how = f" and pass it with '{option} FILE'" if option else ""
    source = (
        f" from {record.source_url.rsplit('/', 1)[0]}/" if record.source_url else ""
    )
    return f"Download {wanted}{source}{how}{pinned}, then {rerun}."


def recheck(
    manifests: Sequence[Path],
    *,
    answers_file: Path | None,
    online: bool,
    command: str = "wgsextract ref recheck",
) -> tuple[RecheckDocument, AnswersDocument | None]:
    """The recheck document, and the answers an online lookup fetched."""
    if answers_file is not None and online:
        raise UsageError("Give either --answers or --online, not both.")
    used, unrecorded = used_resources(manifests)
    fetched: AnswersDocument | None = None
    sha256: str | None = None
    if not used:
        answers = AnswersDocument(
            schema=ANSWERS_SCHEMA, retrieved="", source="", answers=[]
        )
    elif answers_file is not None:
        answers, sha256 = read_answers(answers_file)
    elif online:
        answers = fetched = lookup_online(used)
    else:
        raise UsageError(missing_answers_message(used, command))
    by_key = {(a["id"], a.get("build")): a for a in answers["answers"]}
    resources: list[RecheckResourceDocument] = []
    for item in used:
        record = item.record
        answer = by_key.get((record.id, record.build)) or by_key.get((record.id, None))
        status, reason = _status(record, answer)
        installed, installed_release = _installed(record)
        resources.append(
            RecheckResourceDocument(
                id=record.id,
                name=record.name,
                build=record.build,
                used_release=record.release,
                release_basis=record.release_basis,
                latest_release=(answer or {}).get("latest"),
                status=status,
                reason=reason,
                installed=installed,
                installed_release=installed_release,
                used_by=item.uses,
                next_step=_next_step(
                    record, status, installed, item.rerun, (answer or {}).get("latest")
                ),
                source_url=record.source_url,
                licence=record.licence,
                licence_url=record.licence_url,
                attribution=record.attribution,
            )
        )
    counts = {
        status: sum(r["status"] == status for r in resources)
        for status in (
            "up_to_date",
            "newer_available",
            "different_release",
            "unknown",
            "lookup_error",
        )
    }
    document = RecheckDocument(
        schema=SCHEMA,
        manifests=[str(p) for p in manifests],
        unrecorded_manifests=unrecorded,
        answers=RecheckAnswersDocument(
            mode="online" if online else "answers_file",
            path=str(answers_file) if answers_file else None,
            sha256=sha256,
            retrieved=answers["retrieved"] or None,
            source=answers["source"] or None,
            contacted=[url for _key, url in online_targets(used)] if online else [],
        ),
        resources=resources,
        counts=counts,
        note=NOTHING_RUN,
    )
    return document, fetched


def human_lines(document: RecheckDocument) -> list[str]:
    """The recheck as text for the terminal."""
    answers = document["answers"]
    if answers["mode"] == "online":
        origin = "online: " + (", ".join(answers["contacted"]) or "nothing to ask")
    else:
        origin = (
            f"answers file {answers['path']}"
            if answers["path"]
            else "no answers needed"
        )
    lines = [
        f"Resource releases recorded by {len(document['manifests'])} manifest(s) ({origin})"
    ]
    if answers["retrieved"]:
        lines.append(f"  answers retrieved {answers['retrieved']}")
    for r in document["resources"]:
        build = f" ({r['build']})" if r["build"] else ""
        lines.append(
            f"  {r['name']}{build}: used {r['used_release'] or 'unrecorded'}, newest "
            f"{r['latest_release'] or 'unknown'}: {r['status'].replace('_', ' ')}. {r['reason']}"
        )
        if r["installed"] == "changed":
            lines.append(
                f"    installed now: {r['installed_release'] or 'release unrecorded'} (file changed)"
            )
        if r["next_step"]:
            lines.append(f"    {r['next_step']}")
    for path in document["unrecorded_manifests"]:
        lines.append(
            f"  {path}: written before releases were recorded; its resources are unknown."
        )
    if not document["resources"] and not document["unrecorded_manifests"]:
        lines.append("  No public resources were recorded.")
    lines.append(document["note"])
    return lines


def answers_json(answers: Mapping[str, Any]) -> str:
    return json.dumps(answers, indent=2, ensure_ascii=False) + "\n"


def recheck_schema() -> dict[str, object]:
    """The wgsextract.recheck/1 JSON Schema, from the writer's types."""
    from wgsextract_cli.core.output_schema import Shape, output_schema

    return output_schema(
        RecheckDocument,
        shapes={
            RecheckDocument: Shape("recheck", additional=False),
            RecheckAnswersDocument: Shape("answers", additional=False),
            RecheckResourceDocument: Shape("resource", additional=False),
            RecheckUseDocument: Shape("use", additional=False),
        },
        schema_id="urn:wgsextract:schema:recheck:1",
        title=SCHEMA,
        description="Recorded resource releases compared with the newest ones "
        "(ref recheck's details). See docs/recheck-and-delta.md.",
    )
