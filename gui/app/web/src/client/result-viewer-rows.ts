import type { ResultDocument } from "../../../shared/result-document.js";
import { viewerRowProblem, viewerRowTables, type ViewerRow, type ViewerRowKind, type ViewerRowTable } from "../../../shared/viewer-rows.js";
import { api } from "./api.js";
import { escapeAttribute, escapeHTML } from "./dom.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";

type TrackStatus = { ok: true } | { problem: string; fileName?: string } | "checking";

/** Long tables stay readable and cheap to re-render; the run's output files keep every row. */
const maximumRows = 200;
const columns: Record<ViewerRowKind, string[]> = {
    findings: ["rowsSection", "rowsFinding", "rowsResult", "rowsVariant"],
    mtVariants: ["rowsVariant", "rowsPosition"],
};
// Checked once per result document: a new run of the step (say, after indexing) checks again.
const checks = new WeakMap<ResultDocument, Map<string, TrackStatus>>();

/** A result's findings and haplogroup variants, each row with its own genome viewer link. */
export function renderViewerRowTables(document: ResultDocument): string {
    if (state.manifest?.id !== "wgs-extract") {
        return "";
    }
    return viewerRowTables(document).map((table) => renderTable(table, table.track ? trackStatus(document, table.track) : undefined)).join("");
}

function renderTable(table: ViewerRowTable, status: TrackStatus | undefined): string {
    const trackReason = status === undefined ? text("rowNoTrack") : status === "checking" ? text("rowTrackChecking", table.track) : "problem" in status ? text(status.problem, status.fileName ?? table.track) : "";
    const shown = table.rows.slice(0, maximumRows);
    return `
      <h4>${escapeHTML(text(table.kind === "findings" ? "rowsFindings" : "rowsMtVariants"))} (${table.rows.length})</h4>
      <div class="result-viewer-rows">
        ${trackReason ? `<p class="result-viewer-rows-note">${escapeHTML(trackReason)}</p>` : ""}
        <table>
          <thead><tr>${columns[table.kind].map((key) => `<th scope="col">${escapeHTML(text(key))}</th>`).join("")}<th scope="col"><span class="visually-hidden">${escapeHTML(text("showInViewer"))}</span></th></tr></thead>
          <tbody>${shown.map((row) => renderRow(table, row, trackReason)).join("")}</tbody>
        </table>
        ${table.rows.length > shown.length ? `<p class="result-viewer-rows-note">${escapeHTML(text("rowsTruncated").replace("%{count}", String(shown.length)).replace("%{total}", String(table.rows.length)))}</p>` : ""}
      </div>`;
}

function renderRow(table: ViewerRowTable, row: ViewerRow, trackReason: string): string {
    const rowProblem = viewerRowProblem(table, row);
    const reason = rowProblem ? text(rowProblem) : trackReason;
    const label = escapeHTML(text("showInViewer"));
    const link = reason
        ? `<button type="button" class="action-button secondary compact" disabled title="${escapeAttribute(reason)}">${label}</button>${rowProblem ? `<span class="result-viewer-row-reason">${escapeHTML(reason)}</span>` : ""}`
        : `<button type="button" class="action-button secondary compact" data-result-viewer="${escapeAttribute(table.track)}" data-viewer-locus="${escapeAttribute(row.locus)}"${row.build ? ` data-viewer-build="${escapeAttribute(row.build)}"` : ""}>${label}</button>`;
    return `<tr>${row.cells.map((cell) => `<td>${escapeHTML(cell)}</td>`).join("")}<td>${link}</td></tr>`;
}

function trackStatus(document: ResultDocument, track: string): TrackStatus {
    let statuses = checks.get(document);
    if (!statuses) {
        statuses = new Map();
        checks.set(document, statuses);
    }
    const known = statuses.get(track);
    if (known) {
        return known;
    }
    const pending = statuses;
    pending.set(track, "checking");
    void api<{ tracks: Record<string, TrackStatus> }>("/api/viewer/check", { body: { paths: [track] } })
        .then((result) => result.tracks[track] ?? { problem: "libraryUnavailable" })
        .catch((): TrackStatus => ({ problem: "libraryUnavailable" }))
        .then((status) => {
            pending.set(track, status);
            scheduleRender();
        });
    return "checking";
}

function text(key: string, file = ""): string {
    return (state.labels.viewerStrings?.[key] ?? key).replace("%{file}", file.split(/[\\/]/).at(-1) ?? file);
}
