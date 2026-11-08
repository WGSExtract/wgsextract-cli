import type { ActionSpec } from "./types.js";

/** Placeholders naming the app's saved copies of earlier runs' result documents (docs/result-documents.md). */
export const lastResultDocumentPlaceholders = {
    last: "lastResultDocumentPath",
    lastFailed: "lastFailedResultDocumentPath",
} as const;

const lastResultDocumentPattern = new RegExp(`\\{\\{\\s*(?:${Object.values(lastResultDocumentPlaceholders).join("|")})\\s*\\}\\}`);
const lastResultDocumentNames: string[] = Object.values(lastResultDocumentPlaceholders);

/**
 * Whether the action reports on an earlier run: its command or its conditions refer to a saved
 * result document. Its own run must not become the last one.
 */
export function readsLastResultDocument(action: ActionSpec): boolean {
    const command = action.command;
    const commandValues = command ? [command.executable, ...(command.arguments ?? []), ...(command.optionalArguments ?? []).flat()] : [];
    return commandValues.some((value) => lastResultDocumentPattern.test(String(value ?? "")))
        || [...(action.visibleWhen ?? []), ...(action.disabledWhen ?? [])].some((condition) => lastResultDocumentNames.includes(condition.placeholder));
}
