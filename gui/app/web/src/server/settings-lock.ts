import { randomUUID } from "node:crypto";
import { mkdir, open, readFile, realpath, stat, unlink } from "node:fs/promises";
import { hostname } from "node:os";
import path from "node:path";
import { errnoCode } from "./errors.js";

// The CLI saves the same settings files. Node has no flock, so both take an O_EXCL lock file beside the
// real file; src/wgsextract_cli/core/file_lock.py (shared_file_lock) mirrors this protocol.
const staleAfterMs = 30_000;
const pollMs = 20;

/**
 * Runs one read-modify-write of `filePath` while holding `.<name>.lock` beside the real file behind it, and
 * passes that real path to `action`. Every alias of the file takes the same lock, as do the CLI's saves.
 */
export async function withSettingsFileLock<T>(filePath: string, action: (target: string) => Promise<T>, timeoutMs = 60_000): Promise<T> {
    const target = await realPathAllowingMissing(path.resolve(filePath));
    await mkdir(path.dirname(target), { recursive: true });
    const lock = path.join(path.dirname(target), `.${path.basename(target)}.lock`);
    const record = JSON.stringify({ pid: process.pid, host: hostname(), token: randomUUID() });
    const deadline = Date.now() + timeoutMs;
    while (!await tryCreate(lock, record)) {
        if (Date.now() >= deadline) {
            throw new Error(`Timed out waiting for ${lock}: another WGS Extract window or command is saving this file. If none is, delete the lock and try again.`);
        }
        await delay(pollMs);
    }
    try {
        return await action(target);
    }
    finally {
        await release(lock, record);
    }
}

async function realPathAllowingMissing(filePath: string): Promise<string> {
    try {
        return await realpath(filePath);
    }
    catch (error) {
        const parent = path.dirname(filePath);
        if (errnoCode(error) !== "ENOENT" || parent === filePath) {
            throw error;
        }
        return path.join(await realPathAllowingMissing(parent), path.basename(filePath));
    }
}

/** Windows refuses to create or delete a file another process is deleting or reading. */
function windowsBusy(error: unknown) {
    return process.platform === "win32" && ["EPERM", "EACCES", "EBUSY"].includes(errnoCode(error) ?? "");
}

async function tryCreate(lock: string, record: string): Promise<boolean> {
    let handle;
    try {
        handle = await open(lock, "wx");
    }
    catch (error) {
        if (errnoCode(error) === "EEXIST") {
            await reclaimIfAbandoned(lock);
            return false;
        }
        if (windowsBusy(error)) {
            return false;
        }
        throw error;
    }
    try {
        await handle.writeFile(record, "utf8");
    }
    catch (error) {
        await handle.close();
        await unlink(lock).catch(() => undefined);
        throw error;
    }
    await handle.close();
    return true;
}

async function reclaimIfAbandoned(lock: string) {
    let record: string;
    let ageMs: number;
    try {
        record = await readFile(lock, "utf8");
        ageMs = Date.now() - (await stat(lock)).mtimeMs;
    }
    catch (error) {
        if (errnoCode(error) === "ENOENT" || windowsBusy(error)) {
            return;
        }
        throw error;
    }
    if (!held(record, ageMs) && await remove(lock, record)) {
        console.warn(`Removed abandoned lock ${lock}`);
    }
}

function held(record: string, ageMs: number) {
    if (ageMs > staleAfterMs) {
        return false;
    }
    let owner: { pid?: unknown; host?: unknown };
    try {
        owner = JSON.parse(record) as { pid?: unknown; host?: unknown };
    }
    catch {
        return true; // its holder is still writing it
    }
    if (typeof owner.pid !== "number" || typeof owner.host !== "string" || owner.host.toLowerCase() !== hostname().toLowerCase()) {
        return true;
    }
    try {
        process.kill(owner.pid, 0);
        return true;
    }
    catch (error) {
        return errnoCode(error) === "EPERM";
    }
}

/** Deletes `lock` if it still holds `record`; false if the file is busy. */
async function remove(lock: string, record: string): Promise<boolean> {
    try {
        if (await readFile(lock, "utf8") === record) {
            await unlink(lock);
        }
        return true;
    }
    catch (error) {
        if (errnoCode(error) === "ENOENT") {
            return true;
        }
        if (windowsBusy(error)) {
            return false;
        }
        throw error;
    }
}

async function release(lock: string, record: string) {
    for (let attempt = 0; attempt < 100; attempt++) {
        if (await remove(lock, record)) {
            return;
        }
        await delay(pollMs);
    }
    console.warn(`Could not remove lock ${lock}`);
}

function delay(ms: number) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
