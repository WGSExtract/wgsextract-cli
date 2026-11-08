import { stat } from "node:fs/promises";
import path from "node:path";
import { readsLastResultDocument } from "../../../shared/last-result-documents.js";
import type { ActionSpec, LastResultDocumentPaths } from "../../../shared/types.js";
import type { ActionRunResult } from "./action-runner.js";
import { writeFileAtomically } from "./config-store.js";
import { errnoCode, errorMessage } from "./errors.js";

// Hidden, so syncing the bundle into its workspace never replaces or removes it.
const directoryName = ".last-results";

function documentPaths(bundleRoot: string): Required<LastResultDocumentPaths> {
    const directory = path.join(bundleRoot, directoryName);
    return { last: path.join(directory, "last.json"), lastFailed: path.join(directory, "last-failed.json") };
}

/** The saved documents that exist, for `{{lastResultDocumentPath}}` and `{{lastFailedResultDocumentPath}}`. */
export async function lastResultDocumentPaths(bundleRoot: string): Promise<LastResultDocumentPaths> {
    const existing: LastResultDocumentPaths = {};
    for (const [key, filePath] of Object.entries(documentPaths(bundleRoot)) as Array<[keyof LastResultDocumentPaths, string]>) {
        if (await isFile(filePath)) {
            existing[key] = filePath;
        }
    }
    return existing;
}

/**
 * Saves the document of an action run in the app as the last run, and as the last failed run when it failed.
 * Data sources and setup never come here, and an action reading these documents (a bug report) is not saved,
 * so they describe the last thing the user ran.
 */
export async function rememberLastResultDocument(bundleRoot: string, action: ActionSpec, result: ActionRunResult) {
    const document = result.resultDocument;
    if (!document || readsLastResultDocument(action)) {
        return;
    }
    const paths = documentPaths(bundleRoot);
    const contents = `${JSON.stringify(document.raw, null, 2)}\n`;
    try {
        await writeFileAtomically(paths.last, contents);
        if (result.exitCode !== 0 || document.status === "failed") {
            await writeFileAtomically(paths.lastFailed, contents);
        }
    }
    catch (error) {
        // The run itself finished; only a later bug report loses it.
        console.warn(`Could not save the last result document in ${path.dirname(paths.last)}: ${errorMessage(error)}`);
    }
}

async function isFile(filePath: string) {
    try {
        return (await stat(filePath)).isFile();
    }
    catch (error) {
        if (errnoCode(error) === "ENOENT" || errnoCode(error) === "ENOTDIR") {
            return false;
        }
        throw error;
    }
}
