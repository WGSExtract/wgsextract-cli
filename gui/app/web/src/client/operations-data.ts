import { checkedOptionsForContext } from "../../../shared/rendering.js";
import { api } from "./api.js";
import { errorMessage } from "./model.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";
import type { ActionReference, ActionSpec, CommandContext, DataSourcePayload, FileStateResponse, ManifestLocation, PrecheckResult } from "../../../shared/types.js";

const precheckLabelKeys = [
    "actionPrecheckDiskSpaceTitle",
    "actionPrecheckDiskSpaceInfoTitle",
    "actionPrecheckDiskSpaceMessageFormat",
    "actionPrecheckDiskSpaceInfoFormat",
];

/** The localized strings a precheck message is built from. */
export function precheckLabels() {
    return Object.fromEntries(precheckLabelKeys.flatMap((key) => {
        const value = state.labels[key];
        return typeof value === "string" ? [[key, value]] : [];
    }));
}

let dataSourceGeneration = 0;
/** Bumped when one data source is dropped, so a load already under way for it can't refill the cache. */
const keyGenerations = new Map<string, number>();

export function clearDataSourceCache() {
    dataSourceGeneration += 1;
    state.dataSourcePayloads.clear();
    state.dataSourceErrors.clear();
    state.loadingDataSources.clear();
}

/** Drops the cached results (and errors and loads under way) of the data sources `drop` picks, so they load again. */
export function invalidateDataSources(drop: (key: string) => boolean) {
    const keys = new Set([...state.dataSourcePayloads.keys(), ...state.dataSourceErrors.keys(), ...state.loadingDataSources]);
    for (const key of keys) {
        if (drop(key)) {
            keyGenerations.set(key, (keyGenerations.get(key) ?? 0) + 1);
            state.dataSourcePayloads.delete(key);
            state.dataSourceErrors.delete(key);
            state.loadingDataSources.delete(key);
        }
    }
}

export function ensureDataSource(key: string, source: ManifestLocation, context: CommandContext) {
    if (state.dataSourcePayloads.has(key) || state.dataSourceErrors.has(key) || state.loadingDataSources.has(key)) {
        return;
    }
    // Data sources may need what setup installs; runSetup clears them when it ends, which loads them again.
    if (state.setupRun?.status === "running") {
        return;
    }
    state.dataSourceErrors.delete(key);
    state.loadingDataSources.add(key);
    const generation = dataSourceGeneration;
    const keyGeneration = keyGenerations.get(key) ?? 0;
    const current = () => generation === dataSourceGeneration && keyGeneration === (keyGenerations.get(key) ?? 0);
    api<DataSourcePayload>("/api/datasource", { method: "POST", body: { source, context } })
        .then((payload) => {
        if (!current()
            || Object.keys(payload.displayPaths ?? {}).some((id) => state.fieldValues[id] !== context.fieldValues?.[id])) {
            return;
        }
        state.dataSourcePayloads.set(key, payload);
        selectDefaultDataSourceOption(key, payload);
        state.dataSourceErrors.delete(key);
    })
        .catch((error) => {
        if (current()) {
            state.dataSourceErrors.set(key, errorMessage(error));
        }
    })
        .finally(() => {
        if (current()) {
            state.loadingDataSources.delete(key);
            scheduleRender();
        }
    });
}
export function ensureActionPrecheck(key: string, reference: ActionReference, context: CommandContext) {
    if (!key || state.actionPrechecks.has(key) || state.actionPrecheckErrors.has(key) || state.loadingActionPrechecks.has(key)) {
        return state.actionPrechecks.get(key) ?? null;
    }
    state.actionPrecheckErrors.delete(key);
    state.loadingActionPrechecks.add(key);
    api<PrecheckResult>("/api/precheck", {
        method: "POST",
        body: { target: { kind: "action", ...reference }, context, labels: precheckLabels() },
    })
        .then((result) => {
        state.actionPrechecks.set(key, result);
        state.actionPrecheckErrors.delete(key);
    })
        .catch((error) => {
        state.actionPrecheckErrors.set(key, errorMessage(error));
    })
        .finally(() => {
        state.loadingActionPrechecks.delete(key);
        scheduleRender();
    });
    return null;
}
export function retryDataSource(key: string) {
    state.dataSourceErrors.delete(key);
    scheduleRender();
}
export function retryActionPrecheck(key: string) {
    state.actionPrecheckErrors.delete(key);
    scheduleRender();
}
let fileStateGeneration = 0;

export function clearFileStateCache() {
    fileStateGeneration += 1;
    state.fileStateValues.clear();
    state.loadingFileStates.clear();
}

export function contextWithFileState(context: CommandContext): CommandContext {
    const key = fileStateKey(context);
    ensureFileState(key, context);
    const fileStateValues = state.fileStateValues.get(key);
    return fileStateValues ? { ...context, fileStateValues } : context;
}
function ensureFileState(key: string, context: CommandContext) {
    if (!key || state.fileStateValues.has(key) || state.loadingFileStates.has(key)) {
        return;
    }
    const generation = fileStateGeneration;
    state.loadingFileStates.add(key);
    api<FileStateResponse>("/api/file-state", { method: "POST", body: { context } })
        .then((result) => {
        if (generation !== fileStateGeneration) {
            return;
        }
        state.fileStateValues.set(key, result.values ?? {});
    })
        .catch((error) => {
        if (generation !== fileStateGeneration) {
            return;
        }
        console.warn(`Could not resolve file state: ${errorMessage(error)}`);
        state.fileStateValues.set(key, {});
    })
        .finally(() => {
        if (generation !== fileStateGeneration) {
            return;
        }
        state.loadingFileStates.delete(key);
        scheduleRender();
    });
}
function fileStateKey(context: CommandContext) {
    return JSON.stringify({
        fieldValues: context.fieldValues,
        configValues: context.configValues,
        rowValues: context.rowValues,
        bundleRootPath: context.bundleRootPath,
    });
}
export function actionPrecheckKey(action: ActionSpec, context: CommandContext) {
    return JSON.stringify({
        actionID: action.id,
        precheck: action.precheck,
        fieldValues: context.fieldValues,
        checkedOptions: checkedOptionsForContext(context.checkedOptions ?? {}),
        configValues: context.configValues,
        rowValues: context.rowValues,
        bundleRootPath: context.bundleRootPath,
    });
}
export function selectDefaultDataSourceOption(key: string, payload: DataSourcePayload) {
    const options = payload.options;
    if (!options?.length) {
        return;
    }
    const defaultValue = (options.find((option) => option.selected) ?? options[0])?.id ?? "";
    if (key.startsWith("control:")) {
        const controlID = key.slice("control:".length);
        const current = String(state.fieldValues[controlID] ?? "").trim();
        if (!current || !options.some((option) => option.id === current)) {
            state.fieldValues[controlID] = defaultValue;
        }
        return;
    }
    if (key.startsWith("setting:")) {
        const configKey = key.slice("setting:".length);
        const current = String(state.configValues[configKey] ?? "").trim();
        if (!current || !options.some((option) => option.id === current)) {
            state.configValues[configKey] = defaultValue;
        }
    }
}
