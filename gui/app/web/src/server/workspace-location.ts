import { readFile, readdir } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import type { BundleManifest, WorkspaceLocationInfo } from "../../../shared/types.js";
import { writeFileAtomically } from "./config-store.js";
import { errnoCode, errorMessage } from "./errors.js";
import { appSupportDirectory, safePathComponent } from "./paths.js";

export const workspaceSentinelFileName = ".bundle-workspace";
export const workspaceSentinelContents = "GUI for CLI bundle workspace\n";

/** App-wide settings. They live beside the app's data, not in the workspace, since they say where the workspace is. */
export function appSettingsPath() {
    return path.join(appSupportDirectory(), "settings.json");
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

/** The bundle's workspace: in the folder saved in the app's settings, else the default. */
export async function resolveWorkspaceRoot(manifest: Pick<BundleManifest, "id" | "defaultWorkspace">): Promise<string> {
    const configured = await configuredWorkspacesDirectory();
    return configured ? workspaceRootIn(configured, manifest.id) : defaultWorkspaceRoot(manifest);
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
    return sentinel === workspaceSentinelContents;
}

export async function workspaceLocationInfo(manifest: BundleManifest, workspaceRoot: string): Promise<WorkspaceLocationInfo> {
    const defaultRoot = defaultWorkspaceRoot(manifest);
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
