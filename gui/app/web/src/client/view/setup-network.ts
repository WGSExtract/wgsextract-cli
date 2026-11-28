import { escapeAttribute, escapeHTML } from "../dom.js";
import { renderTooltip } from "../model.js";
import { state } from "../state.js";
import { effectiveNetworkMode, networkPreferences } from "../../../../shared/network-settings.js";

/**
 * The bottom of the Setup card: Network mode, the one switch for all network activity, and Allow check for
 * updates, which turns off only the update checks. Network mode Off greys the checkbox out, unchecked.
 */
export function renderSetupNetworkControls() {
    const { labels, networkSettings } = state;
    const modes = { automatic: labels.networkModeAutomatic, full: labels.networkModeFull, low: labels.networkModeLow, off: labels.networkModeOff };
    const effectiveMode = effectiveNetworkMode(networkSettings);
    const modeLabel = labels.networkMode ?? "Network mode";
    const checkLabel = labels.networkAllowUpdateChecks ?? "Allow check for updates";
    const off = effectiveMode === "off";
    return `
      <div class="controls setup-network-controls" data-network-settings data-effective-network-mode="${effectiveMode}">
        <div class="form-row">
          <label class="row-label" for="network-mode-picker">${escapeHTML(modeLabel)}${renderTooltip(labels.networkModeTooltip)}</label>
          <select id="network-mode-picker" data-network-setting="mode">
            ${networkPreferences.map((mode) => `<option value="${mode}" ${networkSettings.mode === mode ? "selected" : ""}>${escapeHTML(modes[mode] ?? "")}</option>`).join("")}
          </select>
        </div>
        <label class="toggle-row">
          <span class="row-label">${escapeHTML(checkLabel)}${renderTooltip(labels.networkAllowUpdateChecksTooltip)}</span>
          <input type="checkbox" data-network-setting="allowUpdateChecks" aria-label="${escapeAttribute(checkLabel)}" ${networkSettings.allowUpdateChecks && !off ? "checked" : ""} ${off ? "disabled" : ""}>
        </label>
        <p class="inline-error" data-network-settings-error role="alert" hidden></p>
      </div>`;
}
