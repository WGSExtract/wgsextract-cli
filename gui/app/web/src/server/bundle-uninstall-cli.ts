#!/usr/bin/env node
/**
 * Runs a bundle's uninstall steps without the app's window: the Windows uninstaller and the Linux app's
 * Uninstall run this so that uninstalling the app also removes what its setup installed (the same steps as
 * /api/uninstall/stream). Prints the steps' output; exits 1 if any step failed.
 *
 * Usage: bundle-uninstall-cli.js [--app-data-name <name>] [--print-workspace] [--remove-workspace] [--remove-app-data] <bundle>
 * --app-data-name names the app's data folder (default WGSExtract-gui), which holds the CLI runtime that
 * setup installed, the app's settings and the bundle's hidden {{bundleWorkspace}}. Unless a step failed, the
 * bundle workspace is then deleted. The users workspace ({{usersWorkspace}}, ~/wgs-extract), and any folder its
 * settings file names inside the app's data folder (an older install's reference library, say), hold the
 * user's settings, libraries and outputs: they are deleted only with --remove-workspace (ask the user first;
 * --print-workspace only prints their paths, one per line, or nothing if there are none). --remove-app-data then
 * deletes the app's data folder, keeping whatever the user keeps.
 * WGSEXTRACT_GUI_UNINSTALL_RESULT_FILE, if set, receives the result as JSON (as from the macOS app).
 */
import { existsSync } from "node:fs";
import { readFile, readdir, rm, rmdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { configEditorControls, parseFlatToml } from "../../../shared/rendering.js";
import type { BundleManifest } from "../../../shared/types.js";
import { isSameOrInsidePath } from "../../../shared/workspace-paths.js";
import { loadManifestFromRoot } from "./bundle-loader.js";
import { leaveAppImageEnvironment } from "./appimage-environment.js";
import { errorMessage } from "./errors.js";
import { appSupportDirectory, configPath, resolveUserPath } from "./paths.js";
import { createProcessManager } from "./process-runner.js";
import { runUninstall, type SetupSummary } from "./setup-runner.js";
import { isUsersWorkspace, registerUsersWorkspace, resolveUsersWorkspaceRoot } from "./users-workspace.js";
import { appDataWorkspacesDirectory, bundleWorkspaceRoot, isManagedWorkspace, migrateLegacyAppData } from "./workspace-location.js";

type UninstallResult = SetupSummary & { log: string[] };

function parseArgs(argv: string[]) {
    const args = [...argv];
    const take = (flag: string) => {
        const index = args.indexOf(flag);
        return index < 0 ? false : Boolean(args.splice(index, 1));
    };
    const nameIndex = args.indexOf("--app-data-name");
    const appDataName = nameIndex >= 0 ? args.splice(nameIndex, 2)[1] : undefined;
    const printWorkspace = take("--print-workspace");
    const removeWorkspace = take("--remove-workspace");
    const removeAppData = take("--remove-app-data");
    const [bundle, ...rest] = args;
    if ((nameIndex >= 0 && !appDataName) || !bundle || rest.length > 0) {
        throw new Error("Usage: bundle-uninstall-cli.js [--app-data-name <name>] [--print-workspace] [--remove-workspace] [--remove-app-data] <bundle>");
    }
    return { appDataName, printWorkspace, removeWorkspace, removeAppData, bundle };
}

async function main(argv: string[]): Promise<number> {
    // The uninstall steps run the system's programs, not the AppImage's.
    leaveAppImageEnvironment();
    const { appDataName, printWorkspace, removeWorkspace, removeAppData, bundle } = parseArgs(argv);
    if (appDataName) {
        process.env.WGSEXTRACT_GUI_APP_DATA_NAME = appDataName;
    }
    // An app upgraded from 0.8.6 or earlier and uninstalled before it ever ran still has its settings there.
    await migrateLegacyAppData();
    const manifest = await loadManifestFromRoot(path.resolve(bundle));
    // The webview's data and logs are in folders named for the app's identifier, which the uninstaller removes.
    const appData = appSupportDirectory();
    const bundleWorkspace = bundleWorkspaceRoot(manifest);
    const usersWorkspace = await resolveUsersWorkspaceRoot(manifest);
    // 0.8.5 to 0.8.7 kept the bundle's copy in the users workspace; one never opened since still has it there.
    const stepRoots: string[] = [];
    for (const root of [bundleWorkspace, usersWorkspace]) {
        if (root && !stepRoots.includes(root) && await isManagedWorkspace(root)) {
            stepRoots.push(root);
        }
    }
    const userFolders = await usersFolders(manifest, bundleWorkspace, usersWorkspace, appData);
    if (printWorkspace) {
        for (const folder of userFolders) {
            console.log(folder);
        }
        return 0;
    }
    const result: UninstallResult = { status: "ok", results: [], log: [] };
    const note = (line: string) => {
        console.log(line);
        result.log.push(line);
    };
    // Only the app's own workspaces have steps to run; a folder of the same name that isn't one is never touched.
    if (stepRoots.length === 0) {
        note(`No workspace at ${bundleWorkspace}; setup never ran, nothing to remove.`);
    }
    for (const root of stepRoots) {
        if (result.status === "ok") {
            Object.assign(result, await runWorkspaceSteps(root));
        }
    }
    if (result.status !== "ok") {
        // Kept so the user can retry or read what failed.
        note(`Uninstall steps failed; kept ${[bundleWorkspace, ...userFolders].join(", ")}`);
        await writeResult(result);
        return 1;
    }
    const kept = removeWorkspace ? [] : userFolders.filter((folder) => existsSync(folder));
    if (removeWorkspace) {
        for (const folder of userFolders) {
            await rm(folder, { recursive: true, force: true });
            note(`Removed ${folder}`);
        }
    }
    // The bundle workspace is the app's own: removed, but for any folder of the user's in it.
    if (existsSync(bundleWorkspace)) {
        await removeExcept(bundleWorkspace, kept);
        note(kept.some((folder) => isInside(bundleWorkspace, folder)) ? `Removed ${bundleWorkspace}, except your folders in it` : `Removed ${bundleWorkspace}`);
        await rmdir(appDataWorkspacesDirectory()).catch(() => undefined);
    }
    if (removeAppData && existsSync(appData)) {
        await removeExcept(appData, kept);
        note(kept.some((folder) => isInside(appData, folder)) ? `Removed ${appData}, except your folders in it` : `Removed ${appData}`);
    }
    await writeResult(result);
    return 0;
}

/**
 * The folders of the user's that uninstalling keeps unless they say otherwise: the users workspace, and the
 * folders its settings file names in the app's data folder (where releases before 0.8.5 kept the libraries).
 */
async function usersFolders(manifest: BundleManifest, bundleWorkspace: string, usersWorkspace: string | null, appData: string): Promise<string[]> {
    const folders: string[] = [];
    if (usersWorkspace && existsSync(usersWorkspace) && (await isUsersWorkspace(usersWorkspace) || await isManagedWorkspace(usersWorkspace))) {
        folders.push(usersWorkspace);
    }
    registerUsersWorkspace(bundleWorkspace, usersWorkspace ?? bundleWorkspace);
    for (const control of configEditorControls(manifest)) {
        if (!control.configFile) {
            continue;
        }
        // Where the app keeps it, or where an earlier release did if the app hasn't moved it since.
        const candidates = [configPath(control, undefined, bundleWorkspace), ...(control.configFile.previousPaths ?? []).map((previous) => resolveUserPath(previous, bundleWorkspace))];
        const file = candidates.find((candidate) => existsSync(candidate));
        const values = parseFlatToml(file ? await readFile(file, "utf8").catch(() => "") : "");
        for (const setting of control.settings ?? []) {
            const value = values[setting.key ?? setting.id];
            if (setting.kind === "path" && typeof value === "string" && path.isAbsolute(value) && existsSync(value) &&
                isInside(appData, value) && !folders.some((folder) => isSameOrInsidePath(folder, value))) {
                folders.push(value);
            }
        }
    }
    return folders;
}

function isInside(directory: string, candidate: string) {
    const relative = path.relative(directory, candidate);
    return relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

/** Deletes `directory` but none of the `kept` folders that are in it. */
async function removeExcept(directory: string, kept: string[]) {
    const inside = kept.filter((root) => isInside(directory, root));
    if (inside.length === 0) {
        await rm(directory, { recursive: true, force: true });
        return;
    }
    if (inside.some((root) => path.relative(directory, root) === "")) {
        return;
    }
    for (const entry of await readdir(directory)) {
        await removeExcept(path.join(directory, entry), inside);
    }
}

/** Runs the steps from the workspace's own copy of the bundle: the scripts setup ran. */
async function runWorkspaceSteps(workspace: string) {
    const processManager = createProcessManager({ maxOutputBytes: 1_048_576, maxErrorBytes: 65_536 });
    return runUninstall(await loadManifestFromRoot(workspace), workspace, processManager.runProcess, (event) => {
        if (event.type === "step-start") {
            console.log(`==> ${event.step.label ?? event.step.id}`);
        }
        else if (event.type === "output") {
            process.stdout.write(event.text);
        }
        else if (event.type === "step-complete") {
            console.log(`<== ${event.result.id}: ${event.result.status} (exit code ${event.result.exitCode})`);
        }
    });
}

async function writeResult(result: UninstallResult) {
    const resultFile = process.env.WGSEXTRACT_GUI_UNINSTALL_RESULT_FILE;
    if (resultFile) {
        await writeFile(resultFile, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    }
}

main(process.argv.slice(2)).then(
    (code) => {
        process.exitCode = code;
    },
    async (error) => {
        console.error(`Uninstall failed: ${errorMessage(error)}`);
        process.exitCode = 1;
        await writeResult({ status: "failed", results: [], error: errorMessage(error), log: [] });
    },
);
