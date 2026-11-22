"""Semantic delta between two runs' result manifests (#1179).

Compares what two runs found, not their bytes: findings are matched by a
stable identity (section plus variant, or section plus label), and timestamps,
ordering, paths and generated files are ignored. A section whose run failed
or didn't run in either report is not compared, so its findings never show
as added or removed. Resource release changes are listed beside finding
changes as an association; the delta doesn't establish cause.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from wgsextract_cli.core import resource_releases
from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.errors import CodedError
from wgsextract_cli.core.output_schema import Constraints
from wgsextract_cli.core.provenance_records import ResourceRecord
from wgsextract_cli.core.release_recheck import load_manifest

SCHEMA: Literal["wgsextract.delta/1"] = "wgsextract.delta/1"
REPORT_SCHEMA = "wgsextract.report/1"
NOT_DIAGNOSIS = (
    "Technical results, not diagnosis. Confirm any finding with an accredited "
    "clinical laboratory before acting on it."
)
_LIMITATIONS = (
    "Release changes are shown beside finding changes as an association; the "
    "delta does not establish that a release change caused a finding change.",
    "A ClinVar finding added after a ClinVar update may be a reclassified call "
    "or a newly called variant: a report lists only P/LP calls, so the earlier "
    "one does not show whether the call was there.",
    "A section that failed or did not run in either report is not compared; "
    "its findings are not reported as added or removed.",
    "Each change carries the confidence label and disclaimer of the finding as "
    "reported; see docs/personal-report.md for what they mean.",
)

ReleaseChange = Literal[
    "same", "changed", "content_changed", "added", "removed", "unknown"
]
FindingChange = Literal[
    "added",
    "removed",
    "reclassified",
    "review_changed",
    "genotype_changed",
    "result_changed",
    "confidence_changed",
    "ambiguity_changed",
]
ChangeArea = Literal[
    "status", "warning", "metric", "output", "version", "tool", "reference"
]


class DeltaRunDocument(TypedDict):
    manifest: str
    command: str
    status: str
    finished: str | None
    version: str | None
    sample: Annotated[
        str | None, Constraints(description="Report sample, or the input's file name.")
    ]
    build: str | None
    resources_recorded: Annotated[
        bool,
        Constraints(
            description="False for a manifest written before releases were recorded."
        ),
    ]


class DeltaReleaseDocument(TypedDict):
    id: str
    name: str
    build: str | None
    before: str | None
    after: str | None
    change: ReleaseChange


class DeltaFindingStateDocument(TypedDict):
    value: str
    detail: str | None
    genotype: str | None
    zygosity: str | None
    confidence: str
    confidence_reasons: list[str]
    review_status: str | None
    stars: int | None
    pgx_status: str | None


class DeltaFindingDocument(TypedDict):
    section: str
    key: Annotated[
        str, Constraints(description="chrom:pos:ref:alt for a variant, else the label.")
    ]
    label: str
    gene: str | None
    changes: list[FindingChange]
    before: DeltaFindingStateDocument | None
    after: DeltaFindingStateDocument | None
    confidence: Annotated[
        str,
        Constraints(
            description="The finding's confidence label as reported (after, else before)."
        ),
    ]
    disclaimer: str
    release_changes: Annotated[
        list[str],
        Constraints(
            description="Resources this section rests on whose release changed "
            "between the runs (an association, not a cause)."
        ),
    ]


class DeltaSectionDocument(TypedDict):
    section: str
    title: str
    before_status: str | None
    after_status: str | None
    compared: bool
    note: str | None
    unchanged: int
    findings: list[DeltaFindingDocument]


class DeltaChangeDocument(TypedDict):
    area: ChangeArea
    key: str
    before: object
    after: object


class DeltaSummaryDocument(TypedDict):
    findings_added: int
    findings_removed: int
    findings_changed: int
    findings_unchanged: int
    sections_not_compared: int
    releases_changed: int
    resource_only: Annotated[
        bool,
        Constraints(
            description="Resources changed but no finding, section or run status did."
        ),
    ]


class DeltaDocument(TypedDict):
    schema: Literal["wgsextract.delta/1"]
    before: DeltaRunDocument
    after: DeltaRunDocument
    summary: DeltaSummaryDocument
    releases: list[DeltaReleaseDocument]
    sections: list[DeltaSectionDocument]
    changes: list[DeltaChangeDocument]
    disclaimer: str | None
    limitations: list[str]


def _report(document: Mapping[str, Any]) -> Mapping[str, Any] | None:
    details = document.get("details")
    if document.get("command") != "report":
        return None
    if not isinstance(details, Mapping) or details.get("schema") != REPORT_SCHEMA:
        raise CodedError(
            "unsupported_schema",
            "A report manifest's details are not a wgsextract.report/1 document "
            f"(schema {details.get('schema') if isinstance(details, Mapping) else None!r}).",
        )
    return details


def _sample(
    document: Mapping[str, Any], report: Mapping[str, Any] | None
) -> str | None:
    if report is not None:
        return (report.get("sample") or {}).get("name")
    for record in document.get("inputs") or []:
        if isinstance(record, Mapping) and record.get("role") in ("input", "vcf_input"):
            return Path(str(record.get("path") or "")).name or None
    return None


def _build(
    document: Mapping[str, Any],
    report: Mapping[str, Any] | None,
    resources: Sequence[ResourceRecord],
) -> str | None:
    found: list[object] = []
    if report is not None:
        sections = report.get("sections") or {}
        found += [
            (sections.get(n) or {}).get("build") for n in ("clinvar", "acmg_research")
        ]
        found += [
            f.get("value")
            for f in (sections.get("overview") or {}).get("facts") or []
            if isinstance(f, Mapping) and f.get("name") == "build"
        ]
    found.append(
        ((document.get("provenance") or {}).get("reference") or {}).get("build")
    )
    found += [r.build for r in resources]
    return next(
        (b for v in found if isinstance(v, str) and (b := canonical_build(v))), None
    )


def _run(
    path: Path, document: Mapping[str, Any], report: Mapping[str, Any] | None
) -> tuple[DeltaRunDocument, list[ResourceRecord] | None]:
    resources = resource_releases.manifest_resources(document)
    provenance = document.get("provenance") or {}
    return (
        DeltaRunDocument(
            manifest=str(path),
            command=str(document.get("command") or ""),
            status=str(document.get("status") or ""),
            finished=provenance.get("finished") or None,
            version=provenance.get("version") or None,
            sample=_sample(document, report),
            build=_build(document, report, resources or []),
            resources_recorded=resources is not None,
        ),
        resources,
    )


def _check_compatible(before: DeltaRunDocument, after: DeltaRunDocument) -> None:
    if before["command"] != after["command"]:
        raise CodedError(
            "delta_command_mismatch",
            f"Cannot compare a '{before['command']}' run with a '{after['command']}' "
            "run; give two manifests of the same command.",
        )
    if before["sample"] and after["sample"] and before["sample"] != after["sample"]:
        raise CodedError(
            "delta_sample_mismatch",
            f"The runs are of different samples ({before['sample']} and "
            f"{after['sample']}); a delta compares two runs of one sample.",
        )
    if before["build"] and after["build"] and before["build"] != after["build"]:
        raise CodedError(
            "delta_build_mismatch",
            f"The runs are on different reference builds ({before['build']} and "
            f"{after['build']}); their positions and findings aren't comparable.",
        )


def _releases(
    before: Sequence[ResourceRecord] | None, after: Sequence[ResourceRecord] | None
) -> list[DeltaReleaseDocument]:
    if before is None or after is None:
        return []
    old = {r.key(): r for r in before}
    new = {r.key(): r for r in after}
    changes: list[DeltaReleaseDocument] = []
    for key in sorted(old.keys() | new.keys(), key=str):
        a, b = old.get(key), new.get(key)
        either = b or a
        assert either is not None
        if a is None:
            change: ReleaseChange = "added"
        elif b is None:
            change = "removed"
        elif a.release and b.release:
            change = (
                "changed"
                if a.release != b.release
                else "same"
                if a.fingerprint == b.fingerprint
                else "content_changed"
            )
        else:
            change = (
                "same"
                if a.fingerprint and a.fingerprint == b.fingerprint
                else "unknown"
            )
        changes.append(
            DeltaReleaseDocument(
                id=either.id,
                name=either.name,
                build=either.build,
                before=a.release if a else None,
                after=b.release if b else None,
                change=change,
            )
        )
    return changes


def _finding_key(finding: Mapping[str, Any]) -> str:
    variant = finding.get("variant")
    if isinstance(variant, Mapping) and variant.get("pos") is not None:
        chrom = str(variant.get("chrom") or "")
        chrom = chrom[3:] if chrom.lower().startswith("chr") else chrom
        return f"{chrom}:{variant['pos']}:{variant.get('ref') or ''}:{variant.get('alt') or ''}"
    return str(finding.get("label") or "")


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _state(finding: Mapping[str, Any]) -> DeltaFindingStateDocument:
    variant = _mapping(finding.get("variant"))
    evidence = _mapping(variant.get("clinical_evidence"))
    pgx = _mapping(finding.get("pgx"))
    return DeltaFindingStateDocument(
        value=str(finding.get("value") or ""),
        detail=finding.get("detail"),
        genotype=variant.get("genotype"),
        zygosity=variant.get("zygosity"),
        confidence=str(finding.get("confidence") or "not_assessed"),
        confidence_reasons=sorted(
            str(r) for r in finding.get("confidence_reasons") or []
        ),
        review_status=evidence.get("review_status"),
        stars=evidence.get("stars"),
        pgx_status=pgx.get("status"),
    )


def _kinds(
    variant: bool, a: DeltaFindingStateDocument, b: DeltaFindingStateDocument
) -> list[FindingChange]:
    kinds: list[FindingChange] = []
    if variant and (a["genotype"], a["zygosity"]) != (b["genotype"], b["zygosity"]):
        kinds.append("genotype_changed")
    if a["value"] != b["value"] or (not variant and a["detail"] != b["detail"]):
        kinds.append("reclassified" if variant else "result_changed")
    if (a["review_status"], a["stars"]) != (b["review_status"], b["stars"]):
        kinds.append("review_changed")
    if (a["confidence"], a["confidence_reasons"]) != (
        b["confidence"],
        b["confidence_reasons"],
    ):
        kinds.append("confidence_changed")
    if a["pgx_status"] != b["pgx_status"]:
        kinds.append("ambiguity_changed")
    return kinds


def _keyed(findings: Sequence[Any]) -> dict[str, Mapping[str, Any]]:
    keyed: dict[str, Mapping[str, Any]] = {}
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        base = key = _finding_key(finding)
        n = 1
        while key in keyed:
            n += 1
            key = f"{base}#{n}"
        keyed[key] = finding
    return keyed


def _sections(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    releases: Sequence[DeltaReleaseDocument],
) -> list[DeltaSectionDocument]:
    changed = {r["id"] for r in releases if r["change"] not in ("same",)}
    old, new = before.get("sections") or {}, after.get("sections") or {}
    sections: list[DeltaSectionDocument] = []
    for name in [*old, *(n for n in new if n not in old)]:
        a, b = old.get(name) or {}, new.get(name) or {}
        if not (a.get("findings") or b.get("findings")) and a.get("status") == b.get(
            "status"
        ):
            continue
        status_a, status_b = a.get("status"), b.get("status")
        section = DeltaSectionDocument(
            section=name,
            title=str(b.get("title") or a.get("title") or name),
            before_status=status_a,
            after_status=status_b,
            compared=status_a == status_b == "ok",
            note=None,
            unchanged=0,
            findings=[],
        )
        sections.append(section)
        if not section["compared"]:
            section["note"] = (
                f"Not compared: the section is {str(status_a or 'absent').replace('_', ' ')} "
                f"in the earlier report and {str(status_b or 'absent').replace('_', ' ')} in "
                "the later one; its findings are not reported as added or removed."
            )
            continue
        related = sorted(
            changed & set(resource_releases.SECTION_RESOURCES.get(name, ()))
        )
        before_findings, after_findings = (
            _keyed(a.get("findings") or []),
            _keyed(b.get("findings") or []),
        )
        for key in [
            *before_findings,
            *(k for k in after_findings if k not in before_findings),
        ]:
            fa, fb = before_findings.get(key), after_findings.get(key)
            shown = fb or fa
            assert shown is not None
            sa = _state(fa) if fa else None
            sb = _state(fb) if fb else None
            if sa is None:
                kinds: list[FindingChange] = ["added"]
            elif sb is None:
                kinds = ["removed"]
            else:
                kinds = _kinds(isinstance(shown.get("variant"), Mapping), sa, sb)
            if not kinds:
                section["unchanged"] += 1
                continue
            section["findings"].append(
                DeltaFindingDocument(
                    section=name,
                    key=key,
                    label=str(shown.get("label") or key),
                    gene=shown.get("gene"),
                    changes=kinds,
                    before=sa,
                    after=sb,
                    confidence=str(shown.get("confidence") or "not_assessed"),
                    disclaimer=str(shown.get("disclaimer") or ""),
                    release_changes=related,
                )
            )
    return sections


def _counts(items: Sequence[Any], field: str) -> Counter[str]:
    return Counter(str(i.get(field)) for i in items if isinstance(i, Mapping))


def _changes(
    before: Mapping[str, Any], after: Mapping[str, Any], report: bool
) -> list[DeltaChangeDocument]:
    changes: list[DeltaChangeDocument] = []

    def add(area: ChangeArea, key: str, a: object, b: object) -> None:
        if a != b:
            changes.append(DeltaChangeDocument(area=area, key=key, before=a, after=b))

    add("status", "status", before.get("status"), after.get("status"))
    add(
        "status",
        "error",
        (before.get("error") or {}).get("code")
        or (before.get("error") or {}).get("kind"),
        (after.get("error") or {}).get("code")
        or (after.get("error") or {}).get("kind"),
    )
    warned_a = _counts(before.get("warnings") or [], "code")
    warned_b = _counts(after.get("warnings") or [], "code")
    for code in sorted(warned_a.keys() | warned_b.keys()):
        add("warning", code, warned_a.get(code, 0), warned_b.get(code, 0))
    prov_a, prov_b = before.get("provenance") or {}, after.get("provenance") or {}
    add("version", "wgsextract", prov_a.get("version"), prov_b.get("version"))
    tools_a, tools_b = prov_a.get("tools") or {}, prov_b.get("tools") or {}
    for tool in sorted(tools_a.keys() | tools_b.keys()):
        add("tool", tool, tools_a.get(tool), tools_b.get(tool))
    ref_a, ref_b = prov_a.get("reference") or {}, prov_b.get("reference") or {}
    add("reference", "fingerprint", ref_a.get("fingerprint"), ref_b.get("fingerprint"))
    if report:
        # A report's metrics and documents restate its sections, which are compared.
        return changes
    metrics_a, metrics_b = before.get("metrics") or {}, after.get("metrics") or {}
    for metric in sorted(metrics_a.keys() | metrics_b.keys()):
        add("metric", metric, metrics_a.get(metric), metrics_b.get(metric))

    def outputs(document: Mapping[str, Any]) -> dict[str, object]:
        return {
            f"{r.get('role')}:{Path(str(r.get('path') or '')).name}": r.get(
                "fingerprint"
            )
            for r in document.get("outputs") or []
            if isinstance(r, Mapping)
        }

    out_a, out_b = outputs(before), outputs(after)
    for key in sorted(out_a.keys() | out_b.keys()):
        add("output", key, out_a.get(key), out_b.get(key))
    return changes


def compare(before_path: Path, after_path: Path) -> DeltaDocument:
    """The semantic delta from the run in ``before_path`` to ``after_path``."""
    documents = [load_manifest(p) for p in (before_path, after_path)]
    reports = [_report(d) for d in documents]
    (run_a, res_a), (run_b, res_b) = (
        _run(p, d, r)
        for p, d, r in zip((before_path, after_path), documents, reports, strict=True)
    )
    _check_compatible(run_a, run_b)
    releases = _releases(res_a, res_b)
    report_a, report_b = reports
    sections = _sections(report_a, report_b, releases) if report_a and report_b else []
    changes = _changes(documents[0], documents[1], report_a is not None)
    findings = [f for s in sections for f in s["findings"]]
    added = sum("added" in f["changes"] for f in findings)
    removed = sum("removed" in f["changes"] for f in findings)
    not_compared = sum(not s["compared"] for s in sections)
    releases_changed = sum(r["change"] != "same" for r in releases)
    status_changed = any(c["area"] == "status" for c in changes)
    limitations = list(_LIMITATIONS if report_a else _LIMITATIONS[:1])
    if not (run_a["resources_recorded"] and run_b["resources_recorded"]):
        limitations.append(
            "A manifest written before releases were recorded leaves its "
            "resource releases unknown: no release comparison is made."
        )
    return DeltaDocument(
        schema=SCHEMA,
        before=run_a,
        after=run_b,
        summary=DeltaSummaryDocument(
            findings_added=added,
            findings_removed=removed,
            findings_changed=len(findings) - added - removed,
            findings_unchanged=sum(s["unchanged"] for s in sections),
            sections_not_compared=not_compared,
            releases_changed=releases_changed,
            resource_only=bool(releases_changed)
            and not findings
            and not status_changed
            and all(s["before_status"] == s["after_status"] for s in sections),
        ),
        releases=releases,
        sections=sections,
        changes=changes,
        disclaimer=NOT_DIAGNOSIS if report_a else None,
        limitations=limitations,
    )


def _state_text(state: DeltaFindingStateDocument | None) -> str:
    if state is None:
        return "-"
    parts = [state["value"]]
    if state["genotype"]:
        parts.append(state["genotype"])
    if state["stars"] is not None:
        parts.append(f"{state['stars']} stars")
    parts.append(state["confidence"])
    return ", ".join(parts)


def human_lines(delta: DeltaDocument) -> list[str]:
    """The delta as text for the terminal."""
    a, b = delta["before"], delta["after"]
    lines = [
        f"Delta of '{a['command']}' runs of {a['sample'] or b['sample'] or 'the sample'}: "
        f"{a['finished'] or a['manifest']} -> {b['finished'] or b['manifest']}"
    ]
    for r in delta["releases"]:
        build = f" ({r['build']})" if r["build"] else ""
        lines.append(
            f"  {r['name']}{build}: {r['before'] or 'unrecorded'} -> "
            f"{r['after'] or 'unrecorded'} ({r['change'].replace('_', ' ')})"
        )
    for s in delta["sections"]:
        if not s["compared"]:
            lines.append(f"  {s['title']}: {s['note']}")
            continue
        lines.append(
            f"  {s['title']}: {len(s['findings'])} changed, {s['unchanged']} unchanged"
        )
        for f in s["findings"]:
            related = (
                f"; release changed: {', '.join(f['release_changes'])}"
                if f["release_changes"]
                else ""
            )
            lines.append(
                f"    {', '.join(c.replace('_', ' ') for c in f['changes'])}: {f['label']} "
                f"[{_state_text(f['before'])} -> {_state_text(f['after'])}]{related}"
            )
    for c in delta["changes"]:
        lines.append(f"  {c['area']} {c['key']}: {c['before']} -> {c['after']}")
    summary = delta["summary"]
    if summary["resource_only"]:
        lines.append("  Only resources changed: no finding or run status changed.")
    elif not (delta["sections"] or delta["changes"] or delta["releases"]):
        lines.append("  No scientific change.")
    if delta["disclaimer"]:
        lines.append(delta["disclaimer"])
    return lines


def delta_schema() -> dict[str, object]:
    """The wgsextract.delta/1 JSON Schema, from the writer's types."""
    from wgsextract_cli.core.output_schema import Shape, output_schema

    return output_schema(
        DeltaDocument,
        shapes={
            DeltaDocument: Shape("delta", additional=False),
            DeltaRunDocument: Shape("run", additional=False),
            DeltaSummaryDocument: Shape("summary", additional=False),
            DeltaReleaseDocument: Shape("release", additional=False),
            DeltaSectionDocument: Shape("section", additional=False),
            DeltaFindingDocument: Shape("finding", additional=False),
            DeltaFindingStateDocument: Shape("findingState", additional=False),
            DeltaChangeDocument: Shape("change", additional=False),
        },
        schema_id="urn:wgsextract:schema:delta:1",
        title=SCHEMA,
        description="The semantic difference between two runs' result manifests "
        "(report-delta's details). See docs/recheck-and-delta.md.",
    )
