import { escapeHTML } from "./dom.js";
import type { TerminalEntry, TerminalStatus } from "../../../shared/types.js";

/** A tab's status glyph and title, shared by the tab row and the Jobs list so a job looks the same in both. */
export function renderTerminalTabLabel(tab: TerminalEntry): string {
    const glyph = tab.kind === "command" ? `<span class="mini-spinner" aria-hidden="true"></span>` : terminalStatusGlyph(tab.kind, tab.status);
    return `${glyph}<span>${escapeHTML(tab.title)}</span>`;
}

export function terminalStatusGlyph(kind: string, status?: TerminalStatus): string {
    switch (kind) {
        case "success":
            return '<span class="terminal-status success" aria-hidden="true">●</span>';
        case "warning":
            return `<span class="terminal-status warning" aria-hidden="true">${escapeHTML(status?.symbol ?? "▲")}</span>`;
        case "error":
            return `<span class="terminal-status error" aria-hidden="true">${escapeHTML(status?.symbol ?? "●")}</span>`;
        case "config":
            return '<span class="terminal-status config" aria-hidden="true">●</span>';
        case "cancelled":
            return '<span class="terminal-status cancelled" aria-hidden="true">○</span>';
        default:
            return "";
    }
}
