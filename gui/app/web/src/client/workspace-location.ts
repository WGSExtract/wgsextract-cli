import { apiStream, api } from "./api.js";
import { errorMessage } from "./model.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";
import { appendTerminal } from "./terminal.js";
import type { PathPickResponse } from "../../../shared/types.js";
import { createModalFocusBinder } from "./modal-focus.js";

const bindSettingMoveFocus = createModalFocusBinder("[data-setting-move-dialog]", "[data-workspace-location-confirm]", false);

function cancelSettingMove() {
    if (state.workspaceMove?.running) {
        return;
    }
    state.workspaceMove = null;
    saveSettingOnly = null;
    scheduleRender();
}

type WorkspaceMoveEvent =
    | { type: "progress"; percent: number }
    | { type: "complete" }
    | { type: "error"; error: string };

/** What the setting-only button does: save the new folder without moving what is in the old one. */
let saveSettingOnly: (() => Promise<void>) | null = null;

export function bindWorkspaceLocationEvents(app: HTMLElement) {
    app.querySelector<HTMLInputElement>("[data-workspace-location-input]")?.addEventListener("change", (event) => {
        state.workspaceLocationDraft = (event.currentTarget as HTMLInputElement).value.trim();
        scheduleRender();
    });
    app.querySelector("[data-workspace-location-choose]")?.addEventListener("click", async () => {
        await chooseWorkspaceLocation();
    });
    app.querySelector("[data-workspace-location-move]")?.addEventListener("click", () => {
        askToMoveWorkspace(state.workspaceLocationDraft ?? "");
    });
    app.querySelector("[data-workspace-location-reset]")?.addEventListener("click", () => {
        askToMoveWorkspace(null);
    });
    app.querySelectorAll("[data-workspace-location-cancel]").forEach((button) => button.addEventListener("click", () => {
        cancelSettingMove();
    }));
    app.querySelectorAll("[data-workspace-location-confirm]").forEach((button) => button.addEventListener("click", async () => {
        await moveWorkspace();
    }));
    app.querySelector("[data-workspace-location-setting-only]")?.addEventListener("click", async () => {
        const save = saveSettingOnly;
        state.workspaceMove = null;
        saveSettingOnly = null;
        scheduleRender();
        await save?.();
    });
    bindSettingMoveFocus(app, cancelSettingMove);
}

async function chooseWorkspaceLocation() {
    const location = state.workspaceLocation;
    try {
        const result = await api<PathPickResponse>("/api/path/pick", {
            method: "POST",
            body: {
                kind: "directory",
                title: state.labels.workspaceLocationFolderLabel,
                defaultPath: state.workspaceLocationDraft || location?.workspaceRoot || location?.defaultRoot || "",
            },
        });
        if (!result.cancelled && result.path) {
            state.workspaceLocationDraft = result.path;
            scheduleRender();
        }
    }
    catch (error) {
        reportFailure(errorMessage(error));
    }
}

function askToMoveWorkspace(folder: string | null) {
    if (folder !== "") {
        state.workspaceMove = { folder, running: false, percent: 0 };
        scheduleRender();
    }
}

/**
 * Asks whether to move what is in a path setting's folder (`from`) to the one the user just picked; until they
 * answer, the setting keeps its value. `save` saves the new folder alone.
 */
export function askToMoveSettingFolder(controlID: string, key: string, from: string, folder: string, save: () => Promise<void>) {
    state.workspaceMove = { folder, setting: { controlID, key, from }, running: false, percent: 0 };
    saveSettingOnly = save;
    scheduleRender();
}

async function moveWorkspace() {
    const move = state.workspaceMove;
    if (!move) {
        return;
    }
    move.running = true;
    scheduleRender();
    let failure: string | null = null;
    const body = move.setting ? { folder: move.folder, controlID: move.setting.controlID, key: move.setting.key } : { folder: move.folder };
    try {
        await apiStream<WorkspaceMoveEvent>("/api/workspace-location/move", body, (event) => {
            if (event.type === "progress") {
                move.percent = event.percent;
                scheduleRender();
            }
            else if (event.type === "error") {
                failure = event.error;
            }
        }, { label: "Workspace move" });
        // Everything the page holds (paths, settings, setup status) came from the old location.
        window.location.reload();
    }
    catch (error) {
        state.workspaceMove = null;
        saveSettingOnly = null;
        reportFailure(failure ?? errorMessage(error));
    }
}

function reportFailure(message: string) {
    appendTerminal("error", state.labels.workspaceLocationTitle, message);
    scheduleRender();
}
