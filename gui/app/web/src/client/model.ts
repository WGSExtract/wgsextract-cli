import { commandContextFromState, configEditorControls, configValueKey, firstPathValue, optionTitle, splitPathList } from "../../../shared/rendering.js";
import { localizationLabels } from "../../../shared/localization.js";
import type { CommandContext, ConfigSetting, ControlOption, ControlSpec, RowTagSpec, StateValue, ValueMap } from "../../../shared/types.js";
import type { WorkspaceRoots } from "../../../shared/workspace-paths.js";
import { renderTextWithDocLinks } from "../../../shared/docs-links.js";
import { escapeAttribute, escapeHTML } from "./dom.js";
import { state } from "./state.js";

const fallbackLabels = localizationLabels({});

/** The workspaces the GUI shows paths relative to. */
export function workspaceRoots(): WorkspaceRoots {
    return { bundle: state.bundleRootPath, users: state.usersWorkspacePath || undefined };
}
export function commandContext(rowValues: ValueMap = {}, sectionValues: ValueMap = {}): CommandContext {
    return commandContextFromState(state, rowValues, sectionValues);
}
export function configDataSourceContext(control: ControlSpec): CommandContext {
    const settingValues = { ...state.configValues };
    for (const setting of control.settings ?? []) {
        const value = state.configValues[configValueKey(control, setting)] ?? setting.value ?? "";
        settingValues[setting.id] = value;
        if (setting.key) {
            settingValues[setting.key] = value;
        }
    }
    return commandContextFromState({ ...state, fieldValues: { ...state.fieldValues, ...settingValues }, configValues: settingValues });
}
function assignSharedField(key: string, value: StateValue) {
    const existing = state.fieldValues[key];
    // Preserve a multi-file selection in state.json when config.toml matches its primary (first) file.
    if (splitPathList(existing).length > 1 && firstPathValue(existing) === String(value ?? "").trim()) {
        return;
    }
    state.fieldValues[key] = value;
}
export function syncSharedField(setting: ConfigSetting, value: StateValue) {
    if (setting.key && Object.hasOwn(state.fieldValues, setting.key)) {
        assignSharedField(setting.key, value);
    }
    if (Object.hasOwn(state.fieldValues, setting.id)) {
        assignSharedField(setting.id, value);
    }
}
export function boundFieldKey(setting: ConfigSetting): string | undefined {
    if (setting.key && Object.hasOwn(state.fieldValues, setting.key))
        return setting.key;
    if (Object.hasOwn(state.fieldValues, setting.id))
        return setting.id;
    return undefined;
}
export function configSettingBindings(fieldID: string) {
    return configEditorControls(state.manifest ?? {}).flatMap((control) => (control.settings ?? [])
        .filter((setting) => setting.id === fieldID || setting.key === fieldID)
        .map((setting) => ({ control, setting })));
}
export function elements<T extends Element = HTMLElement>(selector: string): T[] {
    const appRoot = document.querySelector("#app");
    if (!appRoot) {
        return [];
    }
    return [...appRoot.querySelectorAll<T>(selector)];
}
export function errorMessage(error: unknown): string {
    const message = error instanceof Error ? error.message : String(error);
    switch (message) {
        case "workspaceChoiceRequired":
        case "workspaceMoveBusy":
        case "workspaceMoveInProgress":
        case "workspaceMoveSettingsLocked":
        case "workspaceMoveStale":
        case "workspaceMoveProtectedFolder":
            return state.labels[message] ?? fallbackLabels[message] ?? message;
        default:
            return message;
    }
}
export function findControl(id: string): ControlSpec {
    const control = controlWithID(id);
    if (!control) {
        throw new Error(`Unknown control: ${id}`);
    }
    return control;
}
export function controlWithID(id: string): ControlSpec | undefined {
    for (const page of state.manifest?.pages ?? []) {
        for (const section of page.sections ?? []) {
            const control = (section.controls ?? []).find((candidate) => candidate.id === id);
            if (control)
                return control;
        }
    }
    return undefined;
}
export function displayOption(option: ControlOption): string {
    return optionTitle(option, state.labels);
}
export function localizedStatus(status: unknown): string {
    return state.labels.libraryStatusLabels?.[String(status).toLowerCase()] ?? String(status ?? "");
}
export function localizedTag(tag: RowTagSpec): string {
    return (tag.id ? state.labels.libraryTagLabels?.[tag.id] : undefined) ?? state.labels.libraryTagLabels?.[String(tag.title).toLowerCase()] ?? String(tag.title ?? "");
}
export function tagStyle(status: unknown): string {
    switch (String(status).toLowerCase()) {
        case "installed":
            return "success";
        case "unindexed":
        case "incomplete":
            return "warning";
        case "missing":
        case "unavailable":
            return "secondary";
        default:
            return "primary";
    }
}
export function buildTagStyle(build: unknown): string | undefined {
    const value = String(build ?? "").trim().toLowerCase();
    if (!value) {
        return undefined;
    }
    const is37 = value === "37" || value.includes("grch37") || value.includes("hg19") || value.includes("hg37") || value.includes("1k37") || value.includes("ebi37") || value.includes("ncb37");
    const is38 = value === "38" || value.includes("grch38") || value.includes("hg38") || value.includes("1k38") || value.includes("ebi38") || value.includes("ncb38");
    if (is37 && is38) {
        return "secondary";
    }
    if (is37) {
        return "primary";
    }
    if (is38) {
        return "success";
    }
    if (value.includes("t2t") || value.includes("chm13") || value.includes("pr1") || value.includes("hg002")) {
        return "warning";
    }
    return "secondary";
}
export function formatLabel(template: unknown, values: Record<string, unknown> = {}): string {
    return String(template ?? "").replace(/%\{([^}]+)\}/g, (_, key: string) => String(values[key] ?? ""));
}
export function renderTooltip(text: unknown): string {
    return text
        ? `<span class="tooltip" tabindex="0" role="button" aria-label="${escapeAttribute(text)}" data-tooltip="${escapeAttribute(text)}">i</span>`
        : "";
}
export function renderInlineError(message: unknown, accessory = ""): string {
    return `<div class="inline-error"><span aria-hidden="true">${renderIcon("exclamationmark.triangle.fill", undefined, "âš ")}</span><span>${renderTextWithDocLinks(message)}</span>${accessory}</div>`;
}
export function renderLoadingInline(message: unknown): string {
    return `<p class="loading-inline"><span class="spinner small" aria-hidden="true"></span>${escapeHTML(message)}</p>`;
}
export function renderLoadingBox(message: unknown): string {
    return `<div class="loading-box"><span class="spinner small" aria-hidden="true"></span>${escapeHTML(message)}</div>`;
}
export function renderIconTitle(title: unknown, iconName: unknown, textIcon: unknown, fallback = "â€¢"): string {
    return `<span class="icon-title"><span class="icon-title-icon" aria-hidden="true">${renderIcon(iconName, textIcon, fallback)}</span><span>${escapeHTML(title)}</span></span>`;
}
export function renderIcon(iconName: unknown, textIcon: unknown, fallback: string): string {
    const bootstrap = resolveIcon("bootstrap", iconName);
    const emoji = resolveIcon("emoji", iconName);
    const playIconClass = isPlayIcon(iconName, textIcon, fallback, bootstrap, emoji) ? " play-icon" : "";
    if (state.iconSet === "platform" && bootstrap) {
        return `<i class="bi bi-${escapeAttribute(bootstrap)} web-icon${playIconClass}" aria-hidden="true"></i>`;
    }
    if (textIcon) {
        return `<span class="emoji-icon${playIconClass}">${escapeHTML(textIcon)}</span>`;
    }
    if (state.iconSet === "emoji" && emoji) {
        return `<span class="emoji-icon${playIconClass}">${escapeHTML(emoji)}</span>`;
    }
    if (bootstrap) {
        return `<i class="bi bi-${escapeAttribute(bootstrap)} web-icon${playIconClass}" aria-hidden="true"></i>`;
    }
    return `<span class="emoji-icon${playIconClass}">${escapeHTML(fallback)}</span>`;
}
function resolveIcon(source: string, iconName: unknown): string | undefined {
    const key = String(iconName ?? "").trim();
    return key ? state.iconMap?.[source]?.[key] : undefined;
}
function isPlayIcon(iconName: unknown, textIcon: unknown, fallback: string, bootstrap: string | undefined, emoji: string | undefined): boolean {
    return iconName === "play" || iconName === "play.fill" || bootstrap === "play" || bootstrap === "play-fill" || emoji === "â–¶ï¸" || (!iconName && !textIcon && fallback === "â–¶");
}
export function resolveText(value: unknown, context: CommandContext): string {
    return String(value ?? "").replace(/\{\{([^}]+)\}\}/g, (_, raw) => {
        const placeholder = String(raw).trim();
        if (placeholder.startsWith("row."))
            return String(context.rowValues?.[placeholder.slice(4)] ?? "");
        if (placeholder.startsWith("config."))
            return String(context.configValues?.[placeholder.slice(7)] ?? "");
        return String(context.rowValues?.[placeholder] ?? context.fieldValues?.[placeholder] ?? context.configValues?.[placeholder] ?? "");
    });
}
