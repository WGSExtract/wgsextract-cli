import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";
import { lstat, mkdir, readdir, realpath, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { errnoCode, HttpError } from "./errors.js";
import { applicationSupportDirectory, defaultAppDataName } from "./paths.js";

type Activity = "use" | "move";
const lease = new AsyncLocalStorage<{ kind: Activity; active: boolean }>();
const markerName = /^(use|move)-([1-9]\d*)-[a-f0-9-]+$/;

// Shared with Python, independent of the bundle and outside everything it moves.
export function workspaceActivityDirectory(): string {
    return path.join(applicationSupportDirectory(), defaultAppDataName, "workspace-activity");
}

function busy(kind: Activity) {
    return new HttpError(409, kind === "move" ? "workspaceMoveBusy" : "workspaceMoveInProgress");
}

export function withWorkspaceUse<T>(run: () => Promise<T>): Promise<T> {
    return withActivity("use", run);
}

export function withWorkspaceMove<T>(run: () => Promise<T>): Promise<T> {
    return withActivity("move", run);
}

/** A settings lock from an older writer is not a file the mover may carry. */
export function assertNotSettingsLock(file: string) {
    if (/^\..+\.toml\.lock(?:\.reclaim)?$/i.test(path.basename(file))) {
        throw settingsLocked(file);
    }
}

export async function assertSettingsFileUnlocked(file: string) {
    const target = await realpath(file).catch((error: unknown) => {
        if (errnoCode(error) === "ENOENT") return file;
        throw error;
    });
    const lock = path.join(path.dirname(target), `.${path.basename(target)}.lock`);
    for (const candidate of [lock, `${lock}.reclaim`]) {
        try {
            await lstat(candidate);
        } catch (error) {
            if (errnoCode(error) === "ENOENT") continue;
            throw error;
        }
        throw settingsLocked(candidate);
    }
}

function settingsLocked(file: string) {
    console.warn(`Settings are locked at ${file}`);
    return new HttpError(409, "workspaceMoveSettingsLocked");
}

async function withActivity<T>(kind: Activity, run: () => Promise<T>): Promise<T> {
    const outer = lease.getStore();
    if (outer?.active) {
        if (kind === "move" && outer.kind !== "move") throw busy(kind);
        return run();
    }
    const directory = workspaceActivityDirectory();
    const name = `${kind}-${process.pid}-${randomUUID()}`;
    const marker = path.join(directory, name);
    await mkdir(directory, { recursive: true, mode: 0o700 });
    // Publish first, then check: of two conflicting entrants, the later
    // publication necessarily checks while the earlier one's marker is visible.
    await writeFile(marker, "", { flag: "wx" });
    const entry = { kind, active: true };
    try {
        for (const other of await readdir(directory)) {
            if (other === name) continue;
            const match = markerName.exec(other);
            if (!match) continue;
            try {
                process.kill(Number(match[2]), 0);
            } catch (error) {
                if (errnoCode(error) === "ESRCH") {
                    await rm(path.join(directory, other), { force: true });
                    continue;
                }
                if (errnoCode(error) !== "EPERM" && errnoCode(error) !== "EACCES") throw error;
            }
            if (kind === "move" || match[1] === "move") throw busy(kind);
        }
        return await lease.run(entry, run);
    } finally {
        entry.active = false;
        // Best effort: a leftover marker must not hide run()'s own error. A
        // dead process's marker is reclaimed by the next entrant.
        await rm(marker, { force: true, maxRetries: 3 }).catch((error: unknown) => {
            console.warn(`Could not remove workspace activity marker ${marker}: ${String(error)}`);
        });
    }
}
