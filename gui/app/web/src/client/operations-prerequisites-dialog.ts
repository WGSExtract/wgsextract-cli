import type { PendingPrerequisites } from "../../../shared/types.js";
import { runAction, streamCommand } from "./operations-actions.js";
import { formatLabel } from "./model.js";
import { missingPrerequisites } from "./operations-prerequisites.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";

/** The dialog's "Install and Continue": installs each missing item in turn, then runs the action once nothing is missing. */
export async function installPendingPrerequisites() {
    const pending = takePending();
    if (!pending) {
        return;
    }
    for (const item of pending.missing) {
        if (!item.installArguments) {
            continue;
        }
        const installAction = {
            title: formatLabel(state.labels.prerequisitesInstallTitleFormat, { name: item.name }),
            command: { executable: state.manifest?.prerequisites?.command.executable ?? "", arguments: item.installArguments },
        };
        const exitCode = await streamCommand(installAction, pending.context, "/api/prerequisites/install/stream", { action: pending.reference, context: pending.context, id: item.id });
        if (exitCode !== 0) {
            return; // The install's terminal tab says what went wrong; the action doesn't run without its data.
        }
    }
    await runIfInstalled(pending);
}

/** The dialog's "Continue", after the user installed the data themselves: runs the action if it is there now. */
export async function continuePendingPrerequisites() {
    const pending = takePending();
    if (pending) {
        await runIfInstalled(pending);
    }
}

export function cancelPendingPrerequisites() {
    state.pendingPrerequisites = null;
    scheduleRender();
}

function takePending(): PendingPrerequisites | null {
    const pending = state.pendingPrerequisites;
    state.pendingPrerequisites = null;
    scheduleRender();
    return pending;
}

async function runIfInstalled(pending: PendingPrerequisites) {
    const missing = await missingPrerequisites(pending.context, pending.reference);
    if (missing.length > 0) {
        state.pendingPrerequisites = { ...pending, missing, stillMissing: true };
        scheduleRender();
        return;
    }
    await runAction(pending.action, pending.context, pending.reference, { prerequisitesChecked: true });
}
