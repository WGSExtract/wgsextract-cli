"""Offline HTML/Markdown presentation and evidence links for personal reports."""

from __future__ import annotations

import html
import os
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote

from wgsextract_cli.core.report_documents import (
    ReportAcmgDocument,
    ReportAncestryDocument,
    ReportAttemptDocument,
    ReportClinvarDocument,
    ReportDocument,
    ReportFactDocument,
    ReportFindingDocument,
    ReportLinkDocument,
    ReportMedicationReviewDocument,
    ReportMetricDocument,
    ReportMicroarrayDocument,
    ReportMissingDocument,
    ReportMitochondrialDocument,
    ReportOverviewDocument,
    ReportSectionsDocument,
    ReportSourceDocument,
    ReportSvDocument,
    ReportWarningDocument,
    section_items,
)

RenderedSection = (
    ReportOverviewDocument
    | ReportClinvarDocument
    | ReportAcmgDocument
    | ReportAncestryDocument
    | ReportSvDocument
    | ReportMicroarrayDocument
    | ReportMedicationReviewDocument
    | ReportMitochondrialDocument
)

NOT_DIAGNOSIS = "Technical result, not diagnosis."
# The disclaimer of findings from automated ACMG classes (`vcf acmg`).
RESEARCH_ONLY = "Research use only; not a diagnosis."
# Section warning: a newer attempt failed; the section shows an older result.
NEWER_ATTEMPT_FAILED = "newer_attempt_failed"


def _number(value: object, unit: str = "") -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    text = f"{value:,.0f}" if abs(value) >= 100 else f"{value:.3g}"
    return f"{text}{unit}"


_CSS = """
body{font:13px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;margin:12px 16px;color:#1b1f24;max-width:1200px}
h1{font-size:18px;margin:0 0 4px}h2{font-size:15px;margin:14px 0 4px;border-bottom:1px solid #d0d7de}
p{margin:3px 0}.muted,.nd{color:#57606a}.nd{font-size:11px;white-space:nowrap}
.disclaimer{background:#fff8c5;border:1px solid #d4a72c;padding:5px 8px;margin:6px 0}
table{border-collapse:collapse;margin:3px 0}
th,td{border:1px solid #d0d7de;padding:2px 5px;text-align:left;vertical-align:top}
th{background:#f6f8fa;font-weight:600}td.n{text-align:right;white-space:nowrap}
.conf{display:inline-block;padding:0 5px;border-radius:3px;font-size:11px;font-weight:600}
.high{background:#dafbe1;color:#116329}.moderate{background:#fff8c5;color:#7d4e00}
.low{background:#ffebe9;color:#a40e26}.not_assessed{background:#eaeef2;color:#424a53}
.status{font-size:11px;font-weight:600;padding:0 5px;border-radius:3px;background:#eaeef2}
.status.ok{background:#dafbe1}.status.failed{background:#ffebe9}
.stale{background:#ffebe9;border:1px solid #cf222e;padding:3px 8px}
code{font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#f6f8fa;padding:0 3px}
ul{margin:2px 0;padding-left:18px}
@media print{body{margin:0}a{color:inherit}}
""".strip()


def _href(file: str | None, base: str) -> str | None:
    if not file:
        return None
    for flavour in (PurePosixPath, PureWindowsPath):
        if flavour(file).is_absolute():  # evidence on another drive
            return flavour(file).as_uri()
    if base.startswith("file:"):
        return f"{base.rstrip('/')}/{quote(file)}"
    return quote(Path(os.path.normpath(Path(base, file))).as_posix())


def _h(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _link(text: object, href: str | None) -> str:
    return f'<a href="{_h(href)}">{_h(text)}</a>' if href else _h(text)


def _source_links(
    source: ReportSourceDocument, link: Callable[[str, str | None], str]
) -> str:
    """A section source as 'command (status, finished)', linked to its
    manifest, then the newer attempt that failed or was cancelled after it."""

    def label(run: ReportAttemptDocument) -> str:
        when = f", {run['finished']}" if run["finished"] else ""
        return link(f"{run['command']} ({run['status']}{when})", run["manifest"])

    newer = source.get("latest_attempt")
    return label(source) + (f"; newest attempt: {label(newer)}" if newer else "")


def _sources_text(
    sources: Sequence[ReportSourceDocument],
    link: Callable[[str, str | None], str],
) -> str:
    return ", ".join(_source_links(source, link) for source in sources)


def _warning_rows(
    warnings: Sequence[ReportWarningDocument], *, newer: bool
) -> list[ReportWarningDocument]:
    return [
        warning
        for warning in warnings
        if (warning["code"] == NEWER_ATTEMPT_FAILED) == newer
    ]


def _missing_lines(
    missing: Sequence[ReportMissingDocument], *, markdown: bool
) -> list[str]:
    if markdown:
        return [
            f"- {_md(item['what'])}: {_md_code(item['command'])}" for item in missing
        ]
    return [
        f"<li>{_h(item['what'])}: <code>{_h(item['command'])}</code></li>"
        for item in missing
    ]


def _finding_links(
    links: Sequence[ReportLinkDocument], link: Callable[[str, str | None], str]
) -> str:
    return " ".join(link(item["label"], item["url"]) for item in links)


def render_html(report: ReportDocument, base: str = ".") -> str:
    """A self-contained HTML page: inline CSS, no scripts, fonts or remote
    resources, so it renders offline. ``base`` is the sample folder relative
    to the page, for the evidence links."""
    sample = report["sample"]
    out = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="referrer" content="no-referrer">',
        f"<title>{_h(sample['name'])}: WGS Extract report</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{_h(sample['name'])}: WGS Extract report</h1>",
        f'<p class="muted">Generated {_h(report["generated"])} by wgsextract '
        f"{_h(report['wgsextract_version'])} from the results in "
        f"<code>{_h(sample['folder'])}</code>. Evidence links are relative to "
        "that folder.</p>",
        f'<p class="disclaimer"><b>{_h(NOT_DIAGNOSIS)}</b> '
        f"{_h(report['disclaimer'])}</p>",
    ]
    for name, section in section_items(report["sections"]):
        out.append(
            f'<h2 id="{_h(name)}">{_h(section["title"])} '
            f'<span class="status {_h(section["status"])}">'
            f"{_h(section['status'].replace('_', ' '))}</span></h2>"
        )
        if disclaimer := section.get("disclaimer"):
            out.append(f'<p class="disclaimer"><b>{_h(disclaimer)}</b></p>')
        if section.get("summary"):
            out.append(f"<p>{_h(section['summary'])}</p>")
        if section.get("error"):
            out.append(f"<p><b>Failed:</b> {_h(section['error'])}</p>")
        out.extend(
            f'<p class="stale"><b>Newer attempt failed:</b> {_h(w["message"])}</p>'
            for w in _warning_rows(section["warnings"], newer=True)
        )
        out.extend(_html_section_body(name, report["sections"], base))
        if section["findings"]:
            out.append(
                _html_findings(section["findings"], report["confidence_reasons"], base)
            )
        if section["missing"]:
            out.append(
                '<p class="muted">Not run yet (run in the sample folder):</p><ul>'
            )
            out.extend(_missing_lines(section["missing"], markdown=False))
            out.append("</ul>")
        warnings = _warning_rows(section["warnings"], newer=False)
        if warnings:
            out.append("<ul>")
            out.extend(f'<li class="muted">⚠ {_h(w["message"])}</li>' for w in warnings)
            out.append("</ul>")
        if section["sources"]:
            links = _sources_text(
                section["sources"], lambda text, f: _link(text, _href(f, base))
            )
            out.append(f'<p class="muted">Sources: {links}</p>')
    out.append(
        f'<p class="muted">Confidence labels: {_h(report["guide"])} and '
        "docs/variant-confidence.md in WGS Extract.</p></body></html>"
    )
    return "\n".join(out) + "\n"


def _html_section_body(
    name: str, sections: ReportSectionsDocument, base: str
) -> list[str]:
    out: list[str] = []
    section: RenderedSection
    if name == "overview":
        section = sections["overview"]
        out.extend(_html_overview(section["facts"], section["qc"]))
    elif name == "clinvar":
        section = sections["clinvar"]
        gl = section["gene_list"]
        source = [_h(gl["citation"])] if gl["citation"] else []
        source += [_link("Table 1", gl["url"])] if gl["url"] else []
        out.append(
            f'<p class="muted">Gene list: {_h(gl["name"])} ({gl["genes"]} genes'
            + (f"; {', '.join(source)}" if source else "")
            + ").</p>"
        )
        if section.get("all_calls"):
            out.append(
                f'<p class="muted">Every carried ClinVar P/LP call: '
                f"{_link(section['all_calls'], _href(section['all_calls'], base))}</p>"
            )
        if section.get("all_conflicts"):
            out.append(
                f"<p>Conflicting assertions for review (not consensus P/LP): {_link(section['all_conflicts'], _href(section['all_conflicts'], base))}</p>"
            )
        if section.get("genes"):
            out.append("<table><tr><th>Gene</th><th>Inheritance</th>"
                       "<th>Heterozygous</th><th>Homozygous</th><th>Hemizygous</th>"
                       "<th>Partly called</th><th>Confidence</th>"
                       "<th>Recessive gene</th></tr>")  # fmt: skip
            for g in section["genes"]:
                status = (g["status"] or "").replace("_", " ")
                out.append(
                    f"<tr><td>{_h(g['gene'])}</td><td>{_h(g['inheritance'] or '')}</td>"
                    f'<td class="n">{g["heterozygous"]}</td>'
                    f'<td class="n">{g["homozygous"]}</td>'
                    f'<td class="n">{g["hemizygous"]}</td>'
                    f'<td class="n">{g["partial"]}</td>'
                    f"<td>{_h(g['confidence'])}</td><td>{_h(status)}"
                    + (
                        f'<br><span class="muted">{_h(g["status_text"])}</span>'
                        if status
                        else ""
                    )
                    + "</td></tr>"
                )
            out.append("</table>")
    elif name == "mitochondrial" and sections["mitochondrial"].get("sites"):
        section = sections["mitochondrial"]
        out.append(f'<p class="disclaimer"><b>{_h(section.get("advisory"))}</b></p>')
        files = _mito_files(section)
        out.extend(f'<p class="muted">{_h(line)}</p>' for line in _mito_notes(section))
        if files:
            out.append(
                '<p class="muted">Evidence: '
                + ", ".join(_link(f, _href(f, base)) for f in files)
                + "</p>"
            )
        head, rows = _mito_table(section)
        out.append("<table><tr>" + "".join(f"<th>{_h(c)}</th>" for c in head) + "</tr>")
        out.extend(
            "<tr>" + "".join(f"<td>{_h(c)}</td>" for c in row) + "</tr>" for row in rows
        )
        out.append("</table>")
    elif name == "acmg_research":
        section = sections["acmg_research"]
        out.extend(f'<p class="muted">{_h(line)}</p>' for line in _acmg_notes(section))
        if section.get("all_calls"):
            out.append(
                f'<p class="muted">Every classified allele: '
                f"{_link(section['all_calls'], _href(section['all_calls'], base))}</p>"
            )
    elif name == "medication_review":
        section = sections["medication_review"]
        out.extend(
            f'<p class="muted">{_h(line)}</p>' for line in _review_notes(section)
        )
    elif name == "ancestry" and sections["ancestry"]["components"]:
        section = sections["ancestry"]
        out.append(f'<p class="muted">{_h(section["note"])}</p>')
        for k in sorted({c["k"] for c in section["components"]}):
            out.append(
                f"<h3>{'Continental' if k == 7 else 'Sub-continental'} (K={k})</h3>"
                "<table><tr><th>Component</th><th>Share</th><th>95% interval</th>"
                "<th></th><th>Most common in</th></tr>"
            )
            for c in section["components"]:
                if c["k"] != k or c["upper"] < 0.005:
                    continue
                width = round(100 * c["proportion"])
                out.append(
                    f"<tr><td>{_h(c['name'])}</td>"
                    f'<td class="n">{c["proportion"]:.1%}</td>'
                    f'<td class="n">{c["lower"]:.1%}-{c["upper"]:.1%}</td>'
                    f'<td><div style="background:#4a7;height:0.8em;width:{width}px">'
                    f"</div></td><td>{_h(c['reference_populations'])}</td></tr>"
                )
            out.append("</table>")
    elif name == "structural_variants" and sections["structural_variants"].get(
        "all_svs"
    ):
        section = sections["structural_variants"]
        out.append(
            f'<p class="muted">Every SV and its frequency: '
            f"{_link(section['all_svs'], _href(section['all_svs'], base))}</p>"
        )
    elif name == "microarray" and sections["microarray"]["files"]:
        section = sections["microarray"]
        out.append("<table><tr><th>File</th><th>Format</th><th>Sites</th>"
                   "<th>Called</th><th>No-call rate</th></tr>")  # fmt: skip
        for f in section["files"]:
            rate = f["no_call_rate"]
            out.append(
                f"<tr><td>{_link(f['file'], _href(f['file'], base))}"
                f"{'' if f['exists'] else ' (missing)'}</td><td>{_h(f['format'])}</td>"
                f'<td class="n">{_h(_number(f["sites"]) if f["sites"] is not None else "")}</td>'
                f'<td class="n">{_h(_number(f["called"]) if f["called"] is not None else "")}</td>'
                f'<td class="n">{_h(f"{rate:.1%}" if isinstance(rate, (int, float)) else "")}</td></tr>'
            )
        out.append("</table>")
    return out


def _mito_files(section: ReportMitochondrialDocument) -> list[str]:
    technical = section.get("technical")
    if not technical:
        return []
    return [f for f in (technical["calls"], technical["site_evidence"]) if f]


def _mito_notes(section: ReportMitochondrialDocument) -> list[str]:
    """How the mtDNA was called, the thresholds, and the reviewed-variant
    table's sources."""
    notes = []
    if technical := section.get("technical"):
        share = technical["min_depth_fraction"]
        caller = f"{technical['caller']} {technical['version'] or ''}".strip()
        notes.append(
            f"Called by {caller} ({technical['settings']}) against "
            f"{technical['mt_reference'] or 'an unconfirmed mtDNA reference'}"
            + (
                f" (contig {technical['contig']}, MD5 {technical['reference_md5']})"
                if technical["reference_md5"]
                else ""
            )
            + (
                f"; {share:.1%} of positions have 100 or more reads"
                if share is not None
                else ""
            )
            + (
                f"; haplocheck contamination: {technical['contamination']}"
                if technical["contamination"]
                else ""
            )
            + "."
        )
    if t := section.get("thresholds"):
        notes.append(
            f"Homoplasmic from {t['homoplasmic']:.0%}; heteroplasmic from "
            f"{t['supported_heteroplasmy']:.0%} (gnomAD's reporting floor; "
            "misaligned nuclear mtDNA copies can inflate low-level calls). "
            f"Lower calls remain weak evidence, as do uncalled signals from "
            f"{t['weak_evidence']:.0%} of reads on a strand. Not detected "
            f"needs {t['min_depth']} or more reads without the ALT."
        )
    if r := section.get("resource"):
        notes.append(
            f"Reviewed variants: {r['name']}; ClinVar release {r['clinvar_release']} "
            f"(MD5 {r['clinvar_md5']}; {r['clinvar_selection']}); "
            f"{r['cpic_citation']}. {r['heteroplasmy_basis']}"
        )
    return notes


def _mito_table(
    section: ReportMitochondrialDocument,
) -> tuple[list[str], list[list[str]]]:
    head = ["Group", "Variant", "State", "Level", "Reads (fwd/rev)",
            "Confidence", "ClinVar", "CPIC"]  # fmt: skip
    rows = []
    for site in section.get("sites", []):
        level = site["level"]
        reasons = f" ({', '.join(site['reasons'])})" if site["reasons"] else ""
        reads = (
            f"{site['depth']} ({site['depth_fwd']}/{site['depth_rev']})"
            if site["depth_fwd"] is not None
            else str(site["depth"] if site["depth"] is not None else "")
        )
        stars = site["clinvar_stars"]
        rows.append(
            [
                site["group"],
                f"{site['gene']} {site['hgvs']}",
                site["state"].replace("_", " "),
                f"{level:.1%}" if level is not None else "",
                reads,
                site["confidence"].replace("_", " ") + reasons,
                f"{site['clinvar_classification']} (VCV {site['clinvar_id']}, "
                f"{site['clinvar_review_status']}"
                + (f", {stars} stars" if stars is not None else "")
                + ")",
                f"{site['cpic_phenotype']} ({site['cpic_evidence']} evidence)"
                if site["cpic_phenotype"]
                else "",
            ]
        )
    return head, rows


def _review_notes(section: ReportMedicationReviewDocument) -> list[str]:
    """The medications reviewed and the CPIC release and rules behind it."""
    cpic, rules = section["cpic"], section["rules"]
    medications = ", ".join(
        m["name"] + (f" ({m['population']})" if m["population"] else "")
        for m in section["context"]["medications"]
    )
    return [
        f"Medications listed: {medications}.",
        f"Source: CPIC® database {cpic['release']} ({cpic['published']}, "
        f"{cpic['license']}); {cpic['attribution']}",
        f"Medication-effect rules {rules['version']}, checked against CPIC "
        f"{rules['checked_against_cpic']}.",
    ]


def _acmg_notes(section: ReportAcmgDocument) -> list[str]:
    """The gene list, classifier and criteria never evaluated behind the
    automated ACMG classes."""
    gl = section["gene_list"]
    notes = [f"Gene list: {gl['name']} ({gl['genes']} genes)."]
    if section.get("tool"):
        notes.append(f"Classified by {section['tool']}.")
    if section.get("criteria_not_evaluated"):
        notes.append(
            "Criteria never evaluated: "
            + ", ".join(section["criteria_not_evaluated"])
            + "."
        )
    return notes


def _call_text(v: Mapping[str, object]) -> str:
    """'REF>ALT GT zygosity, DP n, GQ n', leaving out what the call lacks
    (an SV has no REF, depth or GQ)."""
    if not v:
        return ""
    allele = f"{v['ref']}>{v.get('alt')}" if v.get("ref") else str(v.get("alt") or "")
    head = " ".join(str(x) for x in (allele, v.get("genotype"), v.get("zygosity")) if x)
    tail = [
        f"{label} {v[key]}"
        for label, key in (
            ("DP", "depth"),
            ("GQ", "genotype_quality"),
            ("PS", "phase_set"),
        )
        if v.get(key) is not None
    ]
    return ", ".join([head, *tail])


def _html_findings(
    items: Sequence[ReportFindingDocument], reasons: Mapping[str, str], base: str
) -> str:
    out = ["<table><tr><th>Finding</th><th>Result</th><th>Detail</th>"
           "<th>Call</th><th>Sequencing confidence</th><th>Evidence</th><th></th></tr>"]  # fmt: skip
    for f in items:
        v: Mapping[str, object] = f["variant"] or {}
        call = _h(_call_text(v))
        why = "; ".join(reasons.get(r, r) for r in f["confidence_reasons"])
        detail = _h(f["detail"]) + (f"<br><i>{_h(f['note'])}</i>" if f["note"] else "")
        links = _finding_links(f["links"], _link)
        evidence = f["evidence"]
        out.append(
            f"<tr><td>{_h(f['label'])}</td><td>{_h(f['value'])}"
            f"{'<br>' + links if links else ''}</td><td>{detail}</td><td>{call}</td>"
            f'<td><span class="conf {_h(f["confidence"])}" title="{_h(why)}">'
            f"{_h(f['confidence'].replace('_', ' '))}</span>"
            f"{'<br><span class=muted>' + _h(why) + '</span>' if why else ''}</td>"
            f"<td>{_link(evidence['file'] or 'manifest', _href(evidence['file'] or evidence['manifest'], base))}"
            f"<br><code>{_h(evidence['region'])}</code></td>"
            f'<td class="nd">{_h(f["disclaimer"])}</td></tr>'
        )
    out.append("</table>")
    return "".join(out)


def _html_overview(
    facts: Sequence[ReportFactDocument], qc: Sequence[ReportMetricDocument]
) -> list[str]:
    out: list[str] = []
    if facts:
        out.append("<table><tr><th>Fact</th><th>Value</th><th>Method</th>"
                   "<th>Confidence</th><th>From</th></tr>")  # fmt: skip
        for f in facts:
            method = "your override" if f["source"] == "override" else f["method"]
            out.append(
                f"<tr><td>{_h(f['label'])}</td><td>{_h(f['text'])}</td>"
                f"<td>{_h(method)}</td><td>{_h(f['confidence'] or 'not recorded')}</td>"
                f"<td>{_h(f['file'])}</td></tr>"
            )
        out.append("</table>")
    if qc:
        cells = "".join(
            f"<tr><td>{_h(q['label'])}</td><td>{_h(q['text'])}</td></tr>" for q in qc
        )
        out.append(f"<table><tr><th>QC metric</th><th>Value</th></tr>{cells}</table>")
    return out


# Kept as is in Markdown link destinations; anything else is percent-encoded.
_URL_SAFE = ":/?#[]@!$&'()*+,;=%~"
_MD_ESCAPES = str.maketrans(
    {
        **{c: f"\\{c}" for c in "\\`*_[]|"},
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\r": " ",
        "\n": " ",
    }
)


def _md(value: object) -> str:
    """Text for Markdown (and a table cell): its markup, links and any HTML
    show as typed rather than being rendered."""
    return ("" if value is None else str(value)).translate(_MD_ESCAPES)


def _md_code(value: object, *, cell: bool = False) -> str:
    """An inline code span (which shows its text as typed) that a backtick or
    line break, or in a table ``cell`` a pipe, in ``value`` cannot end."""
    text = " ".join(("" if value is None else str(value)).split())
    longest = max((len(run) for run in re.findall("`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    if cell:
        text = text.replace("|", "\\|")
    return f"{fence}{pad}{text}{pad}{fence}"


def _md_link(text: object, href: str | None) -> str:
    if not href:
        return _md(text)
    return f"[{_md(text)}](<{quote(href, safe=_URL_SAFE)}>)"


def _markdown_overview(
    facts: Sequence[ReportFactDocument], qc: Sequence[ReportMetricDocument]
) -> list[str]:
    out: list[str] = []
    if facts:
        out += [
            "",
            "| Fact | Value | Method | Confidence | From |",
            "|---|---|---|---|---|",
        ]
        for f in facts:
            method = "your override" if f["source"] == "override" else f["method"]
            out.append(
                f"| {_md(f['label'])} | {_md(f['text'])} | {_md(method)} | "
                f"{_md(f['confidence'] or 'not recorded')} | {_md(f['file'])} |"
            )
    if qc:
        out += ["", "| QC metric | Value |", "|---|---|"]
        out += [f"| {_md(q['label'])} | {_md(q['text'])} |" for q in qc]
    return out


def _markdown_findings(
    items: Sequence[ReportFindingDocument], reasons: Mapping[str, str], base: str
) -> list[str]:
    out = [
        "",
        "| Finding | Result | Detail | Call | Confidence | Evidence | |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in items:
        v: Mapping[str, object] = f["variant"] or {}
        call = _call_text(v)
        why = "; ".join(reasons.get(r, r) for r in f["confidence_reasons"])
        links = _finding_links(f["links"], _md_link)
        detail = " — ".join(x for x in (f["detail"], f["note"]) if x)
        evidence = f["evidence"]
        target = evidence["file"] or evidence["manifest"]
        out.append(
            f"| {_md(f['label'])} | {_md(f['value'])} {links} | {_md(detail)} | "
            f"{_md(call)} | **{_md(f['confidence'])}**"
            f"{' (' + _md(why) + ')' if why else ''} | "
            f"{_md_link(evidence['file'] or 'manifest', _href(target, base))} "
            f"{_md_code(evidence['region'], cell=True)} | {_md(f['disclaimer'])} |"
        )
    return out


def render_markdown(report: ReportDocument, base: str = ".") -> str:
    """The report as Markdown, with the same sections, findings and links."""
    sample = report["sample"]
    reasons = report["confidence_reasons"]
    out = [
        f"# {_md(sample['name'])}: WGS Extract report",
        "",
        f"Generated {_md(report['generated'])} by wgsextract "
        f"{_md(report['wgsextract_version'])} from the results in "
        f"{_md_code(sample['folder'])}. Evidence links are relative to that folder.",
        "",
        f"> **{_md(NOT_DIAGNOSIS)}** {_md(report['disclaimer'])}",
    ]
    section: RenderedSection
    sections = report["sections"]
    for name, common in section_items(sections):
        status = common["status"].replace("_", " ")
        out += ["", f"## {_md(common['title'])} ({_md(status)})"]
        if disclaimer := common.get("disclaimer"):
            out += ["", f"> **{_md(disclaimer)}**"]
        if common.get("summary"):
            out += ["", _md(common["summary"])]
        if common.get("error"):
            out += ["", f"**Failed:** {_md(common['error'])}"]
        for w in _warning_rows(common["warnings"], newer=True):
            out += ["", f"**Newer attempt failed:** {_md(w['message'])}"]
        if name == "overview":
            section = sections["overview"]
            out += _markdown_overview(section["facts"], section["qc"])
        if name == "clinvar":
            section = sections["clinvar"]
            gl = section["gene_list"]
            source = [_md(gl["citation"])] if gl["citation"] else []
            source += [_md_link("Table 1", gl["url"])] if gl["url"] else []
            out += [
                "",
                f"Gene list: {_md(gl['name'])} ({_md(gl['genes'])} genes"
                + (f"; {', '.join(source)}" if source else "")
                + ").",
            ]
            if section.get("all_calls"):
                href = _href(section["all_calls"], base)
                out.append(
                    f"Every carried ClinVar P/LP call: {_md_link(section['all_calls'], href)}"
                )
            if section.get("all_conflicts"):
                out.append(
                    f"Conflicting assertions for review (not consensus P/LP): {_md_link(section['all_conflicts'], _href(section['all_conflicts'], base))}"
                )
            if section.get("genes"):
                out += [
                    "",
                    "| Gene | Inheritance | Heterozygous | Homozygous | Hemizygous "
                    "| Partly called | Confidence | Recessive gene |",
                    "|---|---|---|---|---|---|---|---|",
                ]
                for g in section["genes"]:
                    status = (g["status"] or "").replace("_", " ")
                    out.append(
                        f"| {_md(g['gene'])} | {_md(g['inheritance'])} | "
                        f"{g['heterozygous']} | {g['homozygous']} | {g['hemizygous']} | "
                        f"{g['partial']} | {_md(g['confidence'])} | "
                        + (
                            f"**{_md(status)}**: {_md(g['status_text'])}"
                            if status
                            else ""
                        )
                        + " |"
                    )
        if name == "mitochondrial" and sections["mitochondrial"].get("sites"):
            section = sections["mitochondrial"]
            out += ["", f"> **{_md(section.get('advisory'))}**", ""]
            out += [_md(line) for line in _mito_notes(section)]
            if files := _mito_files(section):
                out.append(
                    "Evidence: " + ", ".join(_md_link(f, _href(f, base)) for f in files)
                )
            head, rows = _mito_table(section)
            out += ["", "| " + " | ".join(head) + " |", "|---" * len(head) + "|"]
            out += ["| " + " | ".join(_md(c) for c in row) + " |" for row in rows]
        if name == "acmg_research":
            section = sections["acmg_research"]
            out += ["", *(_md(line) for line in _acmg_notes(section))]
            if section.get("all_calls"):
                href = _href(section["all_calls"], base)
                out.append(
                    f"Every classified allele: {_md_link(section['all_calls'], href)}"
                )
        if name == "medication_review":
            section = sections["medication_review"]
            out += ["", *(_md(line) for line in _review_notes(section))]
        if name == "ancestry" and sections["ancestry"]["components"]:
            section = sections["ancestry"]
            out += ["", f"_{_md(section['note'])}_"]
            for k in sorted({c["k"] for c in section["components"]}):
                out += [
                    "",
                    f"K={k}:",
                    "",
                    "| Component | Share | 95% interval | Most common in |",
                    "|---|---|---|---|",
                ]
                out += [
                    f"| {_md(c['name'])} | {c['proportion']:.1%} | "
                    f"{c['lower']:.1%}-{c['upper']:.1%} | "
                    f"{_md(c['reference_populations'])} |"
                    for c in section["components"]
                    if c["k"] == k and c["upper"] >= 0.005
                ]
        if name == "structural_variants" and sections["structural_variants"].get(
            "all_svs"
        ):
            section = sections["structural_variants"]
            href = _href(section["all_svs"], base)
            out.append(
                f"Every SV and its frequency: {_md_link(section['all_svs'], href)}"
            )
        if name == "microarray" and sections["microarray"]["files"]:
            section = sections["microarray"]
            out += [
                "",
                "| File | Format | Sites | Called | No-call rate |",
                "|---|---|---|---|---|",
            ]
            for f in section["files"]:
                rate = f["no_call_rate"]
                out.append(
                    f"| {_md_link(f['file'], _href(f['file'], base))} | "
                    f"{_md(f['format'])} | {_md(f['sites'])} | {_md(f['called'])} | "
                    f"{f'{rate:.1%}' if isinstance(rate, (int, float)) else ''} |"
                )
        if common["findings"]:
            out += _markdown_findings(common["findings"], reasons, base)
        if common["missing"]:
            out += ["", "Not run yet (run in the sample folder):", ""]
            out += _missing_lines(common["missing"], markdown=True)
        warnings = _warning_rows(common["warnings"], newer=False)
        if warnings:
            out.append("")
            out += [f"- ⚠ {_md(w['message'])}" for w in warnings]
        if common["sources"]:
            links = _sources_text(
                common["sources"], lambda text, f: _md_link(text, _href(f, base))
            )
            out += ["", f"Sources: {links}"]
    out += [
        "",
        f"Confidence labels: {_md(report['guide'])} and docs/variant-confidence.md.",
    ]
    return "\n".join(out) + "\n"


def link_base(folder: Path, outdir: Path) -> str:
    """The sample folder relative to the report's folder, for its links."""
    try:
        return Path(
            os.path.relpath(os.path.abspath(folder), os.path.abspath(outdir))
        ).as_posix()
    except ValueError:
        return Path(os.path.abspath(folder)).as_uri()
