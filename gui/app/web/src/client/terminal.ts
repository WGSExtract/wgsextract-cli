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

function activeTerminalEntry(): TerminalEntry | undefined {
    const tabs = terminalTabs();
    return tabs[state.activeTerminalIndex] ?? tabs[0];
}
/** The pane always shows its row of job tabs; collapsing it hides only the output below them. */
export function renderTerminalPane() {
    const tabs = terminalTabs();
    const entry = activeTerminalEntry();
    const copyLabel = state.labels.terminalCopyTextLabel ?? "Copy terminal text";
    const textDirection = terminalTextDirection();
    const output = entry?.kind === "main"
        ? renderJobsList(entry)
        : `<pre>${renderTerminalText(entry)}</pre>${renderResultSummary(entry?.result)}`;
    return `
    <section class="terminal-panel ${state.isTerminalVisible ? "" : "collapsed"}" aria-label="${escapeHTML(state.labels.terminalCommandOutputLabel)}">
      <header class="terminal-header">
        <span class="terminal-glyph" aria-hidden="true">${renderIcon("terminal", undefined, ">_")}</span>
        <div class="terminal-tabs">
          ${tabs
        .map((tab, index) => `
              <span class="terminal-tab-wrap ${index === state.activeTerminalIndex ? "active" : ""} ${escapeAttribute(tab.kind)}">
                <button type="button" class="terminal-tab" data-terminal-tab="${index}" ${tab.status ? `data-tooltip="${escapeAttribute(terminalStatusTooltip(tab.status))}"` : ""}>
                  ${renderTerminalTabLabel(tab)}
                </button>
                ${tab.kind === "main"
        ? ""
        : `<button type="button" class="terminal-tab-close" data-terminal-tab-close="${index}" aria-label="${escapeAttribute(formatLabel(state.labels.terminalCloseTabLabelFormat, { title: tab.title }))}">×</button>`}
              </span>`)
        .join("")}
        </div>
        <button type="button" class="terminal-icon-button" data-terminal-copy title="${escapeAttribute(copyLabel)}" aria-label="${escapeAttribute(copyLabel)}">
          <i class="bi bi-copy" aria-hidden="true"></i>
        </button>
        ${state.terminalCopyFeedback ? `<span class="terminal-copy-feedback" role="status">${escapeHTML(state.labels.terminalCopiedTextLabel ?? "Copied!")}</span>` : ""}
        ${renderTerminalToggle()}
      </header>
      ${state.isTerminalVisible
        ? `<div class="terminal-log" data-terminal-log-id="${escapeAttribute(entry?.id ?? "")}" dir="${escapeAttribute(textDirection)}">${output}</div>`
        : ""}
    </section>
  `;
}
/** The text the Copy button copies: the active tab's output, or the job list on the Jobs tab. */
export function activeTerminalCopyText(): string {
    const entry = activeTerminalEntry();
    return entry?.kind === "main" ? jobsListText(entry) : entry?.body ?? "";
}
/** Makes a job's tab the active one, reopening the tab if it was closed. */
export function openJob(id: string) {
    let index = state.terminalEntries.findIndex((entry) => entry.id === id);
    if (index < 0) {
        const job = state.jobHistory.find((entry) => entry.id === id);
        if (!job) {
            return;
        }
        state.terminalEntries.push(job);
        index = state.terminalEntries.length - 1;
    }
    state.activeTerminalIndex = index;
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
    state.terminalEntries.push(entry);
    state.terminalEntries = state.terminalEntries.slice(-40);
    state.activeTerminalIndex = Math.max(0, state.terminalEntries.length - 1);
    return id;
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
        state.terminalEntries[0].title = state.labels.terminalMainTabTitle ?? "Main";
        return;
    }
    const hadEntries = state.terminalEntries.length > 0;
    state.terminalEntries.unshift({
        id: "main",
        kind: "main",
        title: state.labels.terminalMainTabTitle ?? "Main",
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
