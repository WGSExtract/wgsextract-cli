import { existsSync } from "node:fs";
import { copyFile, mkdir, readFile, readdir, rm } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import type { BundleManifest, WorkspaceLocationInfo } from "../../../shared/types.js";
import { writeFileAtomically } from "./config-store.js";
import { errnoCode, errorMessage } from "./errors.js";
import { applicationSupportDirectory, appSupportDirectory, safePathComponent } from "./paths.js";

export const workspaceSentinelFileName = ".bundle-workspace";
export const workspaceSentinelContents = "GUI for CLI bundle workspace\n";

/** App-wide settings. They live beside the app's data, not in the workspace, since they say where the workspace is. */
export function appSettingsPath() {
    return path.join(appSupportDirectory(), "settings.json");
}

/** The data folder the desktop app used up to 0.8.6, named for its identifier then. */
export const legacyAppDataName = "dev.guiforcli.web.embed.wgsextract";

/**
 * Brings the app-wide settings (where the workspace is) over from the data folder the app used up to 0.8.6,
 * then removes that folder. One that still holds a workspace (from releases before workspaces moved to the
 * home folder) is left in place. Only for the app's own data folder, not one a test or dev run names.
 */
export async function migrateLegacyAppData() {
    if (process.env.WGSEXTRACT_GUI_APP_DATA_NAME) {
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

/** Where a bundle that declares no defaultWorkspace keeps its workspace, and where every bundle's used to be. */
export function appDataWorkspacesDirectory() {
    return path.join(appSupportDirectory(), "BundleWorkspaces");
}

/** A bundle may only suggest one visible folder directly in the user's home; it never picks an arbitrary path. */
export function defaultWorkspaceRoot(manifest: Pick<BundleManifest, "id" | "defaultWorkspace">) {
    const declared = manifest.defaultWorkspace?.trim();
    if (!declared) {
        return workspaceRootIn(appDataWorkspacesDirectory(), manifest.id);
    }
    const match = /^~\/([A-Za-z0-9_][A-Za-z0-9_.-]*)$/.exec(declared);
    if (!match?.[1]) {
        throw new Error(`defaultWorkspace must name one visible folder in the home folder, like ~/my-app: ${declared}`);
    }
    return path.join(homedir(), match[1]);
}

async function readAppSettings(): Promise<Record<string, unknown>> {
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

async function configuredWorkspacesDirectory() {
    const value = (await readAppSettings()).bundleWorkspacesDirectory;
    return typeof value === "string" && path.isAbsolute(value) ? value : null;
}

/** The folder used instead of the bundle's default when a folder of the user's already had that name (see claimWorkspaceRoot). */
async function recordedDefaultWorkspaceRoot(manifest: Pick<BundleManifest, "id">) {
    const roots = (await readAppSettings()).defaultWorkspaceRoots;
    const value = roots != null && typeof roots === "object" ? (roots as Record<string, unknown>)[safePathComponent(manifest.id)] : undefined;
    return typeof value === "string" && path.isAbsolute(value) ? value : null;
}

/** Where the workspace goes when no folder is chosen: the bundle's default, or the folder used instead of it. */
export async function effectiveDefaultWorkspaceRoot(manifest: Pick<BundleManifest, "id" | "defaultWorkspace">) {
    return (await recordedDefaultWorkspaceRoot(manifest)) ?? defaultWorkspaceRoot(manifest);
}

/** The bundle's workspace: in the folder saved in the app's settings, else the default. */
export async function resolveWorkspaceRoot(manifest: Pick<BundleManifest, "id" | "defaultWorkspace">): Promise<string> {
    const configured = await configuredWorkspacesDirectory();
    return configured ? workspaceRootIn(configured, manifest.id) : effectiveDefaultWorkspaceRoot(manifest);
}

const maxDefaultWorkspaceSuffix = 100;

/**
 * The workspace to prepare. When the bundle's default folder in the home folder is already someone else's
 * (a file, an unreadable folder, or non-empty and not a workspace, also when only its letter case differs on a case-insensitive disk), the
 * first free "<name>-2", "<name>-3", ... is used instead and recorded, so it stays the default from then on.
 * The user's folder is never adopted. A folder the user chose must be usable, or this fails saying what to do.
 */
export async function claimWorkspaceRoot(manifest: Pick<BundleManifest, "id" | "defaultWorkspace">): Promise<string> {
    const configured = await configuredWorkspacesDirectory();
    if (configured) {
        const root = workspaceRootIn(configured, manifest.id);
        if (!(await isAdoptableWorkspaceRoot(root))) {
            throw new Error(`The workspaces folder chosen in Settings holds something that is not this app's workspace, or that can't be read: ${root}. Move that folder away, or remove "bundleWorkspacesDirectory" from ${appSettingsPath()} to use the default location.`);
        }
        return root;
    }
    const recorded = await recordedDefaultWorkspaceRoot(manifest);
    const declared = defaultWorkspaceRoot(manifest);
    const root = recorded ?? declared;
    if (await isAdoptableWorkspaceRoot(root)) {
        return root;
    }
    // Only the bundle's suggested folder in the home folder may give way to another name.
    if (recorded || !manifest.defaultWorkspace?.trim()) {
        throw new Error(`Refusing to use non-empty folder that is not a bundle workspace: ${root}`);
    }
    for (let suffix = 2; suffix <= maxDefaultWorkspaceSuffix; suffix++) {
        const candidate = `${declared}-${suffix}`;
        if (await isAdoptableWorkspaceRoot(candidate)) {
            const settings = await readAppSettings();
            const roots = settings.defaultWorkspaceRoots != null && typeof settings.defaultWorkspaceRoots === "object" ? settings.defaultWorkspaceRoots as Record<string, unknown> : {};
            settings.defaultWorkspaceRoots = { ...roots, [safePathComponent(manifest.id)]: candidate };
            await writeFileAtomically(appSettingsPath(), `${JSON.stringify(settings, null, 2)}\n`);
            console.error(`${declared} is not this app's workspace, so the workspace is kept in ${candidate} instead.`);
            return candidate;
        }
    }
    throw new Error(`Refusing to use non-empty folder that is not a bundle workspace: ${declared}`);
}

export function workspaceRootIn(directory: string, bundleID: unknown) {
    return path.join(directory, safePathComponent(bundleID));
}

/**
 * Syncing replaces entries named like the bundle's, so a folder may only hold this bundle's workspace
 * if it is empty or already a workspace; someone else's folder of that name is never adopted.
 */
export async function assertAdoptableWorkspaceRoot(workspaceRoot: string) {
    if (!(await isAdoptableWorkspaceRoot(workspaceRoot))) {
        throw new Error(`Refusing to use non-empty folder that is not a bundle workspace: ${workspaceRoot}`);
    }
}

// A file, or a folder this user can't read, under the name is someone else's: never adoptable.
const unusableWorkspaceRootCodes = new Set(["ENOTDIR", "EACCES", "EPERM"]);

async function isAdoptableWorkspaceRoot(workspaceRoot: string) {
    const entries = await readdir(workspaceRoot).catch((error: unknown) => {
        const code = errnoCode(error);
        if (code === "ENOENT") {
            return [];
        }
        if (code && unusableWorkspaceRootCodes.has(code)) {
            return null;
        }
        throw error;
    });
    return entries != null && (entries.length === 0 || await isManagedWorkspace(workspaceRoot));
}

export async function isManagedWorkspace(workspaceRoot: string) {
    const sentinel = await readFile(path.join(workspaceRoot, workspaceSentinelFileName), "utf8").catch(() => null);
    return sentinel === workspaceSentinelContents;
}

export async function workspaceLocationInfo(manifest: BundleManifest, workspaceRoot: string): Promise<WorkspaceLocationInfo> {
    const defaultRoot = await effectiveDefaultWorkspaceRoot(manifest);
    return {
        workspaceRoot,
        defaultDirectory: path.dirname(defaultRoot),
        configuredDirectory: await configuredWorkspacesDirectory(),
        usingDefault: path.relative(defaultRoot, workspaceRoot) === "",
    };
}

/** Records the folder holding the workspace, or clears it when null. Moving the workspace there is workspace-move's. */
export async function saveWorkspacesDirectory(directory: string | null) {
    const settings = await readAppSettings();
    if (directory == null) {
        delete settings.bundleWorkspacesDirectory;
    }
    else {
        settings.bundleWorkspacesDirectory = directory;
    }
    // The only record of a moved workspace: never left half-written.
    await writeFileAtomically(appSettingsPath(), `${JSON.stringify(settings, null, 2)}\n`);
}
