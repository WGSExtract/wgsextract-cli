"""HTML and Markdown presentation of the exon callability section."""

from __future__ import annotations

from collections.abc import Callable

from wgsextract_cli.core.report_documents import ReportCallabilityDocument

_HEADER = (
    "Gene",
    "Transcript",
    "Result",
    "Callability",
    "Coding exons callable",
    "Bases read",
    "Exons not callable",
)


def _notes(section: ReportCallabilityDocument) -> list[str]:
    notes = [section["note"]]
    if section.get("exon_model"):
        thresholds = section.get("thresholds", {})
        depth, gq = thresholds.get("min_depth"), thresholds.get("min_gq")
        read = "read means callable reference or a call"
        if isinstance(depth, (int, float)) and isinstance(gq, (int, float)):
            read += f", at depth {depth:g}+ (and GQ {gq:g}+ where the gVCF has GQ)"
        notes.append(
            f"Exon model: {section['exon_model']}. Assessed: {section.get('scope')}; "
            f"{read}, outside known short-read limitations."
        )
    if classes := section.get("unassessed_variant_classes"):
        notes.append("Not assessed even where callable: " + " ".join(classes.values()))
    return notes


def _files(section: ReportCallabilityDocument) -> list[tuple[str, str]]:
    return [
        (label, file)
        for label, file in (
            ("Every exon", section.get("all_exons")),
            ("Reference evidence", section.get("evidence")),
        )
        if file
    ]


def _rows(
    section: ReportCallabilityDocument, escape: Callable[[object], str]
) -> list[tuple[list[str], str]]:
    return [
        (
            [
                escape(value)
                for value in (
                    gene["gene"],
                    gene["transcript"] or "",
                    gene["result"].replace("_", " "),
                    gene["state"].replace("_", " "),
                    f"{gene['exons_callable']}/{gene['coding_exons']}",
                    f"{gene['readable_bases']:,}/{gene['bases']:,}",
                    gene["exons_not_callable"],
                )
            ],
            escape(gene["result_text"]),
        )
        for gene in section.get("genes", [])
    ]


def callability_html(
    section: ReportCallabilityDocument,
    escape: Callable[[object], str],
    link: Callable[[str], str],
) -> list[str]:
    out = [f'<p class="muted">{escape(line)}</p>' for line in _notes(section)]
    out.extend(f'<p class="muted">{label}: {link(file)}</p>' for label, file in _files(section))  # fmt: skip
    if rows := _rows(section, escape):
        out.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in _HEADER) + "</tr>")
        for cells, explanation in rows:
            cells[2] = f'<b>{cells[2]}</b><br><span class="muted">{explanation}</span>'
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        out.append("</table>")
    return out


def callability_markdown(
    section: ReportCallabilityDocument,
    escape: Callable[[object], str],
    link: Callable[[str], str],
) -> list[str]:
    out: list[str] = []
    for line in _notes(section):
        out += ["", f"_{escape(line)}_"]
    out.append("")
    out.extend(f"{label}: {link(file)}" for label, file in _files(section))
    if rows := _rows(section, escape):
        out += ["", "| " + " | ".join(_HEADER) + " |", "|" + "---|" * len(_HEADER)]
        for cells, explanation in rows:
            cells[2] = f"**{cells[2]}**: {explanation}"
            out.append("| " + " | ".join(cells) + " |")
    return out
