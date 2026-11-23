import { randomUUID } from "node:crypto";
import { mkdir, open, readFile, readlink, realpath, stat, unlink } from "node:fs/promises";
import { hostname } from "node:os";
import path from "node:path";
import { errnoCode } from "./errors.js";

// The CLI saves the same settings files. Node has no flock, so both take an O_EXCL lock file beside the
// real file; src/wgsextract_cli/core/file_lock.py (shared_file_lock) mirrors this protocol.
const pollMs = 20;
// A lock is written straight after it is made; still empty after this long, its maker stopped in between.
const unwrittenMs = 10_000;
/** The records of the locks this process holds, or is making. */
const held = new Set<string>();

/**
 * Runs one read-modify-write of `filePath` while holding `.<name>.lock` beside the real file behind it, and
 * passes that real path to `action`. Every alias of the file takes the same lock, as do the CLI's saves.
 */
export async function withSettingsFileLock<T>(filePath: string, action: (target: string) => Promise<T>, timeoutMs = 60_000): Promise<T> {
    const target = await realPathAllowingMissing(filePath);
    await mkdir(path.dirname(target), { recursive: true });
    const lock = path.join(path.dirname(target), `.${path.basename(target)}.lock`);
    const deadline = Date.now() + timeoutMs;
    let busySince: number | undefined;
    let record: string;
    for (;;) {
        // A new record each try: one left by a failed try is reclaimable.
        record = lockRecord();
        try {
            if (await create(lock, record)) {
                break;
            }
            busySince = undefined;
            await reclaimIfAbandoned(lock);
        }
        catch (error) {
            // A lock being deleted is busy for moments; a denial that lasts is the folder refusing writes.
            busySince ??= Date.now();
            if (!windowsBusy(error) || Date.now() - busySince > 2_000) {
                throw error;
            }
        }
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

/** Like Python's non-strict Path.resolve(): follows links, a dangling one too, as far as the path exists. */
async function realPathAllowingMissing(filePath: string, links = 0): Promise<string> {
    try {
        return await realpath(filePath);
    }
    catch (error) {
        const parent = path.dirname(filePath);
        if (errnoCode(error) !== "ENOENT" || parent === filePath) {
            throw error;
        }
        const resolved = path.join(await realPathAllowingMissing(parent, links), path.basename(filePath));
        const link = await readlink(resolved).catch(() => null);
        if (link == null) {
            return resolved;
        }
        if (links >= 40) {
            throw error;
        }
        // Joined, not resolved: `..` after a linked folder in the link's target leaves the folder it links to.
        return realPathAllowingMissing(path.isAbsolute(link) ? link : `${path.dirname(resolved)}${path.sep}${link}`, links + 1);
    }
}

function lockRecord() {
    return JSON.stringify({ pid: process.pid, host: hostname(), token: randomUUID() });
}

/** Windows refuses to create or delete a file another process is deleting or reading. */
function windowsBusy(error: unknown) {
    return process.platform === "win32" && ["EPERM", "EACCES", "EBUSY"].includes(errnoCode(error) ?? "");
}

/** Creates `lock` holding `record`; false if another holds it. One this leaves behind on failing, this process reclaims. */
async function create(lock: string, record: string): Promise<boolean> {
    held.add(record);
    let created = false;
    try {
        created = await publish(lock, record);
    }
    finally {
        if (!created) {
            held.delete(record);
        }
    }
    return created;
}

async function publish(lock: string, record: string): Promise<boolean> {
    let handle;
    try {
        handle = await open(lock, "wx");
    }
    catch (error) {
        if (errnoCode(error) === "EEXIST") {
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
    // A maker that stalled before writing may have lost its empty lock.
    try {
        return await readFile(lock, "utf8") === record;
    }
    catch (error) {
        if (errnoCode(error) === "ENOENT" || windowsBusy(error)) {
            return false;
        }
        throw error;
    }
}

async function reclaimIfAbandoned(lock: string) {
    // One reclaimer at a time, so none deletes the lock a saver has just made in place of the abandoned one
    // another reclaimer removed.
    const guard = `${lock}.reclaim`;
    const record = lockRecord();
    try {
        if (!await create(guard, record)) {
            await removeIfAbandoned(guard);
            return;
        }
    }
    catch (error) {
        if (windowsBusy(error)) {
            return;
        }
        throw error;
    }
    try {
        await removeIfAbandoned(lock);
    }
    finally {
        await release(guard, record);
    }
}

async function removeIfAbandoned(lock: string) {
    let record: string;
    let modifiedMs: number;
    try {
        record = await readFile(lock, "utf8");
        modifiedMs = (await stat(lock)).mtimeMs;
    }
    catch (error) {
        if (errnoCode(error) === "ENOENT" || windowsBusy(error)) {
            return;
        }
        throw error;
    }
    const exited = ownerExited(record) ?? Date.now() - modifiedMs > unwrittenMs;
    if (exited && await remove(lock, record)) {
        console.warn(`Removed abandoned lock ${lock}`);
    }
}

/**
 * Whether the process that wrote `record` has provably exited, or is this one and no longer holds the lock (its
 * removal failed). An owner on another computer, or whose process ID is in use, keeps its lock until it is released,
 * so a slow save is never overtaken. Undefined for a record that is not one.
 */
function ownerExited(record: string): boolean | undefined {
    let owner: { pid?: unknown; host?: unknown };
    try {
        owner = JSON.parse(record) as { pid?: unknown; host?: unknown };
    }
    catch {
        return undefined;
    }
    if (owner == null || typeof owner !== "object" || typeof owner.pid !== "number" || typeof owner.host !== "string") {
        return undefined;
    }
    if (owner.host.toLowerCase() !== hostname().toLowerCase()) {
        return false;
    }
    if (owner.pid === process.pid) {
        return !held.has(record);
    }
    try {
        process.kill(owner.pid, 0);
        return false;
    }
    catch (error) {
        return errnoCode(error) === "ESRCH";
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
    try {
        for (let attempt = 0; attempt < 100; attempt++) {
            if (await remove(lock, record)) {
                return;
            }
            await delay(pollMs);
        }
        console.warn(`Could not remove lock ${lock}; the next save removes it`);
    }
    finally {
        held.delete(record);
    }
}

function delay(ms: number) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
