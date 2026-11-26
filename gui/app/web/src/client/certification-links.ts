import { api } from "./api.js";
import { elements } from "./model.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";
import { appendTerminal } from "./terminal.js";
import { setTerminalVisible } from "./terminal-visibility.js";

export function bindCertificationLinks(): void {
    if (!("__TAURI__" in window)) return;
    elements<HTMLAnchorElement>("[data-certification-command]").forEach((link) => {
        link.addEventListener("click", async (event) => {
            event.preventDefault();
            try {
                await api("/api/certification/open", { body: { command: link.dataset.certificationCommand } });
            } catch {
                appendTerminal("error", state.labels.certificationStrings?.openError);
                setTerminalVisible(true);
                scheduleRender();
            }
        });
    });
}
