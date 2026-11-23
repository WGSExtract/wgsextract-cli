import type { ConfigSetting, StateValue } from "../../../shared/types.js";
import { errorMessage } from "./errors.js";

/** The settings-file key a config setting is stored under (required by the schema). */
export function settingKey(setting: ConfigSetting): string {
    return setting.key ?? setting.id;
}

export function loadedSettingValue(setting: ConfigSetting, value: unknown, warnings: string[]): StateValue {
    try {
        const expectedType = setting.kind === "toggle" || typeof setting.value === "boolean"
            ? "boolean" : typeof setting.value === "number" ? "number" : "string";
        if (typeof value !== expectedType) {
            throw new Error(`Invalid ${expectedType} value for setting ${settingKey(setting)}.`);
        }
        return typedSettingValue(setting, value);
    }
    catch (error) {
        const warning = `${errorMessage(error)} Ignoring it and using the declared default.`;
        warnings.push(warning);
        console.warn(warning);
        return typedSettingValue(setting, setting.value ?? "");
    }
}

export function typedSettingValue(setting: ConfigSetting, value: unknown): StateValue {
    if (setting.kind === "toggle" || typeof setting.value === "boolean") {
        if (typeof value === "boolean") {
            return value;
        }
        if (value === "") {
            return setting.value === true;
        }
        if (value === "true" || value === "false") {
            return value === "true";
        }
        throw new Error(`Invalid boolean value for setting ${settingKey(setting)}.`);
    }
    if (typeof setting.value === "number") {
        const number = typeof value === "string" && value.trim() === "" ? setting.value : Number(value);
        if ((typeof value !== "string" && typeof value !== "number") || !Number.isFinite(number)) {
            throw new Error(`Invalid number value for setting ${settingKey(setting)}.`);
        }
        return number;
    }
    if (typeof value !== "string") {
        throw new Error(`Invalid string value for setting ${settingKey(setting)}.`);
    }
    return value;
}
