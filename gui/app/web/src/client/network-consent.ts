import { effectiveNetworkMode, type NetworkSettings } from "../../../shared/network-settings.js";
import { api } from "./api.js";
import { escapeHTML } from "./dom.js";
import { formatLabel } from "./model.js";
import { state } from "./state.js";
import { scheduleRender } from "./rerender.js";

export function showNetworkConsent(onChoice: (acceptedHere: boolean) => void) {
    if (document.querySelector("[data-network-consent]")) return;
    const { labels } = state;
    const size = state.manifest?.setup?.initialInstallSizeGB;
    const sizeMessage = size && labels.setupInitialInstallSizeFormat
        ? formatLabel(labels.setupInitialInstallSizeFormat, { size: String(size) })
        : "";
    const dialog = document.createElement("dialog");
    dialog.className = "network-consent-dialog";
    dialog.dataset.networkConsent = "";
    dialog.setAttribute("aria-labelledby", "network-consent-title");
    dialog.setAttribute("aria-describedby", `network-consent-question network-consent-detail${sizeMessage ? " network-consent-size" : ""}`);
    dialog.innerHTML = `
      <h2 id="network-consent-title">${escapeHTML(labels.networkTitle ?? "")}</h2>
      <p id="network-consent-question">${escapeHTML(labels.networkConsentQuestion ?? "")}</p>
      <p id="network-consent-detail">${escapeHTML(labels.networkConsentDetail ?? "")}</p>
      ${sizeMessage ? `<p id="network-consent-size">${escapeHTML(sizeMessage)}</p>` : ""}
      <p class="inline-error" role="alert" hidden></p>
      <form class="modal-actions" method="dialog">
        <button class="secondary-button" value="deferred">${escapeHTML(labels.networkNotNow ?? "")}</button>
        <button class="action-button primary" value="accepted" autofocus>${escapeHTML(labels.setupStepOkTitle ?? "OK")}</button>
      </form>`;
    const buttons = [...dialog.querySelectorAll("button")];
    const error = dialog.querySelector<HTMLElement>("[role=alert]");
    let saving = false;
    async function choose(consent: "accepted" | "deferred") {
        if (saving) return;
        saving = true;
        dialog.setAttribute("aria-busy", "true");
        buttons.forEach((button) => { button.disabled = true; });
        let recordedChoice: boolean;
        try {
            const result = await api<NetworkSettings & { recordedChoice: boolean }>("/api/network-settings", { body: { consent } });
            state.networkSettings = result;
            recordedChoice = result.recordedChoice;
        } catch {
            saving = false;
            dialog.removeAttribute("aria-busy");
            buttons.forEach((button) => { button.disabled = false; });
            buttons.find((button) => button.value === consent)?.focus();
            if (error) {
                error.textContent = labels.networkSaveError ?? "";
                error.hidden = false;
            }
            return;
        }
        dialog.close();
        dialog.remove();
        onChoice(recordedChoice && consent === "accepted");
    }
    dialog.addEventListener("submit", (event) => {
        event.preventDefault();
        void choose(event.submitter instanceof HTMLButtonElement && event.submitter.value === "accepted" ? "accepted" : "deferred");
    });
    dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        void choose("deferred");
    });
    document.body.append(dialog);
    dialog.showModal();
}

let savingSettings = false;
let settingsVisible = false;
export async function fetchNetworkSettings() {
    return api<NetworkSettings>("/api/network-settings");
}

async function refreshConnectionCost() {
    const previous = state.networkSettings;
    const fresh = await fetchNetworkSettings().catch(() => undefined);
    if (!fresh || savingSettings || state.networkSettings !== previous) return;
    if (fresh.detectedCost !== previous.detectedCost || fresh.mode !== previous.mode || fresh.consent !== previous.consent ||
        fresh.allowUpdateChecks !== previous.allowUpdateChecks) {
        state.networkSettings = fresh;
        scheduleRender();
    }
}

function refreshVisibleSettings() {
    if (settingsVisible) void refreshConnectionCost();
}

type NetworkInput = HTMLSelectElement | HTMLInputElement;

/** Allow check for updates is greyed out while Network mode is Off, which blocks every check. */
function inputLocked(input: NetworkInput) {
    return savingSettings || (input.dataset.networkSetting === "allowUpdateChecks" && effectiveNetworkMode(state.networkSettings) === "off");
}

export function bindNetworkSettings(root: HTMLElement) {
    const inputs = [...root.querySelectorAll<NetworkInput>("[data-network-setting]")];
    if (inputs.length > 0 && !settingsVisible) void refreshConnectionCost();
    settingsVisible = inputs.length > 0;
    window.addEventListener("focus", refreshVisibleSettings);
    window.addEventListener("online", refreshVisibleSettings);
    window.addEventListener("offline", refreshVisibleSettings);
    for (const input of inputs) {
        input.disabled = inputLocked(input);
        input.addEventListener("change", async () => {
            const key = input.dataset.networkSetting;
            if (savingSettings || (key !== "mode" && key !== "allowUpdateChecks")) return;
            savingSettings = true;
            inputs.forEach((item) => { item.disabled = true; });
            const value = input instanceof HTMLInputElement ? input.checked : input.value;
            try {
                state.networkSettings = await api<NetworkSettings>("/api/network-settings", { body: { [key]: value } });
                scheduleRender();
            } catch {
                if (input instanceof HTMLInputElement) input.checked = state.networkSettings.allowUpdateChecks;
                else input.value = state.networkSettings.mode;
                const error = root.querySelector<HTMLElement>("[data-network-settings-error]");
                if (error) {
                    error.textContent = state.labels.networkSaveError ?? "";
                    error.hidden = false;
                }
            } finally {
                savingSettings = false;
                root.querySelectorAll<NetworkInput>("[data-network-setting]").forEach((item) => { item.disabled = inputLocked(item); });
            }
        });
    }
}
