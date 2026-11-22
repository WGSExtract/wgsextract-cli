"""`lookup`: a sample's genotype, evidence state and report findings at an
rsID, locus or gene."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wgsextract_cli.api.lookup import LookupParams
from wgsextract_cli.cli.registration.lookup import register as register
from wgsextract_cli.core.lookup import Request
from wgsextract_cli.core.lookup import lookup as run_query
from wgsextract_cli.core.lookup.interpretation import report_age_days
from wgsextract_cli.core.ref_library.assets import (
    reference_library_of,
    resolve_reference_library,
)
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.variant_confidence import thresholds_from


def _call_line(call: Mapping[str, Any]) -> str:
    alleles = "/".join(str(a) if a is not None else "." for a in call["alleles"])
    if call.get("phased"):
        alleles = "|".join(str(a) if a is not None else "." for a in call["alleles"])
    extras = [
        f"{k} {call[v]}"
        for k, v in (("DP", "depth"), ("GQ", "genotype_quality"), ("PS", "phase_set"))
        if call.get(v) is not None
    ]
    if call["filter"] not in ("PASS", "."):
        extras.append(f"FILTER {call['filter']}")
    label = call.get("confidence") or call["state"]
    ids = f" {','.join(call['ids'])}" if call.get("ids") else ""
    return (
        f"  {call['chrom']}:{call['pos']}{ids} {call['ref']}>"
        f"{','.join(call['alts']) or '.'}  {call['genotype']} ({alleles}), "
        f"{label}{'; ' + ', '.join(extras) if extras else ''}"
    )


def _print_interpretation(found: Mapping[str, Any]) -> None:
    status = found["status"]
    if status == "no_report":
        human_output(f"\nReport: none found; run '{found['command']}' to add results.")
        return
    if status == "ambiguous_report":
        human_output("\nReport: several found; choose one with --report:")
        for path in found["candidates"]:
            human_output(f"  {path}")
        return
    report = found["report"]
    human_output(f"\nReport {report['path']} ({report.get('generated')}):")
    for finding in found["findings"]:
        human_output(
            f"  [{finding['section']}] {finding['label']}: {finding['value']} "
            f"(confidence {finding['confidence']}; {finding['disclaimer']})"
        )
    for section in found["sections"]:
        if section["status"] == "not_run":
            commands = "; ".join(m["command"] for m in section.get("missing") or [])
            human_output(f"  {section['title']}: not run ({commands})")
        elif not section["matched"]:
            human_output(f"  {section['title']}: {section['status']}, nothing here")
    human_output(f"  {found['limitations'][0]}")


def _print(document: Mapping[str, Any]) -> None:
    resolution = document["resolution"]
    human_output(
        f"Lookup of {', '.join(f'{k} {v}' for k, v in document['query'].items())} "
        f"in {document['input']['sample']} ({document['build']})"
    )
    if resolution.get("kind") == "rsid" and resolution.get("current") not in (
        None,
        resolution["query"],
    ):
        human_output(f"  {resolution['query']} is now {resolution['current']} (dbSNP)")
    if resolution["status"] == "ambiguous":
        human_output(resolution["message"])
        for candidate in resolution["candidates"]:
            name = f"{candidate['symbol']} " if candidate.get("symbol") else ""
            human_output(f"  {name}--locus {candidate['locus']}")
        return
    site = document.get("site")
    if site is not None:
        human_output(
            f"{site['locus']['text']}: {site['status']} ({site['evidence_state']})"
            + (f", genotype {site['genotype']}" if site.get("genotype") else "")
        )
        human_output(f"  {site['reason']}")
        for call in site["calls"]:
            human_output(_call_line(call))
    region = document.get("region")
    if region is not None:
        bases = ", ".join(f"{k} {v:,}" for k, v in region["evidence"]["bases"].items())
        human_output(f"{region['locus']['text']}: {region['call_count']} calls")
        human_output(f"  Bases: {bases} ({region['evidence']['basis']})")
        for call in region["calls"]:
            human_output(_call_line(call))
        if region["next_cursor"]:
            human_output(f"  More: --cursor {region['next_cursor']}")
    _print_interpretation(document["interpretation"])
    human_output(f"\n{document['disclaimer']}")


def _warnings(document: Mapping[str, Any], params: LookupParams) -> None:
    resolution = document["resolution"]
    if resolution["status"] == "ambiguous":
        warn(
            "lookup_ambiguous",
            f"{resolution['query']} matches {len(resolution['candidates'])} "
            "places; choose one (see details.resolution.candidates).",
            candidates=len(resolution["candidates"]),
        )
        return
    if document.get("reference_evidence") is None:
        why = document.get("reference_evidence_unused")
        warn(
            "lookup_no_reference_evidence",
            "No usable gVCF for this sample"
            + (f" ({why})" if why else "")
            + ": sites the VCF doesn't list are reported as not assessed, "
            "not as reference.",
        )
    age = report_age_days(document["interpretation"], params.input)
    if age is not None:
        warn(
            "lookup_report_older_than_input",
            f"The joined report is {age:.1f} days older than {params.input}; "
            "rerun 'wgsextract report' for current results.",
            days=round(age, 1),
        )


def _record_inputs(document: Mapping[str, Any]) -> None:
    result = current_result()
    result.add_input(document["input"]["path"], "input")
    evidence = document.get("reference_evidence")
    if evidence and evidence["origin"] != "input":
        result.add_input(evidence["path"], "reference_evidence")
    report = (document.get("interpretation") or {}).get("report")
    if report:
        result.add_input(report["path"], "report_json")
    for resource in document["resources"]:
        if resource.get("path"):
            result.add_input(resource["path"], str(resource["name"]))


def run_lookup(params: LookupParams) -> None:
    library = resolve_reference_library(
        reference_library_of(params.ref) if params.ref else None
    )
    document = run_query(
        Request(
            input=params.input,
            thresholds=thresholds_from(params),
            reflib=library,
            rsid=params.rsid,
            locus=params.locus,
            gene=params.gene,
            sample=params.sample,
            gvcf=params.gvcf,
            report=params.report,
            build=params.build,
            limit=params.limit,
            cursor=params.cursor,
        )
    )
    _record_inputs(document)
    current_result().set_details(document)
    site = document.get("site")
    if isinstance(site, Mapping):
        current_result().add_metrics(status=str(site["status"]))
    _warnings(document, params)
    _print(document)


__all__ = ["register", "run_lookup"]
