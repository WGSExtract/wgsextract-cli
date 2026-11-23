import { randomUUID } from "node:crypto";
import { mkdir, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { errnoCode } from "./errors.js";

// Windows refuses to replace a file another process has open for a moment (the CLI reading its
// config, an antivirus or indexer scanning it), and the rename fails with one of these.
const transientReplaceCodes = new Set(["EPERM", "EACCES", "EBUSY"]);
const replaceRetryDelaysMs = [10, 20, 40, 80, 160, 320, 640, 800];

async function renameReplacing(from: string, to: string, platform: NodeJS.Platform = process.platform) {
    for (let attempt = 0; ; attempt++) {
        try {
            await rename(from, to);
            return;
        }
        catch (error) {
            const delay = replaceRetryDelaysMs[attempt];
            if (platform !== "win32" || delay == null || !transientReplaceCodes.has(errnoCode(error) ?? "")) {
                throw error;
            }
            await new Promise((resolve) => setTimeout(resolve, delay));
        }
    }
}

/**
 * Writes via a sibling temp file and rename, so readers never observe a truncated file. Symlinks are
 * resolved first and an existing file's permissions are kept.
 */
export async function writeFileAtomically(filePath: string, contents: string) {
    const target = await realpath(filePath).catch((error) => {
        if (errnoCode(error) === "ENOENT") {
            return filePath;
        }
        throw error;
    });
    const existingMode = await stat(target).then((info) => info.mode & 0o7777, () => undefined);
    await mkdir(path.dirname(target), { recursive: true });
    const temporaryPath = `${target}.${process.pid}.${randomUUID()}.tmp`;
    try {
        await writeFile(temporaryPath, contents, { encoding: "utf8", ...(existingMode == null ? {} : { mode: existingMode }) });
        await renameReplacing(temporaryPath, target);
    }
    catch (error) {
        await rm(temporaryPath, { force: true });
        throw error;
    }
}
