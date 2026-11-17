import { configEditorControls, configValueKey } from "../../../shared/rendering.js";
import { api } from "./api.js";
import { boundFieldKey, configSettingBindings, errorMessage, formatLabel, syncSharedField } from "./model.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";
import { appendTerminal } from "./terminal.js";
import { persistBundleState, type PersistBundleStateOptions } from "./operations-state.js";
import { askToMoveSettingFolder } from "./workspace-location.js";
import type { ConfigLoadResponse, ConfigSaveResponse, ConfigSetting, ControlSpec } from "../../../shared/types.js";

export async function loadInitialConfigs() {
    for (const control of configEditorControls(state.manifest ?? {})) {
        if (!control.configFile) {
            continue;
        }
        await loadConfig(control);
    }
}
export async function loadConfig(control: ControlSpec) {
    try {
        const result = await loadConfigIntoState(control);
        appendTerminal("config", formatLabel(state.labels.configLoadedFormat, { path: result.path }), result.warnings.join("\n"));
    }
    catch (error) {
        appendTerminal("error", formatLabel(state.labels.configLoadErrorFormat, { label: control.label, error: errorMessage(error) }));
    }
}
export async function fieldValueChanged(value: string, control: ControlSpec) {
    const movable = configSettingBindings(control.id).find((binding) => binding.setting.moveContents === true);
    if (movable) {
        await configSettingChanged(value, movable.setting, movable.control);
        scheduleRender();
        return;
    }
    state.fieldValues[control.id] = value;
    await syncBoundConfigSettings(control.id, value, { removeFieldIDs: [control.id] });
}
export async function checkedOptionsChanged(selectedIDs: Set<string>, control: ControlSpec) {
    state.checkedOptions[control.id] = selectedIDs;
    const value = [...selectedIDs].sort().join(",");
    await syncBoundConfigSettings(control.id, value, { removeCheckedIDs: [control.id] });
}
async function loadConfigIntoState(control: ControlSpec) {
    const result = await api<ConfigLoadResponse>("/api/config/load", {
        method: "POST",
        body: { controlID: control.id, path: state.configFilePaths[control.id] },
    });
    state.configFilePaths[control.id] = result.path;
    for (const setting of control.settings ?? []) {
        const value = (setting.key ? result.values[setting.key] : undefined) ?? setting.value ?? "";
        state.configValues[configValueKey(control, setting)] = value;
        syncSharedField(setting, value);
    }
    return result;
}
async function syncBoundConfigSettings(controlID: string, value: string, removePersistedState: PersistBundleStateOptions) {
    const bindings = configSettingBindings(controlID);
    if (!bindings.length) {
        await persistBundleState();
        return;
    }
    for (const binding of bindings) {
        state.configValues[configValueKey(binding.control, binding.setting)] = value;
        await saveConfig(binding.control);
    }
    await persistBundleState(removePersistedState);
}
export async function configSettingChanged(value: string, setting: ConfigSetting, control: ControlSpec) {
    const current = String(state.configValues[configValueKey(control, setting)] ?? "");
    if (setting.moveContents === true && current.trim() && value.trim() && current !== value) {
        // The folder's contents may move with it: the user decides, and until then the setting keeps its value.
        askToMoveSettingFolder(control.id, setting.key ?? setting.id, current, value, () => saveSetting(value, setting, control));
        return;
    }
    await saveSetting(value, setting, control);
}
async function saveSetting(value: string, setting: ConfigSetting, control: ControlSpec) {
    state.configValues[configValueKey(control, setting)] = value;
    const fieldKey = boundFieldKey(setting);
    if (fieldKey) {
        state.fieldValues[fieldKey] = value;
    }
    await saveConfig(control);
    if (fieldKey) {
        await persistBundleState({ removeFieldIDs: [fieldKey] });
    }
}
export async function saveConfig(control: ControlSpec, reportSuccess = false) {
    try {
        const values = Object.fromEntries((control.settings ?? []).filter((setting) => state.uiMode === "advanced" || setting.advancedOnly !== true).map((setting) => [setting.key, state.configValues[configValueKey(control, setting)] ?? setting.value ?? ""]));
        const result = await api<ConfigSaveResponse>("/api/config/save", {
            method: "POST",
            body: { controlID: control.id, path: state.configFilePaths[control.id], values },
        });
        state.configFilePaths[control.id] = result.path;
        if (reportSuccess) {
            appendTerminal("config", formatLabel(state.labels.configSavedFormat, { count: result.keyCount, path: result.path }));
        }
    }
    catch (error) {
        appendTerminal("error", formatLabel(state.labels.configSaveErrorFormat, { label: control.label, error: errorMessage(error) }));
        scheduleRender();
        throw error;
    }
}
