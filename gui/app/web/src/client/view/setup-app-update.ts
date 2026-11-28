import { escapeHTML } from "../dom.js";
import { state } from "../state.js";
import { effectiveNetworkMode } from "../../../../shared/network-settings.js";

type AppUpdateRow = { status: "ok" | "warning" | "failed" | "pending" | "running"; label: string; glyph: string };

/** The app's own update check, as setup's first step: the startup check, or the one rerunning setup starts. */
function appUpdateRow(): AppUpdateRow {
    const { labels, update } = state;
    if (!update.supported) {
        return { status: "pending", label: labels.setupStepUnavailableTitle ?? "Unavailable", glyph: "–" };
    }
    // Allow check for updates unchecked, or Network mode Off: setup leaves the check out.
    if (!state.networkSettings.allowUpdateChecks || effectiveNetworkMode(state.networkSettings) === "off") {
        return { status: "pending", label: labels.setupStepUpdateCheckOffTitle ?? "Off", glyph: "–" };
    }
    // A newer version found stays the step's result, even if downloading or installing it then failed.
    if (update.availableVersion && update.status !== "checking") {
        return { status: "warning", label: labels.setupStepUpdateAvailableTitle ?? "Update available", glyph: "!" };
    }
    switch (update.status) {
        case "checking":
            return { status: "running", label: labels.setupStepRunningTitle ?? "Running", glyph: `<span class="mini-spinner" aria-hidden="true"></span>` };
        case "available":
        case "downloading":
        case "downloaded":
        case "installing":
            return { status: "warning", label: labels.setupStepUpdateAvailableTitle ?? "Update available", glyph: "!" };
        case "error":
            return { status: "failed", label: labels.setupStepFailedTitle ?? "Failed", glyph: "×" };
        case "none":
            if (update.checked) return { status: "ok", label: labels.setupStepOkTitle ?? "OK", glyph: "✓" };
            break;
        case "idle":
            // A failed startup check stays quiet in the sidebar (no error button), but Setup reports it.
            if (update.checked) return { status: "failed", label: labels.setupStepFailedTitle ?? "Failed", glyph: "×" };
            break;
    }
    return { status: "pending", label: labels.setupStepPendingTitle ?? "Pending", glyph: "○" };
}

export function renderAppUpdateStepStatus() {
    const row = appUpdateRow();
    return `
    <li class="setup-step ${row.status}" data-setup-app-update>
      <span class="setup-step-status" aria-hidden="true">${row.glyph}</span>
      <span class="setup-step-main">
        <span class="setup-step-title">${escapeHTML(state.labels.setupStepAppUpdateTitle ?? "Check for app updates")}</span>
      </span>
      <span class="setup-step-kind">${escapeHTML(state.labels.setupStepKindAppUpdateLabel ?? "Updater")}</span>
      <span class="setup-step-duration"></span>
      <span class="setup-step-label">${escapeHTML(row.label)}</span>
    </li>
  `;
}
