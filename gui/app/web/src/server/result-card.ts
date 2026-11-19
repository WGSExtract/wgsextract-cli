import { open } from "node:fs/promises";
import path from "node:path";
import { errnoCode, HttpError } from "./errors.js";
import type { ResultPaths } from "./result-paths.js";

/** The name `wgsextract --card` gives a result card (docs/result-manifest.md). */
export const resultCardSuffix = ".card.html";
/** Cards are a few kilobytes; a command that printed a very long report still fits. */
const maximumCardBytes = 8 * 1024 * 1024;

export interface ResultCard {
    path: string;
    name: string;
    html: string;
}

/**
 * Reads a result card the page may show: a `.card.html` file listed in a result document this server read.
 * The page shows it in a sandboxed frame without scripts, so the HTML is returned as text.
 */
export async function readResultCard(filePath: string, paths: ResultPaths, cwd: string): Promise<ResultCard> {
    const absolutePath = path.resolve(cwd, filePath);
    if (!absolutePath.endsWith(resultCardSuffix) || !paths.has(absolutePath)) {
        throw new HttpError(403, "Only result cards listed in an action's result can be shown.");
    }
    let handle;
    try {
        handle = await open(absolutePath, "r");
    }
    catch (error) {
        if (errnoCode(error) === "ENOENT" || errnoCode(error) === "ENOTDIR") {
            throw new HttpError(404, `File not found: ${filePath}`);
        }
        throw error;
    }
    try {
        const info = await handle.stat();
        if (!info.isFile()) {
            throw new HttpError(404, `File not found: ${filePath}`);
        }
        if (info.size > maximumCardBytes) {
            throw new HttpError(413, `The result card is too large to show: ${filePath}`);
        }
        return { path: absolutePath, name: path.basename(absolutePath), html: await handle.readFile("utf8") };
    }
    finally {
        await handle.close();
    }
}
