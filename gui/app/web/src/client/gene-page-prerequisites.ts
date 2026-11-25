import type { CommandContext, MissingPrerequisite } from "../../../shared/types.js";
import { api } from "./api.js";
import { errorMessage, formatLabel } from "./model.js";
import { streamCommand } from "./operations-actions.js";
import { state } from "./state.js";

export interface GenePageInputs {
    gene: string;
    alignment: string;
    vcf: string;
    minDepth: number | null;
}

export interface GenePageProblem {
    problem: string;
    fileName?: string;
    message?: string;
}

export interface PendingGenePrerequisites {
    inputs: GenePageInputs;
    missing: MissingPrerequisite[];
    stillMissing: boolean;
}

export interface GenePrerequisitesCallbacks {
    onChecking: (checking: boolean) => void;
    onPending: (pending: PendingGenePrerequisites | null) => void;
    onInstallStart: (title: string) => void;
    onInstallOutput: (text: string) => void;
    onInstallEnd: (succeeded: boolean) => void;
    onProblem: (problem: GenePageProblem) => void;
    onReady: (inputs: GenePageInputs) => void | Promise<void>;
}

type CheckResponse = { missing?: MissingPrerequisite[] } | GenePageProblem;

const emptyContext: CommandContext = { fieldValues: {}, checkedOptions: {}, configValues: {}, rowValues: {} };

/**
 * Prerequisites flow for the Gene page, mirroring operations-prerequisites-dialog.ts:
 * Cancel drops the request, Continue re-checks after manual install, and Install and Continue
 * installs each still-missing item before loading the gene.
 */
export function createGenePrerequisitesController(callbacks: GenePrerequisitesCallbacks) {
    let pending: PendingGenePrerequisites | null = null;
    let activeController: AbortController | undefined;
    let disposed = false;

    const setPending = (next: PendingGenePrerequisites | null) => {
        pending = next;
        callbacks.onPending(next);
    };

    const startRequest = (): AbortController => {
        activeController?.abort();
        const controller = new AbortController();
        activeController = controller;
        return controller;
    };

    const isCurrent = (controller: AbortController) => !disposed && !controller.signal.aborted && activeController === controller;

    const checkMissing = async (inputs: GenePageInputs, controller: AbortController): Promise<{ missing: MissingPrerequisite[] } | GenePageProblem | null> => {
        callbacks.onChecking(true);
        try {
            const result = await api<CheckResponse>("/api/gene/prerequisites", { body: inputs, signal: controller.signal });
            if (!isCurrent(controller)) return null;
            if ("problem" in result) return result;
            return { missing: result.missing ?? [] };
        }
        catch (error) {
            if (!isCurrent(controller)) return null;
            console.warn(`Gene page prerequisite check failed; running it anyway: ${errorMessage(error)}`);
            return { missing: [] };
        }
        finally {
            if (isCurrent(controller)) {
                callbacks.onChecking(false);
            }
        }
    };

    const runIfInstalled = async (inputs: GenePageInputs, controller: AbortController, afterRetry: boolean) => {
        const result = await checkMissing(inputs, controller);
        if (!result || !isCurrent(controller)) return;
        if ("problem" in result) {
            callbacks.onProblem(result);
            return;
        }
        if (result.missing.length > 0) {
            setPending({ inputs, missing: result.missing, stillMissing: afterRetry });
            return;
        }
        await callbacks.onReady(inputs);
    };

    return {
        get pending() {
            return pending;
        },
        async run(inputs: GenePageInputs) {
            setPending(null);
            if (!state.manifest?.prerequisites) {
                await callbacks.onReady(inputs);
                return;
            }
            const controller = startRequest();
            await runIfInstalled(inputs, controller, false);
        },
        cancel() {
            activeController?.abort();
            activeController = undefined;
            callbacks.onChecking(false);
            setPending(null);
        },
        async continue() {
            const current = pending;
            if (!current) return;
            setPending(null);
            const controller = startRequest();
            await runIfInstalled(current.inputs, controller, true);
        },
        async install() {
            const current = pending;
            if (!current) return;
            setPending(null);
            const controller = startRequest();
            const checked = await checkMissing(current.inputs, controller);
            if (!checked || !isCurrent(controller)) return;
            if ("problem" in checked) {
                callbacks.onProblem(checked);
                return;
            }
            let ranInstall = false;
            for (const item of checked.missing) {
                if (!item.installArguments) {
                    continue;
                }
                ranInstall = true;
                const title = formatLabel(state.labels.prerequisitesInstallTitleFormat, { name: item.name });
                callbacks.onInstallStart(title);
                const installAction = {
                    title,
                    command: { executable: state.manifest?.prerequisites?.command.executable ?? "", arguments: item.installArguments },
                };
                const exitCode = await streamCommand(
                    installAction,
                    emptyContext,
                    "/api/gene/prerequisites/install/stream",
                    { ...current.inputs, id: item.id },
                    null,
                    {
                        signal: controller.signal,
                        onOutput: (text) => {
                            if (isCurrent(controller)) callbacks.onInstallOutput(text);
                        },
                    },
                );
                if (!isCurrent(controller)) return;
                if (exitCode !== 0) {
                    callbacks.onInstallEnd(false);
                    return;
                }
            }
            if (ranInstall) {
                callbacks.onInstallEnd(true);
            }
            await runIfInstalled(current.inputs, controller, true);
        },
        dispose() {
            disposed = true;
            activeController?.abort();
            activeController = undefined;
            pending = null;
        },
    };
}
