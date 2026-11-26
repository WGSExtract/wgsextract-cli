import { api } from "./api.js";
import { bundlePickerDefaultPath, rememberBundlePickerPath } from "./bundle-picker-memory.js";
import { errorMessage } from "./model.js";
import { clearDataSourceCache, clearFileStateCache } from "./operations-data.js";
import { chooseLocalPath } from "./path-picker.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";
import { appendTerminal, resetTerminalEntries } from "./terminal.js";

/** Reloads the manifest and re-renders, optionally for another locale. */
export type Bootstrap = (locale?: string) => Promise<void>;

let bundleLoadInFlight = false;
let installedGlobalBundleLoadHandler = false;

export function installGlobalBundleLoadHandler(bootstrap: Bootstrap) {
    if (installedGlobalBundleLoadHandler) {
        return;
    }
    installedGlobalBundleLoadHandler = true;
    window.addEventListener("wgsextract-gui-load-bundle", () => {
        void loadBundleFromPicker(bootstrap);
    });
}

export async function loadBundleFromPicker(bootstrap: Bootstrap) {
    if (bundleLoadInFlight) {
        return;
    }
    bundleLoadInFlight = true;
    try {
        const selectedPath = await chooseLocalPath(
            {
                id: "bundle_root",
                label: "Bundle",
                pathKind: "bundle",
                defaultDirectory: bundlePickerDefaultPath(state.sourceRootPath || state.bundleRootPath || ""),
            },
            "",
        );
        if (!selectedPath) {
            return;
        }
        await api<void>("/api/bundle/load", {
            method: "POST",
            body: { path: selectedPath },
        });
        rememberBundlePickerPath(selectedPath);
        resetBundleClientState();
        await bootstrap();
        appendTerminal("config", `[bundle] Loaded ${state.manifest?.displayName ?? "bundle"}`, state.sourceRootPath);
        scheduleRender();
    }
    catch (error) {
        appendTerminal("error", "Could not load bundle", errorMessage(error));
        scheduleRender();
    }
    finally {
        bundleLoadInFlight = false;
    }
}

function resetBundleClientState() {
    state.activePageID = "";
    clearDataSourceCache();
    clearFileStateCache();
    state.actionPrechecks.clear();
    state.actionPrecheckErrors.clear();
    state.actionResultNotices.clear();
    state.loadingActionPrechecks.clear();
    state.setupRun = null;
    state.setupPreflight = null;
    state.setupPreflightError = "";
    state.setupPreflightKey = "";
    state.loadingSetupPreflight = false;
    state.setupPromptVisible = false;
    state.setupPromptDismissed = false;
    state.aboutDialogVisible = false;
    state.pendingConfirmation = null;
    state.pendingPrerequisites = null;
    state.pendingAlreadyRun = null;
    resetTerminalEntries();
}
