import { copyFile, lstat, mkdir, mkdtemp, readFile, readdir, readlink, realpath, rename, rm, rmdir, statfs, symlink, utimes } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { configEditorControls } from "../../../shared/rendering.js";
import { parseConfigToml as parseToml, serializeConfigToml as serializeToml } from "./config-toml.js";
import type { BundleManifest, ControlSpec } from "../../../shared/types.js";
import { isSameOrInsidePath, relocatePath } from "../../../shared/workspace-paths.js";
import { bundleStatePath, writeFileAtomically } from "./config-store.js";
import { errnoCode, HttpError } from "./errors.js";
import { configPath, expandWorkspaceToken } from "./paths.js";
import { withSettingsFileLock } from "./settings-lock.js";
import { defaultUsersWorkspaceRoot, saveUsersWorkspace, usersWorkspaceFor } from "./users-workspace.js";

/** What to move: the users workspace to `folder` (null: its default location), or with `setting`, that setting's folder. */
export interface WorkspaceMoveTarget {
    folder: string | null;
    setting?: { controlID: string; key: string } | undefined;
}

export interface WorkspaceMoveRequest extends WorkspaceMoveTarget {
    manifest: BundleManifest;
    sourceRoot: string;
    bundleRoot: string;
    onProgress: (percent: number) => void | Promise<void>;
    signal: AbortSignal;
}

/**
 * Moves the users workspace and everything in it, or the folder one path setting names, then points the settings
 * file, and the app's saved state, at the new place. The original stays whole until the new one is complete, so a
 * failure or cancelled copy loses nothing. Folders the user set elsewhere stay where they are.
 */
export async function moveWorkspace(request: WorkspaceMoveRequest): Promise<void> {
    if (request.setting) {
        await moveSettingFolder(request, request.setting);
        return;
    }
    const { manifest, bundleRoot, folder } = request;
    if (folder != null && !path.isAbsolute(folder)) {
        throw new HttpError(400, `The users workspace folder must be an absolute path: ${folder}`);
    }
    const from = usersWorkspaceFor(bundleRoot);
    const target = folder == null ? await defaultUsersWorkspaceRoot(manifest) : path.resolve(folder);
    if (!target || path.relative(from, bundleRoot) === "") {
        throw new HttpError(400, "This bundle has no users workspace to move.");
    }
    const save = () => saveUsersWorkspace(manifest, folder == null ? null : target);
    if (path.relative(from, target) === "") {
        await save();
        return;
    }
    await moveFolderAndPaths(request, from, target, save);
}

/** Moves what one path setting names to `folder`, then saves the setting; a missing or empty one only saves it. */
async function moveSettingFolder(request: WorkspaceMoveRequest, setting: { controlID: string; key: string }) {
    const { manifest, bundleRoot, folder } = request;
    const control = configEditorControls(manifest).find((candidate) => candidate.id === setting.controlID);
    const spec = control?.settings?.find((candidate) => (candidate.key ?? candidate.id) === setting.key);
    if (!control?.configFile || !spec?.moveContents) {
        throw new HttpError(400, `${setting.key} is not a folder the app can move.`);
    }
    const expandedFolder = folder ? expandWorkspaceToken(folder, bundleRoot) : "";
    if (!expandedFolder || !path.isAbsolute(expandedFolder)) {
        throw new HttpError(400, `The new folder must be an absolute path: ${folder ?? ""}`);
    }
    const settingsFile = (await configFiles(manifest, bundleRoot)).get(control.id);
    if (!settingsFile) {
        throw new HttpError(400, `${control.label ?? control.id} does not declare a settings file.`);
    }
    const values = await readSettings(settingsFile);
    const current = typeof values[setting.key] === "string" ? expandWorkspaceToken(String(values[setting.key]), bundleRoot) : "";
    const target = path.resolve(expandedFolder);
    const save = async () => {
        const latestFile = (await configFiles(manifest, bundleRoot)).get(control.id);
        if (!latestFile) {
            throw new HttpError(400, `${control.label ?? control.id} does not declare a settings file.`);
        }
        await withSettingsFileLock(latestFile, async (file) => {
            const latest = await readSettings(file);
            if (setting.key === "mygenomes_library") {
                delete latest.dataset_library;
                delete latest.genome_library;
            }
            await writeFileAtomically(file, serializeToml({ ...latest, [setting.key]: target }));
        });
    };
    if (!current || !path.isAbsolute(current) || path.relative(current, target) === "" || (await isEmptyOrMissing(current))) {
        await save();
        return;
    }
    await moveFolderAndPaths(request, current, target, save);
}

/** Moves `from` to `to`, then rewrites saved paths into it and runs `save`; puts everything back if a step fails. */
async function moveFolderAndPaths(request: WorkspaceMoveRequest, from: string, to: string, save: () => Promise<void>) {
    const { manifest, sourceRoot, bundleRoot, onProgress, signal } = request;
    const settingsFiles = [...(await configFiles(manifest, bundleRoot)).values()];
    if (request.setting) {
        await assertMovableSettingFolder(from, [usersWorkspaceFor(bundleRoot), sourceRoot, bundleRoot, ...settingsFiles]);
    }
    await assertUsableTarget(to, from, [sourceRoot, bundleRoot]);
    await mkdir(path.dirname(to), { recursive: true });
    await rmdir(to).catch(ignoreMissing);
    const renamed = await renameIfSameVolume(from, to);
    if (!renamed) {
        await copyFolder(from, to, onProgress, signal);
    }
    const rewritten: RewrittenFile[] = [];
    try {
        for (const settingsFile of settingsFiles) {
            // A settings file in the moved folder moved with it.
            await relocateSettingsFile(relocatePath(settingsFile, from, to), from, to, rewritten);
        }
        await relocateBundleState(bundleRoot, from, to, rewritten);
        await save();
    }
    catch (error) {
        for (const file of rewritten) {
            await writeFileAtomically(file.filePath, file.text).catch(() => undefined);
        }
        if (renamed) {
            await rename(to, from);
        }
        else {
            await rm(to, { recursive: true, force: true });
        }
        throw error;
    }
    if (!renamed) {
        await rm(from, { recursive: true, force: true, maxRetries: 3 }).catch((error: unknown) => {
            console.warn(`Could not remove the old folder at ${from}: ${String(error)}`);
        });
    }
    await onProgress(100);
}

async function assertMovableSettingFolder(from: string, protectedPaths: string[]) {
    const resolvedFrom = await existingRealPath(from);
    for (const protectedPath of protectedPaths) {
        if (isSameOrInsidePath(from, protectedPath) || isSameOrInsidePath(resolvedFrom, await existingRealPath(protectedPath))) {
            throw new HttpError(400, `Cannot move ${from}: it contains the workspace, bundle source or settings file ${protectedPath}. Move the users workspace from Users Workspace Location instead.`);
        }
    }
}

async function existingRealPath(file: string): Promise<string> {
    return realpath(file).catch((error: unknown) => {
        if (errnoCode(error) === "ENOENT") {
            return path.resolve(file);
        }
        throw error;
    });
}

/** Each settings editor's file, by control ID: the one saved in the app's state, else its declared one. */
async function configFiles(manifest: BundleManifest, bundleRoot: string): Promise<Map<string, string>> {
    const saved = await readBundleState(bundleRoot);
    const chosen = (saved?.configFilePaths ?? {}) as Record<string, string | undefined>;
    const files = new Map<string, string>();
    for (const control of configEditorControls(manifest)) {
        const file = settingsFileOf(control, chosen[control.id], bundleRoot);
        if (file) {
            files.set(control.id, file);
        }
    }
    return files;
}

function settingsFileOf(control: ControlSpec, requested: string | undefined, bundleRoot: string) {
    if (!control.configFile) {
        return undefined;
    }
    try {
        return configPath(control, requested, bundleRoot);
    }
    catch {
        return configPath(control, undefined, bundleRoot);
    }
}

async function assertUsableTarget(target: string, from: string, roots: string[]) {
    for (const root of [from, ...roots]) {
        if (isSameOrInsidePath(root, target)) {
            throw new HttpError(400, `The new folder cannot be inside ${root}`);
        }
    }
    if (isSameOrInsidePath(target, from)) {
        throw new HttpError(400, `The new folder cannot hold ${from}`);
    }
    if (!(await isEmptyOrMissing(target))) {
        throw new HttpError(400, `${target} already has files in it. Choose another folder, or move or delete them first.`);
    }
}

async function isEmptyOrMissing(folder: string) {
    const entries = await readdir(folder).catch((error: unknown) => {
        if (errnoCode(error) === "ENOENT") {
            return [];
        }
        throw new HttpError(400, `Cannot use ${folder}: ${String(error)}`);
    });
    return entries.length === 0;
}

function ignoreMissing(error: unknown) {
    if (errnoCode(error) !== "ENOENT") {
        throw error;
    }
}

async function renameIfSameVolume(from: string, to: string) {
    try {
        await rename(from, to);
        return true;
    }
    catch (error) {
        if (errnoCode(error) === "EXDEV") {
            return false;
        }
        throw new HttpError(409, `Could not move ${from}. Close anything using files in it and try again: ${String(error)}`);
    }
}

/** Copies through a staging folder renamed into place at the end, so the target is never partly there. */
async function copyFolder(from: string, to: string, onProgress: WorkspaceMoveRequest["onProgress"], signal: AbortSignal) {
    // Unique, so no folder already there is ever taken over or deleted.
    const staging = await mkdtemp(path.join(path.dirname(to), `.${path.basename(to)}.moving-`));
    try {
        const files = await listEntries(from);
        const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
        const volume = await statfs(staging);
        const availableBytes = Number(volume.bavail) * Number(volume.bsize);
        if (totalBytes > availableBytes) {
            throw new HttpError(409, `Moving needs ${formatGB(totalBytes)} GB, but ${path.dirname(to)} has ${formatGB(availableBytes)} GB free.`);
        }
        let copiedBytes = 0;
        let reportedPercent = 0;
        for (const file of files) {
            signal.throwIfAborted();
            await copyEntry(from, staging, file);
            copiedBytes += file.size;
            const percent = totalBytes ? Math.floor(copiedBytes / totalBytes * 99) : 99;
            if (percent > reportedPercent) {
                reportedPercent = percent;
                await onProgress(percent);
            }
        }
        signal.throwIfAborted();
        await rename(staging, to);
    }
    catch (error) {
        await rm(staging, { recursive: true, force: true });
        throw error;
    }
}

function formatGB(bytes: number) {
    return (bytes / 1024 ** 3).toFixed(1);
}

interface CarriedEntry {
    relative: string;
    size: number;
    directory: boolean;
}

/** Files, and folders too, so that empty ones (a custom temp folder, say) still exist after the move. */
async function listEntries(root: string, relative = ""): Promise<CarriedEntry[]> {
    const files: CarriedEntry[] = [];
    for (const entry of await readdir(path.join(root, relative), { withFileTypes: true })) {
        const entryPath = path.join(relative, entry.name);
        if (entry.isDirectory()) {
            files.push({ relative: entryPath, size: 0, directory: true }, ...await listEntries(root, entryPath));
        }
        else {
            files.push({ relative: entryPath, size: (await lstat(path.join(root, entryPath))).size, directory: false });
        }
    }
    return files;
}

async function copyEntry(fromRoot: string, toRoot: string, { relative, directory }: CarriedEntry) {
    const from = path.join(fromRoot, relative);
    const to = path.join(toRoot, relative);
    if (directory) {
        await mkdir(to, { recursive: true });
        return;
    }
    await mkdir(path.dirname(to), { recursive: true });
    const info = await lstat(from);
    if (info.isSymbolicLink()) {
        await symlink(await readlink(from), to);
        return;
    }
    await copyFile(from, to);
    await utimes(to, info.atime, info.mtime);
}

/** A file's text from before it was rewritten, to put back if the move fails. */
interface RewrittenFile {
    filePath: string;
    text: string;
}

async function readSettings(filePath: string): Promise<Record<string, unknown>> {
    const text = await readFile(filePath, "utf8").catch((error: unknown) => {
        ignoreMissing(error);
        return "";
    });
    return parseToml(text);
}

async function relocateSettingsFile(filePath: string, from: string, to: string, rewritten: RewrittenFile[]) {
    if (!existsSync(filePath)) {
        return;
    }
    await withSettingsFileLock(filePath, async (target) => {
        const text = await readFile(target, "utf8").catch((error: unknown) => {
            ignoreMissing(error);
            return null;
        });
        if (text != null) {
            rewritten.push({ filePath: target, text });
            await writeFileAtomically(target, serializeToml(relocateStrings(parseToml(text), from, to)));
        }
    });
}

async function readBundleState(bundleRoot: string): Promise<Record<string, unknown> | null> {
    return readFile(bundleStatePath(bundleRoot), "utf8").then((text) => JSON.parse(text) as Record<string, unknown>).catch((error: unknown) => {
        ignoreMissing(error);
        return null;
    });
}

/** The app's saved state holds paths too: the settings file chosen, and field values. */
async function relocateBundleState(bundleRoot: string, from: string, to: string, rewritten: RewrittenFile[]) {
    const statePath = bundleStatePath(bundleRoot);
    const text = await readFile(statePath, "utf8").catch((error: unknown) => {
        ignoreMissing(error);
        return null;
    });
    if (text != null) {
        rewritten.push({ filePath: statePath, text });
        const relocated = relocateStrings(JSON.parse(text) as unknown, from, to);
        await writeFileAtomically(statePath, `${JSON.stringify(relocated, null, 2)}\n`);
    }
}

function relocateStrings(value: unknown, from: string, to: string): unknown {
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
