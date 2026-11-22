import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { isDeepStrictEqual } from "node:util";
import { relocatePath } from "../../../shared/workspace-paths.js";
import { parseConfigToml as parseToml, serializeConfigToml as serializeToml } from "./config-toml.js";
import { writeFileAtomically } from "./config-store.js";
import { isErrnoCode } from "./errors.js";
import { withSettingsFileLock } from "./settings-lock.js";

/** A file's text from before it was rewritten, to put back if the move fails. */
export interface RewrittenFile {
    filePath: string;
    text: string;
    /** For a settings file the CLI also saves: the text written, to tell whether a save followed. */
    written?: string;
}

/** Points the paths into `from` in a settings file at `to`, noting the file in `rewritten` to undo. */
export async function relocateSettingsFile(filePath: string, from: string, to: string, rewritten: RewrittenFile[]) {
    if (!existsSync(filePath)) {
        return;
    }
    await withSettingsFileLock(filePath, async (target) => {
        const text = await readTextIfPresent(target);
        if (text != null) {
            const written = serializeToml(relocateStrings(parseToml(text), from, to));
            rewritten.push({ filePath: target, text, written });
            await writeFileAtomically(target, written);
        }
    });
}

/** Puts a rewritten file back. A settings file keeps any save made since: only the values the move wrote revert. */
export async function restoreRewrittenFile(file: RewrittenFile) {
    const { written } = file;
    if (written == null) {
        await writeFileAtomically(file.filePath, file.text);
        return;
    }
    await withSettingsFileLock(file.filePath, async (target) => {
        const current = await readTextIfPresent(target);
        const restored = current == null || current === written
            ? file.text
            : serializeToml(undoRewrite(parseToml(file.text), parseToml(written), parseToml(current)) as Record<string, unknown>);
        await writeFileAtomically(target, restored);
    });
}

export function relocateStrings(value: unknown, from: string, to: string): unknown {
    if (typeof value === "string") {
        return relocatePath(value, from, to);
    }
    if (Array.isArray(value)) {
        return value.map((item) => relocateStrings(item, from, to));
    }
    if (value != null && typeof value === "object") {
        return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, relocateStrings(item, from, to)]));
    }
    return value;
}

/** `current`, with each value that is still what the rewrite wrote put back as it was before. */
function undoRewrite(original: unknown, written: unknown, current: unknown): unknown {
    if (isDeepStrictEqual(current, written)) {
        return original;
    }
    if (Array.isArray(original) && Array.isArray(written) && Array.isArray(current)) {
        return current.map((item) => {
            const index = written.findIndex((candidate) => isDeepStrictEqual(candidate, item));
            return index < 0 ? item : original[index];
        });
    }
    if (isTable(original) && isTable(written) && isTable(current)) {
        return Object.fromEntries(Object.entries(current).map(([key, item]) => [
            key,
            Object.hasOwn(original, key) && Object.hasOwn(written, key) ? undoRewrite(original[key], written[key], item) : item,
        ]));
    }
    return current;
}

function isTable(value: unknown): value is Record<string, unknown> {
    return value != null && typeof value === "object" && !Array.isArray(value) && !(value instanceof Date);
}

async function readTextIfPresent(filePath: string): Promise<string | null> {
    try {
        return await readFile(filePath, "utf8");
    }
    catch (error) {
        if (isErrnoCode(error, "ENOENT")) {
            return null;
        }
        throw error;
    }
}
