import { readFile, rename } from "node:fs/promises";
import path from "node:path";
import type { LooseRecord, SetupRun, StringMap, ValueMap } from "../../../shared/types.js";
import { normalizeUIMode, type UIMode } from "../../../shared/ui-mode.js";
import { writeFileAtomically } from "./atomic-file.js";
import { errnoCode, errorMessage } from "./errors.js";

/** The per-bundle UI state persisted in the workspace's state.json. */
export type BundleState = {
    localizationCode: string | null;
    configFilePaths: StringMap;
    fieldValues: ValueMap;
    checkedOptions: Record<string, string[]>;
    selectedPageID: string | null;
    setupRun: SetupRun | null;
    usersWorkspaceAdopted?: true;
    iconSet: "emoji" | "platform";
    colorTheme: "light" | "dark" | "system";
    webUIFont: "sfPro" | "system";
    uiMode: UIMode;
};

/** A partial state update; appearance values are normalized and setup runs are stored as given. */
export type BundleStateUpdate = {
    [Key in Exclude<keyof BundleState, "iconSet" | "colorTheme" | "webUIFont" | "uiMode" | "setupRun">]?: BundleState[Key];
} & {
    setupRun?: SetupRun | LooseRecord | null;
    iconSet?: unknown;
    colorTheme?: unknown;
    webUIFont?: unknown;
    uiMode?: unknown;
};

export function normalizeIconSet(value: unknown): BundleState["iconSet"] {
    return value === "emoji" ? "emoji" : "platform";
}
export function normalizeColorTheme(value: unknown): BundleState["colorTheme"] {
    return value === "light" || value === "dark" ? value : "system";
}
export function normalizeWebUIFont(value: unknown): BundleState["webUIFont"] {
    return value === "sfPro" ? "sfPro" : "system";
}
export function bundleStatePath(bundleRoot: string) {
    return path.join(bundleRoot, "state.json");
}
export function emptyBundleState(): BundleState {
    return {
        localizationCode: null,
        configFilePaths: {},
        fieldValues: {},
        checkedOptions: {},
        selectedPageID: null,
        setupRun: null,
        iconSet: "platform",
        colorTheme: "system",
        webUIFont: "system",
        uiMode: "basic",
    };
}
let saveBundleStateQueue: Promise<unknown> = Promise.resolve();
const bundleStateWarnings = new Map<string, string>();

/**
 * Reads state.json. A file that does not parse is moved aside (never silently overwritten) and a warning
 * is queued for `takeBundleStateWarning`, so the UI can tell the user their saved state was reset.
 */
export async function loadBundleState(bundleRoot: string): Promise<BundleState> {
    const statePath = bundleStatePath(bundleRoot);
    let text: string;
    try {
        text = await readFile(statePath, "utf8");
    }
    catch (error) {
        if (errnoCode(error) === "ENOENT") {
            return emptyBundleState();
        }
        throw error;
    }
    let state: Partial<BundleState>;
    try {
        const parsed: unknown = JSON.parse(text);
        if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("expected a JSON object");
        }
        state = parsed;
    }
    catch (error) {
        const backupPath = `${statePath}.corrupt-${new Date().toISOString().replaceAll(":", "-")}`;
        await rename(statePath, backupPath);
        const warning = `Saved app state could not be read (${errorMessage(error)}); it was reset and the old file was kept at ${backupPath}.`;
        console.warn(warning);
        bundleStateWarnings.set(bundleRoot, warning);
        return emptyBundleState();
    }
    return {
        localizationCode: state.localizationCode ?? null,
        configFilePaths: state.configFilePaths ?? {},
        fieldValues: state.fieldValues ?? {},
        checkedOptions: state.checkedOptions ?? {},
        selectedPageID: state.selectedPageID ?? null,
        setupRun: state.setupRun ?? null,
        ...(state.usersWorkspaceAdopted === true ? { usersWorkspaceAdopted: true } : {}),
        iconSet: normalizeIconSet(state.iconSet),
        colorTheme: normalizeColorTheme(state.colorTheme),
        webUIFont: normalizeWebUIFont(state.webUIFont),
        uiMode: normalizeUIMode(state.uiMode),
    };
}
export function takeBundleStateWarning(bundleRoot: string): string | undefined {
    const warning = bundleStateWarnings.get(bundleRoot);
    bundleStateWarnings.delete(bundleRoot);
    return warning;
}
export async function saveBundleState(partialState: BundleStateUpdate, bundleRoot: string): Promise<BundleState> {
    const saveOperation = saveBundleStateQueue.then(async () => {
        const current = await loadBundleState(bundleRoot);
        const next: BundleState = {
            localizationCode: Object.hasOwn(partialState, "localizationCode") ? partialState.localizationCode ?? null : current.localizationCode,
            configFilePaths: partialState.configFilePaths ?? current.configFilePaths,
            fieldValues: partialState.fieldValues ?? current.fieldValues,
            checkedOptions: partialState.checkedOptions ?? current.checkedOptions,
            selectedPageID: Object.hasOwn(partialState, "selectedPageID") ? partialState.selectedPageID ?? null : current.selectedPageID,
            // Setup runs are validated as objects at the request boundary and stored as reported.
            setupRun: Object.hasOwn(partialState, "setupRun") ? (partialState.setupRun ?? null) as SetupRun | null : current.setupRun,
            ...(partialState.usersWorkspaceAdopted || current.usersWorkspaceAdopted ? { usersWorkspaceAdopted: true } : {}),
            iconSet: Object.hasOwn(partialState, "iconSet") ? normalizeIconSet(partialState.iconSet) : current.iconSet,
            colorTheme: Object.hasOwn(partialState, "colorTheme")
                ? normalizeColorTheme(partialState.colorTheme)
                : current.colorTheme,
            webUIFont: Object.hasOwn(partialState, "webUIFont") ? normalizeWebUIFont(partialState.webUIFont) : current.webUIFont,
            uiMode: Object.hasOwn(partialState, "uiMode") ? normalizeUIMode(partialState.uiMode) : current.uiMode,
        };
        await writeFileAtomically(bundleStatePath(bundleRoot), `${JSON.stringify(next, null, 2)}\n`);
        return next;
    });
    saveBundleStateQueue = saveOperation.then(() => undefined, () => undefined);
    return saveOperation;
}
