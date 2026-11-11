import type { ActionReference, ActionSpec, CommandContext, MissingPrerequisite } from "../../../shared/types.js";
import { api } from "./api.js";
import { errorMessage } from "./model.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";

/** What the action needs that isn't installed, from the bundle's prerequisite check; nothing when the check fails. */
export async function missingPrerequisites(context: CommandContext, reference: ActionReference): Promise<MissingPrerequisite[]> {
    state.checkingPrerequisites = true;
    scheduleRender();
    try {
        const result = await api<{ missing?: MissingPrerequisite[] }>("/api/prerequisites", { body: { action: reference, context } });
        return result.missing ?? [];
    }
    catch (error) {
        console.warn(`Prerequisite check failed; running the action anyway: ${errorMessage(error)}`);
        return [];
    }
    finally {
        state.checkingPrerequisites = false;
        scheduleRender();
    }
}

/**
 * Whether the action may run now: true when it needs nothing that is missing. Otherwise it is held back and the
 * prerequisites dialog asks the user to install what it needs, or to cancel.
 */
export async function prerequisitesInstalled(action: ActionSpec, context: CommandContext, reference: ActionReference): Promise<boolean> {
    const missing = await missingPrerequisites(context, reference);
    if (missing.length === 0) {
        return true;
    }
    state.pendingPrerequisites = { action, reference, context, missing, stillMissing: false };
    scheduleRender();
    return false;
}
