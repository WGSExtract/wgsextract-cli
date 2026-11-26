import { api } from "./api.js";
import { bindGenomeViewerEvents } from "./genome-viewer-entry.js";
import { configValueKey, splitPathList } from "../../../shared/rendering.js";
import { installGlobalBundleLoadHandler, loadBundleFromPicker, type Bootstrap } from "./bundle-loading.js";
import { bindCertificationLinks } from "./certification-links.js";
import { normalizeColorTheme, normalizeIconSet } from "./icons.js";
import { elements, errorMessage, findControl, resolveText } from "./model.js";
import { checkedOptionsChanged, clearDataSourceCache, clearFileStateCache, configSettingChanged, fieldValueChanged, loadConfig, cancelSetup, openBundleWorkspace, persistBundleState, retryActionPrecheck, retryDataSource, runAction, runSetup, saveConfig } from "./operations.js";
import { cancelPendingAlreadyRun, forceRunPendingAlreadyRun } from "./operations-already-run.js";
import { cancelPendingPrerequisites, continuePendingPrerequisites, installPendingPrerequisites } from "./operations-prerequisites-dialog.js";
import { bindAlreadyRunFocus } from "./view/already-run.js";
import { chooseLocalPath } from "./path-picker.js";
import { bindPrerequisitesFocus } from "./prerequisites-focus.js";
import { openResultCard } from "./result-card-dialog.js";
import { nextFrame, scheduleRender } from "./rerender.js";
import { bindSplitters } from "./splitters.js";
import { state } from "./state.js";
import { dismissUpdatePopover, downloadUpdate, installUpdate, toggleUpdatePopover } from "./tauri-updater.js";
import { activeTerminalCopyText, appendTerminal, closeTerminalTab, openJob } from "./terminal.js";
import { applyUIModeTerminalVisibility, setTerminalVisible } from "./terminal-visibility.js";
import { bindTooltipEvents } from "./tooltips.js";
import { bindWorkspaceLocationEvents } from "./workspace-location.js";
import { bindCollapsibleSections } from "./view/collapsible.js";
import { setupPageID } from "./view.js";
import type { ConfigSetting, ControlSpec } from "../../../shared/types.js";
import { normalizeUIMode } from "../../../shared/ui-mode.js";

const app: HTMLElement = (() => {
    const el = document.querySelector<HTMLElement>("#app");
    if (!el) {
        throw new Error("Missing required root element: `#app`");
    }
    return el;
})();
let terminalCopyFeedbackTimer = 0;
let updateOutsideClickBound = false;
let installedGlobalAboutHandler = false;
export function bindEvents(bootstrap: Bootstrap) {
    bindGenomeViewerEvents();
    installGlobalBundleLoadHandler(bootstrap);
    installGlobalAboutHandler();
    bindTooltipEvents();
    bindCertificationLinks();
    bindSplitters(app);
    bindUpdateEvents();
    elements("[data-page-id]").forEach((button) => {
        button.addEventListener("click", async () => {
            state.activePageID = requiredDataset(button, "pageId");
            clearFileStateCache();
            await persistAndRender();
        });
    });
    elements<HTMLButtonElement>("[data-ui-mode]").forEach((button) => {
        button.addEventListener("click", () => selectUIMode(requiredDataset(button, "uiMode")));
        button.addEventListener("keydown", (event) => {
            // Radio-group keys: an arrow key moves to the other mode and selects it.
            if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
                return;
            }
            event.preventDefault();
            const other = normalizeUIMode(button.dataset.uiMode) === "basic" ? "advanced" : "basic";
            // Focus moves first, so the re-render's focus restore keeps it on the newly selected mode.
            app.querySelector<HTMLElement>(`[data-ui-mode="${other}"]`)?.focus();
            void selectUIMode(other);
        });
    });
    elements<HTMLSelectElement>("[data-locale-picker]").forEach((picker) => {
        picker.addEventListener("change", async (event) => {
            const target = event.currentTarget as HTMLSelectElement;
            clearDataSourceCache();
            const useSystemDefault = target.value === "__system__";
            state.localizationCode = useSystemDefault ? "" : target.value;
            state.usingSystemDefaultLocale = useSystemDefault;
            await persistBundleState();
            await bootstrap(useSystemDefault ? undefined : target.value);
        });
    });
    app.querySelector<HTMLSelectElement>("[data-icon-set-picker]")?.addEventListener("change", async (event) => {
        state.iconSet = normalizeIconSet((event.currentTarget as HTMLSelectElement).value);
        await persistAndRender();
    });
    app.querySelector<HTMLSelectElement>("[data-color-theme-picker]")?.addEventListener("change", async (event) => {
        state.colorTheme = normalizeColorTheme((event.currentTarget as HTMLSelectElement).value);
        await persistAndRender();
    });
    app.querySelector<HTMLInputElement>("[data-privacy-mode-toggle]")?.addEventListener("change", async (event) => {
        const enabled = (event.currentTarget as HTMLInputElement).checked;
        try {
            await api("/api/privacy-mode", { method: "POST", body: { enabled } });
            state.privacyMode = enabled;
        }
        catch (error) {
            // Unsaved, the checkbox goes back to the setting in force.
            appendTerminal("error", state.labels.privacyModeLabel ?? "Privacy mode", errorMessage(error));
        }
        finally {
            scheduleRender();
        }
    });
    app.querySelector<HTMLSelectElement>("[data-web-font-picker]")?.addEventListener("change", async (event) => {
        state.webUIFont = (event.currentTarget as HTMLSelectElement).value === "sfPro" ? "sfPro" : "system";
        await persistAndRender();
    });
    app.querySelector("[data-run-setup]")?.addEventListener("click", async () => {
        await runSetup();
    });
    app.querySelector("[data-cancel-setup]")?.addEventListener("click", () => {
        cancelSetup();
    });
    elements("[data-setup-global-start], [data-setup-prompt-run]").forEach((button) => {
        button.addEventListener("click", async () => {
            state.setupPromptVisible = false;
            state.setupPromptDismissed = true;
            state.activePageID = setupPageID();
            await persistAndRender();
            await runSetup();
        });
    });
    app.querySelector("[data-setup-prompt-dismiss]")?.addEventListener("click", () => {
        state.setupPromptVisible = false;
        state.setupPromptDismissed = true;
        scheduleRender();
    });
    app.querySelector("[data-open-bundle-workspace]")?.addEventListener("click", async () => {
        await openBundleWorkspace();
    });
    bindWorkspaceLocationEvents(app);
    bindCollapsibleSections(app);
    app.querySelector("[data-load-bundle]")?.addEventListener("click", async () => {
        await loadBundleFromPicker(bootstrap);
    });
    elements<HTMLInputElement | HTMLSelectElement>("[data-field-id]").forEach((input) => {
        input.addEventListener("change", async () => {
            const id = requiredDataset(input, "fieldId");
            const control = findControl(id);
            let nextValue = input.dataset.toggle != null ? String((input as HTMLInputElement).checked) : input.value;
            if (control?.kind === "path" && control.multiple === true) {
                const existing = splitPathList(state.fieldValues[id]);
                const entered = splitPathList(nextValue);
                if (existing.length > 1 && entered.length === 1) {
                    nextValue = [entered[0], ...existing.slice(1)].join("\n");
                }
                else if (entered.length > 1) {
                    nextValue = entered.join("\n");
                }
            }
            await fieldValueChanged(nextValue, control);
            clearDataSourcesAndRender();
        });
    });
    elements("[data-path-prompt]").forEach((button) => {
        button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const id = requiredDataset(button, "pathPrompt");
            const control = findControl(id);
            const value = await chooseLocalPath(control, state.fieldValues[id] ?? "");
            if (value) {
                await fieldValueChanged(value, control);
                clearDataSourcesAndRender();
            }
        });
    });
    elements("[data-path-add]").forEach((button) => {
        button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const id = requiredDataset(button, "pathAdd");
            const control = findControl(id);
            const value = await chooseLocalPath(control, state.fieldValues[id] ?? "");
            if (value) {
                const existing = splitPathList(state.fieldValues[id]);
                const added = splitPathList(value).filter((entry) => !existing.includes(entry));
                const combined = [...existing, ...added];
                if (combined.length > 0) {
                    await fieldValueChanged(combined.join("\n"), control);
                    clearDataSourcesAndRender();
                }
            }
        });
    });
    elements("[data-path-remove]").forEach((button) => {
        button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const raw = requiredDataset(button, "pathRemove");
            const splitAt = raw.lastIndexOf(":");
            const id = raw.slice(0, splitAt);
            const index = Number(raw.slice(splitAt + 1));
            const control = findControl(id);
            const existing = splitPathList(state.fieldValues[id]);
            if (Number.isInteger(index) && index >= 0 && index < existing.length) {
                existing.splice(index, 1);
                await fieldValueChanged(existing.join("\n"), control);
                clearDataSourcesAndRender();
            }
        });
    });
    elements<HTMLInputElement>("[data-check-group]").forEach((input) => {
        input.addEventListener("change", async () => {
            const groupID = requiredDataset(input, "checkGroup");
            const selected = state.checkedOptions[groupID] ?? new Set<string>();
            state.checkedOptions[groupID] = selected;
            input.checked ? selected.add(input.value) : selected.delete(input.value);
            await checkedOptionsChanged(selected, findControl(groupID));
            clearDataSourcesAndRender();
        });
    });
    elements<HTMLInputElement>("[data-config-path]").forEach((input) => {
        input.addEventListener("change", async () => {
            state.configFilePaths[requiredDataset(input, "configPath")] = input.value;
            await persistBundleState();
        });
    });
    elements<HTMLInputElement | HTMLSelectElement>("[data-config-control][data-config-setting]").forEach((input) => {
        input.addEventListener("change", async () => {
            const control = findControl(requiredDataset(input, "configControl"));
            const setting = findSetting(control, requiredDataset(input, "configSetting"));
            const value = input.dataset.toggle != null ? String((input as HTMLInputElement).checked) : input.value;
            await configSettingChanged(value, setting, control);
            clearDataSourcesAndRender();
        });
    });
    elements("[data-config-path-prompt]").forEach((button) => {
        button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const [controlID, settingID] = requiredDataset(button, "configPathPrompt").split(":");
            const control = findControl(controlID ?? "");
            const setting = findSetting(control, settingID ?? "");
            const key = configValueKey(control, setting);
            const value = await chooseLocalPath(setting, state.configValues[key] ?? "");
            if (value) {
                await configSettingChanged(value, setting, control);
                clearDataSourcesAndRender();
            }
        });
    });
    elements("[data-load-config]").forEach((button) => {
        button.addEventListener("click", async () => {
            await loadConfig(findControl(requiredDataset(button, "loadConfig")));
            clearDataSourcesAndRender();
        });
    });
    elements("[data-save-config]").forEach((button) => {
        button.addEventListener("click", async () => {
            await saveConfig(findControl(requiredDataset(button, "saveConfig")), true);
            scheduleRender();
        });
    });
    elements("[data-action-id]").forEach((button) => {
        button.addEventListener("click", async () => {
            let action;
            let context;
            let reference;
            try {
                action = JSON.parse(button.dataset.action ?? "");
                context = JSON.parse(button.dataset.actionContext ?? "");
                reference = JSON.parse(button.dataset.actionRef ?? "");
            }
            catch (error) {
                const message = error instanceof Error ? error.message : String(error);
                console.error("Failed to parse action data:", error);
                appendTerminal("error", state.labels.terminalProcessErrorTitle, message);
                scheduleRender();
                return;
            }
            await runAction(action, context, reference, { opener: button });
        });
    });
    elements("[data-result-open], [data-result-reveal]").forEach((button) => {
        button.addEventListener("click", async () => {
            const reveal = button.dataset.resultReveal != null;
            try {
                await api<void>("/api/result/open", { method: "POST", body: { path: button.dataset.resultReveal ?? button.dataset.resultOpen, reveal } });
            }
            catch (error) {
                appendTerminal("error", state.labels.terminalProcessErrorTitle, errorMessage(error));
                scheduleRender();
            }
        });
    });
    elements("[data-result-card]").forEach((button) => {
        button.addEventListener("click", () => void openResultCard(button.dataset.resultCard ?? "", state.labels));
    });
    elements("[data-result-open-setup]").forEach((button) => {
        button.addEventListener("click", async () => {
            state.activePageID = setupPageID();
            await persistAndRender();
        });
    });
    elements("[data-terminal-tab]").forEach((button) => {
        button.addEventListener("click", () => {
            state.activeTerminalIndex = Number(button.dataset.terminalTab);
            // A tab in the collapsed row opens the pane to show what it holds.
            setTerminalVisible(true);
            scheduleRender();
        });
    });
    elements("[data-job-open]").forEach((button) => {
        button.addEventListener("click", () => {
            openJob(requiredDataset(button, "jobOpen"));
            scheduleRender();
        });
    });
    elements("[data-terminal-tab-close]").forEach((button) => {
        button.addEventListener("click", () => {
            closeTerminalTab(Number(button.dataset.terminalTabClose));
            scheduleRender();
        });
    });
    app.querySelector("[data-terminal-copy]")?.addEventListener("click", async () => {
        await copyText(activeTerminalCopyText());
        state.terminalCopyFeedback = true;
        window.clearTimeout(terminalCopyFeedbackTimer);
        terminalCopyFeedbackTimer = window.setTimeout(() => {
            state.terminalCopyFeedback = false;
            scheduleRender();
        }, 1600);
        scheduleRender();
    });
    app.querySelector("[data-terminal-toggle]")?.addEventListener("click", () => {
        setTerminalVisible(!state.isTerminalVisible);
        scheduleRender();
    });
    app.querySelector("[data-sidebar-toggle]")?.addEventListener("click", () => {
        state.isSidebarVisible = !state.isSidebarVisible;
        localStorage.setItem("wgsextractGui.sidebarVisible", state.isSidebarVisible ? "true" : "false");
        scheduleRender();
    });
    elements("[data-section-toggle]").forEach((button) => {
        button.addEventListener("click", () => {
            const key = requiredDataset(button, "sectionToggle");
            if (!state.toggledSections.delete(key)) {
                state.toggledSections.add(key);
            }
            scheduleRender();
        });
    });
    app.querySelector("[data-setup-toggle]")?.addEventListener("click", (event) => {
        state.setupExpanded = (event.currentTarget as HTMLElement).getAttribute("aria-expanded") !== "true";
        scheduleRender();
    });
    elements("[data-retry-source]").forEach((button) => {
        button.addEventListener("click", () => {
            retryDataSource(requiredDataset(button, "retrySource"));
        });
    });
    elements("[data-retry-precheck]").forEach((button) => {
        button.addEventListener("click", () => {
            retryActionPrecheck(requiredDataset(button, "retryPrecheck"));
        });
    });
    app.querySelector("[data-confirm-cancel]")?.addEventListener("click", () => {
        state.pendingConfirmation = null;
        scheduleRender();
    });
    app.querySelector<HTMLInputElement>("[data-confirm-input]")?.addEventListener("input", (event) => {
        const target = event.currentTarget as HTMLInputElement;
        const pending = state.pendingConfirmation;
        if (!pending?.action.confirm) {
            return;
        }
        pending.input = target.value;
        const requiredText = resolveText(pending.action.confirm.requiredText ?? "", pending.context);
        const button = app.querySelector<HTMLButtonElement>("[data-confirm-run]");
        if (button) {
            button.disabled = Boolean(requiredText && target.value !== requiredText);
        }
    });
    app.querySelector("[data-confirm-run]")?.addEventListener("click", async () => {
        const pending = state.pendingConfirmation;
        if (!pending) {
            return;
        }
        state.pendingConfirmation = null;
        const { confirm: _confirm, ...action } = pending.action;
        await runAction(action, pending.context, pending.reference);
    });
    app.querySelector("[data-already-run-cancel]")?.addEventListener("click", cancelPendingAlreadyRun);
    app.querySelector("[data-already-run-force]")?.addEventListener("click", () => void forceRunPendingAlreadyRun());
    bindAlreadyRunFocus(app, cancelPendingAlreadyRun);
    app.querySelector("[data-prerequisites-cancel]")?.addEventListener("click", cancelPendingPrerequisites);
    app.querySelector("[data-prerequisites-continue]")?.addEventListener("click", () => void continuePendingPrerequisites());
    app.querySelector("[data-prerequisites-install]")?.addEventListener("click", () => void installPendingPrerequisites());
    bindPrerequisitesFocus(app, cancelPendingPrerequisites);
    app.querySelector("[data-about-close]")?.addEventListener("click", closeAboutDialog);
    app.querySelector("[data-about-backdrop]")?.addEventListener("click", (event) => {
        if (event.target === event.currentTarget) {
            closeAboutDialog();
        }
    });
    app.querySelector("[data-about-dialog]")?.addEventListener("keydown", (event) => {
        const keyboardEvent = event as KeyboardEvent;
        if (keyboardEvent.key === "Escape") {
            keyboardEvent.preventDefault();
            closeAboutDialog();
        }
    });
}

function bindUpdateEvents() {
    app.querySelector("[data-update-toggle]")?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const wasVisible = state.update.popoverVisible;
        toggleUpdatePopover();
        if (!wasVisible) {
            nextFrame(focusUpdatePopover);
        }
    });
    const updatePopover = app.querySelector("[data-update-popover]") as HTMLElement | null;
    updatePopover?.addEventListener("click", (event) => {
        event.stopPropagation();
    });
    updatePopover?.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            dismissUpdatePopover();
            nextFrame(() => (app.querySelector("[data-update-toggle]") as HTMLElement | null)?.focus());
        }
    });
    app.querySelector("[data-update-download]")?.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        await downloadUpdate("user");
    });
    app.querySelector("[data-update-install]")?.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        await installUpdate();
    });
    if (!updateOutsideClickBound) {
        updateOutsideClickBound = true;
        document.addEventListener("click", (event) => {
            const target = event.target;
            if (target instanceof Element && target.closest("[data-update-surface]")) {
                return;
            }
            dismissUpdatePopover();
        });
    }
}

function focusUpdatePopover() {
    (app.querySelector("[data-update-popover]") as HTMLElement | null)?.focus();
}

function installGlobalAboutHandler() {
    if (installedGlobalAboutHandler) {
        return;
    }
    installedGlobalAboutHandler = true;
    window.addEventListener("wgsextract-gui-show-about", showAboutDialog);
}

function showAboutDialog() {
    state.aboutDialogVisible = true;
    scheduleRender();
    requestAnimationFrame(() => {
        (app.querySelector("[data-about-dialog]") as HTMLElement | null)?.focus();
    });
}

function closeAboutDialog() {
    state.aboutDialogVisible = false;
    scheduleRender();
}

/** Switches between Basic and Advanced and saves the choice, so the app restarts in the same mode. */
async function selectUIMode(value: string) {
    const mode = normalizeUIMode(value);
    if (mode === state.uiMode) {
        return;
    }
    state.uiMode = mode;
    clearFileStateCache();
    applyUIModeTerminalVisibility();
    await persistAndRender();
}

async function persistAndRender(options = {}) {
    await persistBundleState(options);
    scheduleRender();
}

function clearDataSourcesAndRender() {
    clearDataSourceCache();
    clearFileStateCache();
    scheduleRender();
}
async function copyText(text: string) {
    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.select();
    try {
        document.execCommand("copy");
    }
    finally {
        textarea.remove();
    }
}
function requiredDataset(element: HTMLElement, key: string): string {
    const value = element.dataset[key];
    if (!value) {
        throw new Error(`Missing required data-${key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)} attribute.`);
    }
    return value;
}
function findSetting(control: ControlSpec, settingID: string): ConfigSetting {
    const setting = control.settings?.find((candidate) => candidate.id === settingID);
    if (!setting) {
        throw new Error(`Unknown setting: ${control.id}.${settingID}`);
    }
    return setting;
}
