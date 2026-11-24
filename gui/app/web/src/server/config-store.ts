import { readFile } from "node:fs/promises";
import { allControls, configEditorControls, firstPathValue, persistsFieldValue, splitPathList } from "../../../shared/rendering.js";
import type { BundleManifest, ConfigSetting, ControlSpec, StringMap, ValueMap } from "../../../shared/types.js";
import { writeFileAtomically } from "./atomic-file.js";
import type { BundleState } from "./bundle-state.js";
import { loadedSettingValue, settingKey, typedSettingValue } from "./config-settings.js";
import { parseConfigToml as parseToml, readOptionalFlatToml, serializeConfigToml as serializeToml } from "./config-toml.js";
import { errnoCode } from "./errors.js";
import { configPath, expandWorkspaceToken, isPreviousConfigPath } from "./paths.js";
import { withSettingsFileLock } from "./settings-lock.js";

export function initialConfigFilePaths(manifest: BundleManifest, bundleState: Pick<BundleState, "configFilePaths">, bundleRoot: string): StringMap {
    return Object.fromEntries(configEditorControls(manifest).flatMap((control) => {
        if (!control.configFile) {
            return [];
        }
        const persistedPath = bundleState.configFilePaths?.[control.id];
        const defaultPath = control.configFile.path;
        return [[
            control.id,
            persistedPath && !isPreviousConfigPath(control, persistedPath, bundleRoot) && isAllowedConfigPath(control, persistedPath, bundleRoot)
                ? persistedPath
                : defaultPath,
        ]];
    }));
}
function isAllowedConfigPath(control: ControlSpec, requestedPath: string, bundleRoot: string) {
    try {
        configPath(control, requestedPath, bundleRoot);
        return true;
    }
    catch {
        return false;
    }
}

export async function initialConfigValues(manifest: BundleManifest, configFilePaths: StringMap, bundleRoot: string, warnings: string[] = []): Promise<ValueMap> {
    const values: ValueMap = Object.fromEntries(configEditorControls(manifest).flatMap((control) => (control.settings ?? []).map((setting) => [`${control.id}.${setting.id}`, setting.value ?? ""])));
    for (const control of configEditorControls(manifest)) {
        const requestedPath = configFilePaths[control.id];
        if (!control.configFile || !requestedPath)
            continue;
        try {
            const fileValues = parseToml(await readFile(configPath(control, requestedPath, bundleRoot), "utf8"));
            for (const setting of control.settings ?? []) {
                const fileValue = fileValues[settingKey(setting)];
                if (fileValue != null) {
                    values[`${control.id}.${setting.id}`] = loadedSettingValue(setting, fileValue, warnings);
                }
            }
        }
        catch (error) {
            if (errnoCode(error) !== "ENOENT")
                throw error;
        }
    }
    return values;
}

export function initialFieldValues(manifest: BundleManifest, configValues: ValueMap, bundleState: Pick<BundleState, "fieldValues">): ValueMap {
    const values: ValueMap = Object.fromEntries(allControls(manifest)
        .filter((control) => persistsFieldValue(control.kind))
        .map((control) => [control.id, control.value ?? ""]));
    for (const control of allControls(manifest).filter((control) => persistsFieldValue(control.kind))) {
        const persisted = bundleState.fieldValues?.[control.id];
        if (!configSettingBindings(manifest, control.id).length && persisted != null) {
            values[control.id] = persisted;
        }
    }
    for (const control of configEditorControls(manifest)) {
        for (const setting of control.settings ?? []) {
            const value = configValues[`${control.id}.${setting.id}`] ?? setting.value ?? "";
            if (Object.hasOwn(values, settingKey(setting)))
                values[settingKey(setting)] = value;
            if (Object.hasOwn(values, setting.id))
                values[setting.id] = value;
        }
    }
    // Preserve persisted multi-file lists when config.toml holds their matching primary (first) file.
    for (const control of allControls(manifest).filter((candidate) => candidate.kind === "path" && candidate.multiple === true)) {
        const persisted = bundleState.fieldValues?.[control.id];
        if (splitPathList(persisted).length > 1) {
            const current = String(values[control.id] ?? "").trim();
            if (!current || firstPathValue(persisted) === current) {
                values[control.id] = persisted;
            }
        }
    }
    return values;
}
export function initialCheckedOptions(manifest: BundleManifest, configValues: ValueMap, bundleState: Pick<BundleState, "checkedOptions">): Record<string, string[]> {
    const values: Record<string, string[]> = {};
    for (const control of allControls(manifest).filter((candidate) => candidate.kind === "checkboxGroup")) {
        const binding = configSettingBindings(manifest, control.id)[0];
        if (binding) {
            const configValue = configValues[`${binding.control.id}.${binding.setting.id}`] ?? binding.setting.value ?? "";
            values[control.id] = String(configValue)
                .split(",")
                .map((item) => item.trim())
                .filter(Boolean);
        }
        else if (bundleState.checkedOptions?.[control.id]) {
            values[control.id] = bundleState.checkedOptions[control.id] ?? [];
        }
        else {
            values[control.id] = (control.options ?? []).filter((option) => option.selected).map((option) => option.id);
        }
    }
    return values;
}
export function configSettingBindings(manifest: BundleManifest, fieldID: string): Array<{ control: ControlSpec; setting: ConfigSetting }> {
    return configEditorControls(manifest).flatMap((control) => (control.settings ?? [])
        .filter((setting) => setting.id === fieldID || setting.key === fieldID)
        .map((setting) => ({ control, setting })));
}
export async function loadConfig(control: ControlSpec, requestedPath: string | undefined, bundleRoot: string) {
    const filePath = configPath(control, requestedPath, bundleRoot);
    const warnings: string[] = [];
    let values: Record<string, unknown> = {};
    try {
        values = parseToml(await readFile(filePath, "utf8"));
    }
    catch (error) {
        if (errnoCode(error) !== "ENOENT") {
            throw error;
        }
    }
    const loadedValues = Object.fromEntries((control.settings ?? []).map((setting) => [
            settingKey(setting),
            loadedSettingValue(setting, values[settingKey(setting)] ?? setting.value ?? "", warnings),
        ]));
    return {
        path: filePath,
        values: loadedValues,
        warnings,
    };
}
export async function saveConfig(control: ControlSpec, requestedPath: string | undefined, values: Readonly<Record<string, unknown>>, bundleRoot: string) {
    const filePath = configPath(control, requestedPath, bundleRoot);
    const byKey: Record<string, unknown> = {};
    for (const setting of control.settings ?? []) {
        const key = settingKey(setting);
        if (![key, setting.id, `${control.id}.${setting.id}`].some((name) => Object.hasOwn(values, name))) {
            continue;
        }
        const value = typedSettingValue(
            setting,
            values[key] ?? values[setting.id] ?? values[`${control.id}.${setting.id}`] ?? setting.value ?? "",
        );
        // The CLI reads this file too, and knows only real paths.
        byKey[key] = setting.kind === "path" && typeof value === "string" ? expandWorkspaceToken(value, bundleRoot) : value;
    }
    // The CLI and other windows save this file too, including settings the editor doesn't show: merge into
    // what is there now, under the lock they all take.
    await withSettingsFileLock(filePath, async (target) => {
        const existing = await readOptionalFlatToml(target) ?? {};
        // Remove former names only when the editor owns their canonical setting.
        if (Object.hasOwn(byKey, "mygenomes_library")) {
            delete existing.dataset_library;
            delete existing.genome_library;
        }
        await writeFileAtomically(target, serializeToml({ ...existing, ...byKey }));
    });
    return { path: filePath, keyCount: Object.keys(byKey).length };
}
