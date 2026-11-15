import { escapeAttribute, escapeHTML } from "./dom.js";
import type { TerminalEntry, TerminalStatus } from "../../../shared/types.js";
import { formatLabel, renderIcon } from "./model.js";
import { state } from "./state.js";
import { jobsListText, renderJobsList } from "./jobs-list.js";
import { renderResultSummary } from "./result-view.js";
import { renderTerminalTabLabel } from "./terminal-tab-label.js";
import { renderTerminalText } from "./terminal-output.js";
export { appendTerminalOutput, discardPendingTerminalOutput } from "./terminal-output.js";
/** Cancels the run streaming into a terminal tab (actions and setup) when the tab is closed. */
export const runningActionControllers = new Map<string, AbortController>();

/** Keeps a session's job list bounded; older jobs drop off the Jobs tab. */
const maxJobHistory = 200;
const maxTerminalTabs = 40;

function activeTerminalEntry(): TerminalEntry | undefined {
    const tabs = terminalTabs();
    return tabs[state.activeTerminalIndex] ?? tabs[0];
}
/**
 * The pane shows its row of job tabs, collapsed or not; collapsing hides only the output below them. While
 * the Jobs list itself is showing, its rows already hold those tabs, so the row shows only Jobs.
 */
export function renderTerminalPane() {
    const tabs = terminalTabs();
    const entry = activeTerminalEntry();
    const jobTabs = state.isTerminalVisible && entry?.kind === "main"
        ? []
        : tabs.map((tab, index) => ({ tab, index })).slice(1).reverse();
    const copyLabel = state.labels.terminalCopyTextLabel ?? "Copy terminal text";
    const textDirection = terminalTextDirection();
    // Collapsed, only the tab row shows: skip building output that can run to a million characters.
    const output = () => entry?.kind === "main"
        ? renderJobsList(entry)
        : `<pre>${renderTerminalText(entry)}</pre>${renderResultSummary(entry?.result)}`;
    return `
    <section class="terminal-panel ${state.isTerminalVisible ? "" : "collapsed"}" aria-label="${escapeHTML(state.labels.terminalCommandOutputLabel)}">
      <header class="terminal-header">
        <span class="terminal-glyph" aria-hidden="true">${renderIcon("terminal", undefined, ">_")}</span>
        <div class="terminal-tabs">
          ${tabs[0] ? renderTerminalTab(tabs[0], 0) : ""}
          <div class="terminal-job-tabs">
            ${jobTabs.map(({ tab, index }) => renderTerminalTab(tab, index)).join("")}
          </div>
        </div>
        <button type="button" class="terminal-icon-button" data-terminal-copy title="${escapeAttribute(copyLabel)}" aria-label="${escapeAttribute(copyLabel)}">
          <i class="bi bi-copy" aria-hidden="true"></i>
        </button>
        ${state.terminalCopyFeedback ? `<span class="terminal-copy-feedback" role="status">${escapeHTML(state.labels.terminalCopiedTextLabel ?? "Copied!")}</span>` : ""}
        ${renderTerminalToggle()}
      </header>
      ${state.isTerminalVisible
        ? `<div class="terminal-log" data-terminal-log-id="${escapeAttribute(entry?.id ?? "")}" dir="${escapeAttribute(textDirection)}">${output()}</div>`
        : ""}
    </section>
  `;
}
/**
 * One tab chip. Job tabs are rendered newest first into a row-reverse strip one tab high, so the newest
 * sit at the right end and the oldest wrap out of view on the left, whole; the Jobs list still lists them.
 */
function renderTerminalTab(tab: TerminalEntry, index: number): string {
    const close = tab.kind === "main"
        ? ""
        : `<button type="button" class="terminal-tab-close" data-terminal-tab-close="${index}" aria-label="${escapeAttribute(formatLabel(state.labels.terminalCloseTabLabelFormat, { title: tab.title }))}">×</button>`;
    return `
      <span class="terminal-tab-wrap ${index === state.activeTerminalIndex ? "active" : ""} ${escapeAttribute(tab.kind)}">
        <button type="button" class="terminal-tab" data-terminal-tab="${index}" ${tab.status ? `data-tooltip="${escapeAttribute(terminalStatusTooltip(tab.status))}"` : ""}>
          ${renderTerminalTabLabel(tab)}
        </button>
        ${close}
      </span>`;
}
/** The text the Copy button copies: the active tab's output, or the job list on the Jobs tab. */
export function activeTerminalCopyText(): string {
    const entry = activeTerminalEntry();
    return entry?.kind === "main" ? jobsListText(entry) : entry?.body ?? "";
}
/** Makes a job's tab the active one at the right end of the tab row, where it is always in view, reopening it if it was closed. */
export function openJob(id: string) {
    const job = state.jobHistory.find((entry) => entry.id === id);
    if (!job) {
        return;
    }
    state.terminalEntries = [...state.terminalEntries.filter((entry) => entry.id !== id), job];
    state.activeTerminalIndex = state.terminalEntries.length - 1;
}
export function terminalTextDirection() {
    return state.manifest?.terminalTextDirection === "rtl" ? "rtl" : "ltr";
}
/** Opens a terminal tab. A "command" tab is a job: it is timestamped and listed on the Jobs tab. */
export function appendTerminal(kind: string, title: unknown, body = "", command = "", details: Pick<TerminalEntry, "primaryFile"> = {}): string {
    ensureMainTerminal();
    if (kind === "config") {
        const main = state.terminalEntries[0];
        if (!main) {
            throw new Error("Main terminal was not initialized.");
        }
        main.body = [main.body, title, body].filter(Boolean).join("\n");
        state.activeTerminalIndex = 0;
        return "main";
    }
    const id = crypto.randomUUID();
    const entry: TerminalEntry = { id, kind, title: String(title ?? ""), body, command, ...details };
    if (kind === "command") {
        entry.startedAt = Date.now();
        state.jobHistory = [...state.jobHistory, entry].slice(-maxJobHistory);
    }
    const [main, ...rest] = state.terminalEntries;
    state.terminalEntries = main ? [main, ...capTabs([...rest, entry])] : [entry];
    state.activeTerminalIndex = Math.max(0, state.terminalEntries.length - 1);
    return id;
}
/**
 * Keeps at most 40 tabs besides Jobs, dropping the oldest finished ones first, then the oldest running ones
 * (a running job whose tab drops off still updates its Jobs row).
 */
function capTabs(tabs: TerminalEntry[]): TerminalEntry[] {
    const kept = [...tabs];
    while (kept.length > maxTerminalTabs - 1) {
        // Never the newest tab, which just opened.
        const finished = kept.slice(0, -1).findIndex((tab) => tab.kind !== "command");
        kept.splice(finished < 0 ? 0 : finished, 1);
    }
    return kept;
}
export function resetTerminalEntries() {
    for (const controller of runningActionControllers.values()) {
        controller.abort();
    }
    runningActionControllers.clear();
    state.terminalEntries = [];
    state.jobHistory = [];
    state.activeTerminalIndex = 0;
    ensureMainTerminal();
}
export function terminalTabs(): TerminalEntry[] {
    ensureMainTerminal();
    return state.terminalEntries;
}
export function ensureMainTerminal() {
    if (state.terminalEntries[0]?.kind === "main") {
        state.terminalEntries[0].id ??= "main";
        state.terminalEntries[0].title = state.labels.terminalMainTabTitle ?? "Jobs";
        return;
    }
    const hadEntries = state.terminalEntries.length > 0;
    state.terminalEntries.unshift({
        id: "main",
        kind: "main",
        title: state.labels.terminalMainTabTitle ?? "Jobs",
        body: "",
        command: "main",
    });
    state.activeTerminalIndex = hadEntries ? state.activeTerminalIndex + 1 : 0;
}
export function closeTerminalTab(index: number) {
    if (index <= 0 || index >= state.terminalEntries.length) {
        return;
    }
    const tab = state.terminalEntries[index];
    if (!tab) {
        return;
    }
    runningActionControllers.get(tab.id)?.abort();
    runningActionControllers.delete(tab.id);
    // Closing a running job's tab stops it; the Jobs list keeps it as cancelled.
    if (tab.kind === "command") {
        tab.kind = "cancelled";
    }
    state.terminalEntries.splice(index, 1);
    if (state.activeTerminalIndex === index) {
        state.activeTerminalIndex = Math.max(0, index - 1);
    }
    else if (state.activeTerminalIndex > index) {
        state.activeTerminalIndex -= 1;
    }
    state.activeTerminalIndex = Math.min(state.activeTerminalIndex, state.terminalEntries.length - 1);
}
/** Collapses the pane to its row of tabs (down arrow) or expands it again (up arrow). */
export function renderTerminalToggle(): string {
    const title = escapeAttribute(terminalToggleTitle());
    const icon = state.isTerminalVisible ? renderIcon("chevron.down", undefined, "⌄") : renderIcon("chevron.up", undefined, "⌃");
    return `<button type="button" class="terminal-icon-button" data-terminal-toggle aria-expanded="${state.isTerminalVisible}" title="${title}" aria-label="${title}">${icon}</button>`;
}
function terminalToggleTitle(): string {
    return state.isTerminalVisible
        ? state.labels.terminalHideOutputLabel ?? "Hide Command Output"
        : state.labels.terminalShowOutputLabel ?? "Show Command Output";
}
export function terminalExitStatus(exitCode: number | null, command: string): TerminalStatus {
    const reference = state.exitCodeReference.get(Number(exitCode));
    const severity = reference?.severity === "warning" ? "warning" : "error";
    const blurb = reference?.summary ?? state.labels.terminalNonzeroExitSummary;
    return {
        severity,
        symbol: severity === "warning" ? "▲" : "✕",
        title: reference?.title ?? formatLabel(state.labels.terminalExitCodeTitleFormat, { code: exitCode }),
        ...(blurb != null ? { blurb } : {}),
        detail: formatLabel(state.labels.terminalExitDetailFormat, { command, code: exitCode }),
    };
}
export function terminalProcessErrorStatus(command: string, message: string): TerminalStatus {
    return {
        severity: "error",
        symbol: "✕",
        ...(state.labels.terminalProcessErrorTitle != null ? { title: state.labels.terminalProcessErrorTitle } : {}),
        ...(state.labels.terminalProcessErrorSummary != null ? { blurb: state.labels.terminalProcessErrorSummary } : {}),
        detail: `${command}\n${message}`,
    };
}
export function terminalStatusTooltip(status: TerminalStatus): string {
    return `${status.title}\n${status.blurb}\n\n${status.detail}`;
}
