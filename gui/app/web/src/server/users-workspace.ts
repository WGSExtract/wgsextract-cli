import { randomUUID } from "node:crypto";
import { mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import type { BundleManifest, WorkspaceLocationInfo } from "../../../shared/types.js";
import { errnoCode, HttpError } from "./errors.js";
import { safePathComponent } from "./paths.js";
import { appSettingsPath, bundleSetting, isManagedWorkspace, type MovedConfigPath, readAppSettings, withBundleSetting, withMovedConfigPaths, workspaceRootIn, workspaceSentinelFileName, writeAppSettings } from "./workspace-location.js";

/**
 * {{usersWorkspace}}: one visible folder in the user's home (~/wgs-extract) for the bundle's settings file and the
 * folders the user manages, which they may move to another disk. The CLI keeps its own settings file and folders
 * there too, and marks the folder the same way, so the two share it.
 */
export const usersWorkspaceSentinelFileName = ".users-workspace";
export const usersWorkspaceSentinelContents = "WGS Extract users workspace\n";

/** The folder the user moved the users workspace to, per bundle. */
const chosenKey = "usersWorkspaces";
/** The folder used instead of the bundle's default when a folder of the user's already had that name. */
const defaultsKey = "defaultUsersWorkspaceRoots";
/** What 0.8.5 to 0.8.7 recorded for the one workspace they kept in the home folder. */
const legacyChosenDirectoryKey = "bundleWorkspacesDirectory";
const legacyDefaultsKey = "defaultWorkspaceRoots";

const syncMetadataFileName = ".workspace-sync.json";
const bundleStateFileName = "state.json";

const usersWorkspaces = new Map<string, { root: string; followsLocation: boolean }>();

/** Records the users workspace of the bundle prepared in `bundleRoot`, for {{usersWorkspace}} in its paths. */
export function registerUsersWorkspace(bundleRoot: string, usersRoot: string, followsLocation = false) {
    usersWorkspaces.set(path.resolve(bundleRoot), { root: path.resolve(usersRoot), followsLocation });
}

/** The users workspace of the bundle in `bundleRoot`; without one, the bundle workspace stands in for it. */
export function usersWorkspaceFor(bundleRoot: string): string {
    return usersWorkspaces.get(path.resolve(bundleRoot))?.root ?? bundleRoot;
}

/** A bundle may only suggest one visible folder directly in the user's home; it never picks an arbitrary path. */
export function declaredDefaultUsersWorkspace(manifest: Pick<BundleManifest, "defaultUsersWorkspace">): string | null {
    const declared = manifest.defaultUsersWorkspace?.trim();
    if (!declared) {
        return null;
    }
    const match = /^~\/([A-Za-z0-9_][A-Za-z0-9_.-]*)$/.exec(declared);
    if (!match?.[1]) {
        throw new Error(`defaultUsersWorkspace must name one visible folder in the home folder, like ~/my-app: ${declared}`);
    }
    return path.join(homedir(), match[1]);
}

type UsersManifest = Pick<BundleManifest, "id" | "defaultUsersWorkspace">;

/** Where the users workspace goes when the user chose no folder: the bundle's default, or the one used instead of it. */
export async function defaultUsersWorkspaceRoot(manifest: UsersManifest): Promise<string | null> {
    const declared = declaredDefaultUsersWorkspace(manifest);
    return declared && (bundleSetting(await readAppSettings(), defaultsKey, manifest) ?? declared);
}

/**
 * The bundle's users workspace, without changing anything: the folder the user moved it to, else the default
 * (also as 0.8.5 to 0.8.7 recorded them); null when the bundle has none.
 */
export async function resolveUsersWorkspaceRoot(manifest: UsersManifest): Promise<string | null> {
    const declared = declaredDefaultUsersWorkspace(manifest);
    if (!declared) {
        return null;
    }
    const settings = await readAppSettings();
    const legacyDirectory = settings[legacyChosenDirectoryKey];
    return bundleSetting(settings, chosenKey, manifest) ??
        (typeof legacyDirectory === "string" && path.isAbsolute(legacyDirectory) ? workspaceRootIn(legacyDirectory, manifest.id) : null) ??
        bundleSetting(settings, defaultsKey, manifest) ?? bundleSetting(settings, legacyDefaultsKey, manifest) ?? declared;
}

/** Records the folder the user moved the users workspace to, or clears it (null) to use the default. */
export async function saveUsersWorkspace(manifest: UsersManifest, folder: string | null) {
    await writeAppSettings(withBundleSetting(await readAppSettings(), chosenKey, manifest, folder));
}

const revisionsKey = "workspaceMoveRevisions";

function moveRevisions(settings: Record<string, unknown>): Record<string, unknown> {
    const revisions = settings[revisionsKey];
    return revisions != null && typeof revisions === "object" && !Array.isArray(revisions)
        ? { ...revisions } : {};
}

export async function workspaceMoveRevision(manifest: UsersManifest) {
    const revision = moveRevisions(await readAppSettings())[safePathComponent(manifest.id)];
    return typeof revision === "string" ? revision : "0";
}

export async function recordWorkspaceMove(manifest: UsersManifest, folder?: string | null, movedConfigs: readonly MovedConfigPath[] = []) {
    const settings = await readAppSettings();
    const next = folder === undefined ? settings : withBundleSetting(settings, chosenKey, manifest, folder);
    const revisions = { ...moveRevisions(settings), [safePathComponent(manifest.id)]: randomUUID() };
    await writeAppSettings(withMovedConfigPaths({ ...next, [revisionsKey]: revisions }, movedConfigs));
}

/** A second app process must not start using paths cached before another moved the workspace. */
export async function assertCurrentUsersWorkspace(manifest: UsersManifest, bundleRoot: string) {
    const registered = usersWorkspaces.get(path.resolve(bundleRoot));
    if (!registered?.followsLocation) return;
    const location = await resolveUsersWorkspaceRoot(manifest);
    if (location && path.relative(location, registered.root) !== "") {
        throw new HttpError(409, "workspaceMoveStale");
    }
}

export async function usersWorkspaceLocationInfo(manifest: UsersManifest, usersRoot: string): Promise<WorkspaceLocationInfo | undefined> {
    const defaultRoot = await defaultUsersWorkspaceRoot(manifest);
    if (!defaultRoot) {
        return undefined;
    }
    return { workspaceRoot: usersRoot, defaultRoot, usingDefault: path.relative(defaultRoot, usersRoot) === "" };
}

const maxDefaultSuffix = 100;

/**
 * The users workspace to prepare, or null when the bundle has none. A folder the user chose must be usable, or
 * this fails saying what to do. When the bundle's default folder is someone else's (a file, an unreadable folder,
 * or non-empty and not a workspace), the first free "<name>-2", "<name>-3", ... is used instead and recorded, so
 * it stays the default from then on. The user's own folder is never adopted.
 */
export async function claimUsersWorkspaceRoot(manifest: UsersManifest): Promise<string | null> {
    const declared = declaredDefaultUsersWorkspace(manifest);
    if (!declared) {
        return null;
    }
    const settings = await adoptLegacySettings(manifest);
    const chosen = bundleSetting(settings, chosenKey, manifest);
    if (chosen) {
        if (!(await isAdoptable(chosen))) {
            throw new Error(`The users workspace folder chosen in Settings holds something that is not this app's, or that can't be read: ${chosen}. Move that folder away, or remove it from "${chosenKey}" in ${appSettingsPath()} to use the default location.`);
        }
        return chosen;
    }
    const recorded = bundleSetting(settings, defaultsKey, manifest);
    const root = recorded ?? declared;
    if (await isAdoptable(root)) {
        return root;
    }
    if (recorded) {
        throw new Error(`Refusing to use non-empty folder that is not a users workspace: ${root}`);
    }
    for (let suffix = 2; suffix <= maxDefaultSuffix; suffix++) {
        const candidate = `${declared}-${suffix}`;
        if (await isAdoptable(candidate)) {
            await writeAppSettings(withBundleSetting(settings, defaultsKey, manifest, candidate));
            console.error(`${declared} is not this app's, so the users workspace is kept in ${candidate} instead.`);
            return candidate;
        }
    }
    throw new Error(`Refusing to use non-empty folder that is not a users workspace: ${declared}`);
}

/**
 * 0.8.5 to 0.8.7 kept the whole workspace in the home folder and recorded where the user moved it. That folder is
 * the users workspace now: what it held of the user's (settings file, libraries, outputs) stays there.
 */
async function adoptLegacySettings(manifest: UsersManifest): Promise<Record<string, unknown>> {
    let settings = await readAppSettings();
    const legacyDirectory = settings[legacyChosenDirectoryKey];
    const legacyDefault = bundleSetting(settings, legacyDefaultsKey, manifest);
    if (typeof legacyDirectory !== "string" && !legacyDefault) {
        return settings;
    }
    if (typeof legacyDirectory === "string" && path.isAbsolute(legacyDirectory) && !bundleSetting(settings, chosenKey, manifest)) {
        settings = withBundleSetting(settings, chosenKey, manifest, workspaceRootIn(legacyDirectory, manifest.id));
    }
    if (legacyDefault && !bundleSetting(settings, defaultsKey, manifest)) {
        settings = withBundleSetting(settings, defaultsKey, manifest, legacyDefault);
    }
    delete settings[legacyChosenDirectoryKey];
    delete settings[legacyDefaultsKey];
    await writeAppSettings(settings);
    return settings;
}

// A file, or a folder this user can't read, under the name is someone else's: never adoptable.
const unusableCodes = new Set(["ENOTDIR", "EACCES", "EPERM"]);

/** Empty or missing, a users workspace (the app's or the CLI's), or a workspace an earlier release kept here. */
async function isAdoptable(root: string) {
    const entries = await readdir(root).catch((error: unknown) => {
        const code = errnoCode(error);
        if (code === "ENOENT") {
            return [];
        }
        if (code && unusableCodes.has(code)) {
            return null;
        }
        throw error;
    });
    return entries != null && (entries.length === 0 || await isUsersWorkspace(root) || await isManagedWorkspace(root));
}

export async function isUsersWorkspace(root: string) {
    const sentinel = await readFile(path.join(root, usersWorkspaceSentinelFileName), "utf8").catch(() => null);
    return sentinel === usersWorkspaceSentinelContents;
}

/**
 * Creates the users workspace and marks it. A workspace that 0.8.5 to 0.8.7 kept here gives the app what is the app's:
 * its saved state moves to `bundleRoot` (unless that has its own), and its copy of the bundle is removed: the
 * entries its last sync copied that the bundle (`bundleNames`, its top-level entries) has. Anything else stays.
 */
export async function prepareUsersWorkspace(usersRoot: string, bundleRoot: string, bundleNames: ReadonlySet<string>) {
    if (path.relative(usersRoot, bundleRoot) !== "") {
        const identities = await Promise.all([usersRoot, bundleRoot].map(async (root) => {
            try {
                return await stat(root, { bigint: true });
            }
            catch (error) {
                if (errnoCode(error) === "ENOENT") {
                    return null;
                }
                throw error;
            }
        }));
        const [users, bundle] = identities;
        if (users && bundle && users.dev === bundle.dev && users.ino === bundle.ino) {
            throw new Error("The bundle and users workspaces refer to the same physical directory. Choose distinct workspace folders.");
        }
    }
    await mkdir(usersRoot, { recursive: true });
    if (path.relative(usersRoot, bundleRoot) !== "" && await isManagedWorkspace(usersRoot)) {
        await releaseLegacyBundleWorkspace(usersRoot, bundleRoot, bundleNames);
    }
    await writeFile(path.join(usersRoot, usersWorkspaceSentinelFileName), usersWorkspaceSentinelContents, "utf8");
}

async function releaseLegacyBundleWorkspace(usersRoot: string, bundleRoot: string, bundleNames: ReadonlySet<string>) {
    const state = path.join(usersRoot, bundleStateFileName);
    if (existsSync(state) && !existsSync(path.join(bundleRoot, bundleStateFileName))) {
        await mkdir(bundleRoot, { recursive: true });
        const saved: unknown = JSON.parse(await readFile(state, "utf8"));
        await writeFile(path.join(bundleRoot, bundleStateFileName), `${JSON.stringify(migrateWorkspaceTokens(saved), null, 2)}\n`, "utf8");
    }

    function migrateWorkspaceTokens(value: unknown): unknown {
        if (typeof value === "string") {
            return value.replaceAll("{{bundleWorkspace}}", "{{usersWorkspace}}");
        }
        if (Array.isArray(value)) {
            return value.map(migrateWorkspaceTokens);
        }
        if (value != null && typeof value === "object") {
            return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, migrateWorkspaceTokens(item)]));
        }
        return value;
    }
    // A sync record can name folders of the user's too (a dev bundle's logs or temp): only the bundle's own go.
    const copied = (await syncedEntryNames(usersRoot)).filter((name) => bundleNames.has(name));
    for (const name of [...copied, bundleStateFileName, syncMetadataFileName, workspaceSentinelFileName]) {
        await rm(path.join(usersRoot, name), { recursive: true, force: true, maxRetries: 3 });
    }
}

/** The top-level entries the last sync copied from the bundle into `root`. */
export async function syncedEntryNames(root: string): Promise<string[]> {
    const metadata = await readFile(path.join(root, syncMetadataFileName), "utf8")
        .then((text) => JSON.parse(text) as { entries?: Array<{ path?: unknown }> })
        .catch(() => null);
    const entries = Array.isArray(metadata?.entries) ? metadata.entries : [];
    const names = entries.map((entry) => String(entry.path ?? "").split(/[\\/]/)[0] ?? "");
    return [...new Set(names.filter((name) => name && name !== "." && name !== ".." && name !== syncMetadataFileName && name !== workspaceSentinelFileName))];
}
