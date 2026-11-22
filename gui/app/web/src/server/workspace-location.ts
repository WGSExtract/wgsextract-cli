import { existsSync } from "node:fs";
import { copyFile, mkdir, readFile, readdir, rm } from "node:fs/promises";
import path from "node:path";
import type { BundleManifest } from "../../../shared/types.js";
import { writeFileAtomically } from "./config-store.js";
import { errnoCode, errorMessage } from "./errors.js";
import { applicationSupportDirectory, appSupportContainerName, appSupportDirectory, defaultAppDataName, safePathComponent } from "./paths.js";

export const workspaceSentinelFileName = ".bundle-workspace";
export const workspaceSentinelContents = "WGS Extract GUI workspace\n";
/** What workspaces made by releases up to 0.8.7 hold. They are still this app's; preparing one rewrites it. */
const legacyWorkspaceSentinelContents = "GUI for CLI bundle workspace\n";

export function isWorkspaceSentinel(contents: string | null) {
    return contents === workspaceSentinelContents || contents === legacyWorkspaceSentinelContents;
}

/** App-wide settings. They live beside the app's data, not in a workspace, since they say where the users workspace is. */
export function appSettingsPath() {
    return path.join(appSupportDirectory(), "settings.json");
}

/** The data folder the desktop app used up to 0.8.6, named for its identifier then. */
export const legacyAppDataName = "dev.guiforcli.web.embed.wgsextract";

/**
 * Brings the app-wide settings over from the data folder the app used up to 0.8.6, then removes that
 * folder. One that still holds a workspace is left in place. Only for the app's own data folder, not
 * one a test or dev run names.
 */
export async function migrateLegacyAppData() {
    // The desktop app names its data folder explicitly too, so compare the name rather than whether one is set.
    if (appSupportContainerName() !== defaultAppDataName) {
        return;
    }
    const legacy = path.join(applicationSupportDirectory(), legacyAppDataName);
    const legacySettings = path.join(legacy, "settings.json");
    if (!existsSync(legacySettings) && !existsSync(legacy)) {
        return;
    }
    try {
        if (existsSync(legacySettings) && !existsSync(appSettingsPath())) {
            await mkdir(appSupportDirectory(), { recursive: true });
            await copyFile(legacySettings, appSettingsPath());
        }
        // Nor one the saved settings point into: a workspaces folder chosen in it.
        const settings = existsSync(legacySettings) ? await readFile(legacySettings, "utf8") : "";
        const pointedInto = settings.includes(JSON.stringify(legacy).slice(1, -1));
        if (!pointedInto && !existsSync(path.join(legacy, "BundleWorkspaces"))) {
            await rm(legacy, { recursive: true, force: true, maxRetries: 3 });
        }
    }
    catch (error) {
        console.error(`Could not move the app's settings from ${legacy}: ${errorMessage(error)}`);
    }
}

/** Where every bundle keeps its {{bundleWorkspace}}: in the app's data folder, out of the user's sight. */
export function appDataWorkspacesDirectory() {
    return path.join(appSupportDirectory(), "BundleWorkspaces");
}

/** The bundle's {{bundleWorkspace}}. The app decides where it is; the user never moves it. */
export function bundleWorkspaceRoot(manifest: Pick<BundleManifest, "id">) {
    return workspaceRootIn(appDataWorkspacesDirectory(), manifest.id);
}

export async function readAppSettings(): Promise<Record<string, unknown>> {
    try {
        const parsed: unknown = JSON.parse(await readFile(appSettingsPath(), "utf8"));
        return parsed != null && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
    }
    catch (error) {
        if (errnoCode(error) !== "ENOENT") {
            console.error(`Ignoring unreadable ${appSettingsPath()}: ${errorMessage(error)}`);
        }
        return {};
    }
}

/** Saves the app-wide settings. The only record of where a moved users workspace is: never left half-written. */
export async function writeAppSettings(settings: Record<string, unknown>) {
    await mkdir(appSupportDirectory(), { recursive: true });
    await writeFileAtomically(appSettingsPath(), `${JSON.stringify(settings, null, 2)}\n`);
}

export type MovedConfigPath = readonly [from: string, to: string];

/** Records where an explicit CLI --config path went; read only if the old file is gone. */
export function withMovedConfigPaths(settings: Record<string, unknown>, paths: readonly MovedConfigPath[]) {
    if (paths.length === 0) return settings;
    const previous = settings.movedConfigPaths;
    const redirects = previous != null && typeof previous === "object" && !Array.isArray(previous)
        ? { ...previous } as Record<string, unknown>
        : {};
    for (const [from, to] of paths) {
        for (const oldPath of Object.keys(redirects)) {
            if (!path.relative(oldPath, to) || !path.relative(oldPath, from)) delete redirects[oldPath];
        }
        redirects[from] = to;
    }
    return { ...settings, movedConfigPaths: redirects };
}

export async function saveMovedConfigPath(from: string, to: string) {
    await writeAppSettings(withMovedConfigPaths(await readAppSettings(), [[from, to]]));
}

/** One bundle's entry of a map in the app settings, keyed by bundle ID. */
export function bundleSetting(settings: Record<string, unknown>, key: string, manifest: Pick<BundleManifest, "id">): string | null {
    const map = settings[key];
    const value = map != null && typeof map === "object" ? (map as Record<string, unknown>)[safePathComponent(manifest.id)] : undefined;
    return typeof value === "string" && path.isAbsolute(value) ? value : null;
}

/** Sets (or, with null, removes) one bundle's entry of a map in the app settings. */
export function withBundleSetting(settings: Record<string, unknown>, key: string, manifest: Pick<BundleManifest, "id">, value: string | null) {
    const map = settings[key] != null && typeof settings[key] === "object" ? { ...(settings[key] as Record<string, unknown>) } : {};
    if (value == null) {
        delete map[safePathComponent(manifest.id)];
    }
    else {
        map[safePathComponent(manifest.id)] = value;
    }
    return { ...settings, [key]: map };
}

export function workspaceRootIn(directory: string, bundleID: unknown) {
    return path.join(directory, safePathComponent(bundleID));
}

/**
 * Syncing replaces entries named like the bundle's, so a folder may only hold this bundle's workspace
 * if it is empty or already a workspace; someone else's folder of that name is never adopted.
 */
export async function assertAdoptableWorkspaceRoot(workspaceRoot: string) {
    const entries = await readdir(workspaceRoot).catch((error: unknown) => {
        if (errnoCode(error) === "ENOENT") {
            return [];
        }
        throw error;
    });
    if (entries.length > 0 && !(await isManagedWorkspace(workspaceRoot))) {
        throw new Error(`Refusing to use non-empty folder that is not a bundle workspace: ${workspaceRoot}`);
    }
}

export async function isManagedWorkspace(workspaceRoot: string) {
    const sentinel = await readFile(path.join(workspaceRoot, workspaceSentinelFileName), "utf8").catch(() => null);
    return isWorkspaceSentinel(sentinel);
}
