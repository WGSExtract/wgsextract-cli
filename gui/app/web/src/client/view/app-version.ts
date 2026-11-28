import { escapeAttribute, escapeHTML } from "../dom.js";
import { formatLabel } from "../model.js";
import { state } from "../state.js";

/** An update the updater found, from the check until it is installed. */
const updatePending = new Set(["available", "downloading", "downloaded", "installing"]);

/** The version as shown: a development run adds "Dev" to the latest release's version. */
export function displayedAppVersion() {
    const version = String(state.appVersion ?? "").trim();
    if (!version || !state.appVersionDev) return version;
    return `${version} ${state.labels.appVersionDevLabel ?? "Dev"}`;
}

/** Out of date: a newer release is available, or always, for a development run. */
export function isAppVersionOutOfDate() {
    return state.appVersionDev || updatePending.has(state.update.status);
}

export function renderAppVersion() {
    const version = displayedAppVersion();
    if (!version) return "";
    const outOfDate = isAppVersionOutOfDate();
    const tooltip = state.appVersionDev
        ? formatLabel(state.labels.appVersionDevTooltip ?? "", { version: state.appVersion })
        : outOfDate ? state.labels.appVersionOutOfDateTooltip ?? "" : "";
    return `<p class="app-version${outOfDate ? " out-of-date" : ""}" data-app-version${tooltip ? ` title="${escapeAttribute(tooltip)}"` : ""}>${escapeHTML(state.labels.setupVersionLabel ?? "Version")} ${escapeHTML(version)}</p>`;
}
