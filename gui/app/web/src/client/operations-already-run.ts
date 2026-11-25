import type { ActionReference, ActionSpec, CommandContext } from "../../../shared/types.js";
import { api } from "./api.js";
import { errorMessage } from "./model.js";
import { runAction } from "./operations-actions.js";
import { inSameBundle } from "./operations-prerequisites.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";

export type ExistingOutputCheck =
    | { kind: "none" }
    | { kind: "display"; cardPath?: string; openPath?: string; outputs?: string[]; finishedAt?: string }
    | { kind: "already-run"; outputs: string[]; finishedAt?: string };

/**
 * Checks whether `action` already has fresh permanent outputs on disk (newer than its inputs).
 * Returns null if the bundle changed mid-check; falls back to `{ kind: "none" }` on error.
 */
export async function checkExistingOutput(context: CommandContext, reference: ActionReference): Promise<ExistingOutputCheck | null> {
    return inSameBundle(async () => {
        try {
            return await api<ExistingOutputCheck>("/api/existing-output", { body: { action: reference, context } });
        }
        catch (error) {
            console.warn(`Existing output check failed; running action anyway: ${errorMessage(error)}`);
            return { kind: "none" };
        }
    });
}

export function holdBackAlreadyRun(action: ActionSpec, context: CommandContext, reference: ActionReference, outputs: string[], finishedAt?: string) {
    state.pendingAlreadyRun = { action, reference, context, outputs, ...(finishedAt ? { finishedAt } : {}) };
    scheduleRender();
}

export function cancelPendingAlreadyRun() {
    state.pendingAlreadyRun = null;
    scheduleRender();
}

export async function forceRunPendingAlreadyRun() {
    const pending = state.pendingAlreadyRun;
    state.pendingAlreadyRun = null;
    scheduleRender();
    if (pending) {
        await runAction(pending.action, pending.context, pending.reference, { skipExistingCheck: true, forceRerun: true });
    }
}
