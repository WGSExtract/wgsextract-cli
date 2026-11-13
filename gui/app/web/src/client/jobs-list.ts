import { escapeAttribute, escapeHTML } from "./dom.js";
import { state } from "./state.js";
import { renderTerminalTabLabel } from "./terminal-tab-label.js";
import type { TerminalEntry } from "../../../shared/types.js";

/** The Jobs tab: one row per job run this session, newest first, followed by the app's own messages. */
export function renderJobsList(main: TerminalEntry | undefined): string {
    const jobs = [...state.jobHistory].reverse();
    const labels = state.labels;
    const list = jobs.length
        ? `<table class="jobs-table">
        <thead><tr>
          <th scope="col">${escapeHTML(labels.jobsColumnJobLabel ?? "Job")}</th>
          <th scope="col">${escapeHTML(labels.jobsColumnStatusLabel ?? "Status")}</th>
          <th scope="col">${escapeHTML(labels.jobsColumnStartedLabel ?? "Started")}</th>
          <th scope="col">${escapeHTML(labels.jobsColumnFileLabel ?? "File")}</th>
        </tr></thead>
        <tbody>${jobs.map(renderJobRow).join("")}</tbody>
      </table>`
        : `<p class="jobs-empty">${escapeHTML(labels.jobsEmptyLabel ?? "")}</p>`;
    const messages = main?.body
        ? `<section class="jobs-messages" aria-label="${escapeAttribute(labels.jobsMessagesLabel ?? "Messages")}">
        <h3>${escapeHTML(labels.jobsMessagesLabel ?? "Messages")}</h3>
        <pre>${escapeHTML(main.body)}</pre>
      </section>`
        : "";
    return `<div class="jobs-list">${list}${messages}</div>`;
}

/** Plain text of the Jobs tab, for its Copy button. */
export function jobsListText(main: TerminalEntry | undefined): string {
    const rows = [...state.jobHistory].reverse().map((job) => [job.title, jobStatusLabel(job), formatStartedAt(job.startedAt), job.primaryFile ?? ""].join("\t"));
    return [...rows, main?.body ?? ""].filter(Boolean).join("\n");
}

function renderJobRow(job: TerminalEntry): string {
    const started = job.startedAt == null ? "" : `<time datetime="${escapeAttribute(new Date(job.startedAt).toISOString())}">${escapeHTML(formatStartedAt(job.startedAt))}</time>`;
    const file = job.primaryFile ?? "";
    return `<tr class="jobs-row ${escapeAttribute(job.kind)}">
      <td><button type="button" class="terminal-tab-wrap jobs-tab ${escapeAttribute(job.kind)}" data-job-open="${escapeAttribute(job.id)}">${renderTerminalTabLabel(job)}</button></td>
      <td class="jobs-status">${escapeHTML(jobStatusLabel(job))}</td>
      <td class="jobs-started">${started}</td>
      <td class="jobs-file" ${file ? `title="${escapeAttribute(file)}"` : ""}>${escapeHTML(fileName(file))}</td>
    </tr>`;
}

function jobStatusLabel(job: TerminalEntry): string {
    const labels = state.labels;
    switch (job.kind) {
        case "command":
            return labels.jobsStatusRunningLabel ?? "Running";
        case "success":
            return labels.jobsStatusSucceededLabel ?? "Succeeded";
        case "warning":
            return labels.jobsStatusWarningLabel ?? "Finished with warnings";
        case "cancelled":
            return labels.jobsStatusCancelledLabel ?? "Cancelled";
        default:
            return labels.jobsStatusFailedLabel ?? "Failed";
    }
}

function formatStartedAt(startedAt: number | undefined): string {
    if (startedAt == null) {
        return "";
    }
    return new Intl.DateTimeFormat(state.localizationCode || undefined, { dateStyle: "short", timeStyle: "medium" }).format(startedAt);
}

function fileName(path: string): string {
    return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path;
}
