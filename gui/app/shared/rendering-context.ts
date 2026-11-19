import { allControls, configEditorControls } from "./rendering-controls.js";
import { lastResultDocumentPlaceholders } from "./last-result-documents.js";
import { currentSetupPlatform, setupStepsForPlatform } from "./setup-platforms.js";
import { basicModeHiddenInputs, controlDefaultValue, normalizeUIMode } from "./ui-mode.js";
import type { BundleManifest, CommandContext, Labels, LastResultDocumentPaths, LooseRecord, SetupRun, StateValue, StringMap, ValueMap } from "./types.js";

type CheckedOptionsInput = Record<string, Set<string> | string[] | string | null | undefined>;
type StateLike = {
    fieldValues?: ValueMap;
    checkedOptions?: CheckedOptionsInput;
    configValues?: ValueMap;
    bundleRootPath?: string;
    usersWorkspacePath?: string;
    manifest?: BundleManifest | null;
    homePath?: string;
    lastResultDocumentPaths?: LastResultDocumentPaths;
    setupRun?: SetupRun | null;
    uiMode?: unknown;
} & LooseRecord;

export function commandContextFromState(state: StateLike, rowValues: ValueMap = {}, sectionValues: ValueMap = {}): CommandContext {
    const { fieldValues, checkedOptions, configValues } = valuesForUIMode(state);
    const context: CommandContext = {
        fieldValues: { ...fieldValues, ...sectionValues },
        checkedOptions,
        configValues: { ...configValues, ...fieldValues, ...sectionValues },
        rowValues,
        placeholderLabels: placeholderLabelsFromManifest(state.manifest),
    };
    if (state.bundleRootPath != null) {
        context.bundleRootPath = state.bundleRootPath;
    }
    if (state.usersWorkspacePath) {
        context.usersWorkspacePath = state.usersWorkspacePath;
    }
    if (state.homePath != null) {
        context.homePath = state.homePath;
    }
    if (state.lastResultDocumentPaths != null) {
        context.lastResultDocumentPaths = state.lastResultDocumentPaths;
    }
    if (setupToolsReady(state.manifest, state.setupRun)) {
        context.setupToolsReady = "true";
    }
    return context;
}
/**
 * Whether the last setup run installed every tool this platform's setup declares (its pathTool steps). A later
 * step's failure (a download, say) leaves the tools usable.
 */
function setupToolsReady(manifest: BundleManifest | null | undefined, setupRun: SetupRun | null | undefined): boolean {
    const toolIDs = setupStepsForPlatform(manifest?.setup?.steps ?? [])
        .filter((step) => step.kind === "pathTool")
        .map((step) => step.id);
    const statuses = new Map((setupRun?.results ?? []).map((result) => [result.id, result.status]));
    return toolIDs.length > 0 && toolIDs.every((id) => ["ok", "warning"].includes(statuses.get(id) ?? ""));
}
/** Basic mode runs commands with the controls it hides at their defaults, and the options it hides unchecked. */
function valuesForUIMode(state: StateLike): { fieldValues: ValueMap; checkedOptions: StringMap; configValues: ValueMap } {
    const fieldValues: ValueMap = { ...(state.fieldValues ?? {}) };
    const configValues: ValueMap = { ...(state.configValues ?? {}) };
    const checked: CheckedOptionsInput = { ...(state.checkedOptions ?? {}) };
    if (normalizeUIMode(state.uiMode) === "basic" && state.manifest) {
        const hidden = basicModeHiddenInputs(state.manifest);
        for (const [id, control] of hidden.controls) {
            if (control.kind === "checkboxGroup") {
                checked[id] = (control.options ?? []).filter((option) => option.selected).map((option) => option.id);
            }
            // Paths say where files are, not how a command runs, so a hidden one (the Reference Library) still applies.
            else if (control.kind !== "path" && Object.hasOwn(fieldValues, id)) {
                fieldValues[id] = controlDefaultValue(control);
            }
        }
        for (const [id, hiddenOptions] of hidden.options) {
            const selected = checked[id];
            if (selected instanceof Set || Array.isArray(selected)) {
                checked[id] = normalizeSelectedIDs(selected).filter((option) => !hiddenOptions.has(option));
            }
        }
        for (const control of configEditorControls(state.manifest)) {
            for (const setting of control.settings ?? []) {
                if (setting.advancedOnly !== true) {
                    continue;
                }
                for (const name of new Set([setting.id, setting.key ?? setting.id])) {
                    fieldValues[name] = "";
                    configValues[name] = "";
                    configValues[`${control.id}.${name}`] = "";
                    if (Object.hasOwn(checked, name)) {
                        checked[name] = [];
                    }
                }
            }
        }
    }
    return { fieldValues, checkedOptions: checkedOptionsForContext(checked), configValues };
}
function placeholderLabelsFromManifest(manifest: BundleManifest | null | undefined): Labels {
    const labels: Labels = {};
    for (const control of allControls(manifest ?? {})) {
        if (control.label) {
            labels[control.id] = control.label;
        }
        for (const setting of control.settings ?? []) {
            if (!setting.label) {
                continue;
            }
            labels[setting.id] = setting.label;
            if (setting.key) {
                labels[setting.key] = setting.label;
                labels[`${control.id}.${setting.key}`] = setting.label;
            }
            labels[`${control.id}.${setting.id}`] = setting.label;
        }
    }
    return labels;
}

export function contextValue(context: CommandContext, placeholder: string): StateValue {
    if (placeholder === "bundleRoot" || placeholder === "bundleWorkspace") {
        return context.bundleRootPath;
    }
    if (placeholder === "usersWorkspace") {
        return context.usersWorkspacePath || context.bundleRootPath;
    }
    if (placeholder === "home") {
        return context.homePath;
    }
    if (placeholder === "hostPlatform") {
        return currentSetupPlatform();
    }
    if (placeholder === "setupToolsReady") {
        return context.setupToolsReady;
    }
    if (placeholder === lastResultDocumentPlaceholders.last) {
        return context.lastResultDocumentPaths?.last;
    }
    if (placeholder === lastResultDocumentPlaceholders.lastFailed) {
        return context.lastResultDocumentPaths?.lastFailed;
    }
    if (placeholder.startsWith("row.")) {
        return context.rowValues?.[placeholder.slice(4)];
    }
    if (placeholder.startsWith("config.")) {
        return context.configValues?.[placeholder.slice(7)];
    }
    const computed = computedFileStateValue(context, placeholder);
    if (computed != null) {
        return computed;
    }
    return (context.rowValues?.[placeholder] ??
        context.checkedOptions?.[placeholder] ??
        context.fieldValues?.[placeholder] ??
        context.configValues?.[placeholder]);
}

export function checkedOptionsForContext(checkedOptions: CheckedOptionsInput): StringMap {
    return Object.fromEntries(Object.entries(checkedOptions).map(([key, selected]) => [
        key,
        selected instanceof Set || Array.isArray(selected)
            ? normalizeSelectedIDs(selected).sort().join(",")
            : selected == null
                ? ""
                : String(selected),
    ]));
}
export function normalizeSelectedIDs(value: Set<unknown> | unknown[] | unknown): string[] {
    if (value instanceof Set) {
        return [...value].map(String);
    }
    if (Array.isArray(value)) {
        return value.map(String);
    }
    return String(value ?? "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
}

function computedFileStateValue(context: CommandContext, placeholder: string): StateValue {
    const separator = placeholder.lastIndexOf(".");
    if (separator <= 0 || separator >= placeholder.length - 1) {
        return undefined;
    }
    const fieldID = placeholder.slice(0, separator);
    const property = placeholder.slice(separator + 1);
    const rawPath = context.fieldValues?.[fieldID] ?? context.configValues?.[fieldID];
    const serverComputed = context.fileStateValues?.[placeholder];
    if (serverComputed != null) {
        return serverComputed;
    }
    switch (property) {
        case "pathExtension": {
            const name = String(rawPath ?? "").split(/[\\/]/).pop() ?? "";
            const dot = name.lastIndexOf(".");
            return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
        }
        default:
            return undefined;
    }
}
