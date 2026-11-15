#!/usr/bin/env node
/**
 * Runs a bundle's uninstall steps on the app's workspace for it, without the app's window: the
 * Windows uninstaller and the Linux app's Uninstall run this so that uninstalling the app also
 * removes what its setup installed (the same steps as /api/uninstall/stream). Prints the steps'
 * output; exits 1 if any step failed.
 *
 * Usage: bundle-uninstall-cli.js [--app-data-name <name>] [--print-workspace] [--remove-workspace] [--remove-app-data] <bundle>
 * --app-data-name names the app's data folder (default WGSExtract-gui), which holds the CLI runtime that
 * setup installed and the app's settings. The bundle's workspaces are where it is now and, if it was moved or the bundle names no
 * defaultWorkspace, in the app's data folder. They hold the user's libraries, settings and outputs, so
 * they are deleted only with --remove-workspace (ask the user first; --print-workspace only prints
 * their paths, one per line, or nothing if there are none). Unless a step failed, --remove-workspace
 * then deletes them and --remove-app-data the app's data folder, keeping any workspace in it.
 * WGSEXTRACT_GUI_UNINSTALL_RESULT_FILE, if set, receives the result as JSON (as from the macOS app).
 */
import { existsSync } from "node:fs";
import { readdir, rm, rmdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { loadManifestFromRoot } from "./bundle-loader.js";
import { leaveAppImageEnvironment } from "./appimage-environment.js";
import { errorMessage } from "./errors.js";
import { appSupportDirectory } from "./paths.js";
import { createProcessManager } from "./process-runner.js";
import { runUninstall, type SetupSummary } from "./setup-runner.js";
import { appDataWorkspacesDirectory, isManagedWorkspace, migrateLegacyAppData, resolveWorkspaceRoot, workspaceRootIn } from "./workspace-location.js";

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
    const appDataFolders = [appSupportDirectory()];
    // Where prepareBundleWorkspace keeps the workspace now, which can be outside the app's data folder, and where it is kept by default.
    const workspace = await resolveWorkspaceRoot(manifest);
    const appDataWorkspace = workspaceRootIn(appDataWorkspacesDirectory(), manifest.id);
    const workspaces = [workspace, appDataWorkspace].filter((root, index) => index === 0 || path.relative(workspace, root) !== "");
    const managed: string[] = [];
    for (const root of workspaces) {
        if (await isManagedWorkspace(root)) {
            managed.push(root);
        }
    }
    if (printWorkspace) {
        for (const root of managed) {
            console.log(root);
        }
        return 0;
    }
    const result: UninstallResult = { status: "ok", results: [], log: [] };
    const note = (line: string) => {
        console.log(line);
        result.log.push(line);
    };
    // Only the app's own workspaces have steps to run; a folder of the same name that isn't one is never touched.
    if (managed.length === 0) {
        note(`No workspace at ${workspace}; setup never ran, nothing to remove.`);
    }
    for (const root of managed) {
        if (result.status === "ok") {
            Object.assign(result, await runWorkspaceSteps(root));
        }
    }
    if (result.status !== "ok") {
        if (removeWorkspace || removeAppData) {
            // Kept so the user can retry or read what failed.
            note(`Uninstall steps failed; kept ${[...managed, ...(removeAppData ? appDataFolders : [])].join(", ")}`);
        }
    }
    else {
        if (removeWorkspace) {
            for (const root of managed) {
                await rm(root, { recursive: true, force: true });
                note(`Removed ${root}`);
            }
            await rmdir(appDataWorkspacesDirectory()).catch(() => undefined);
        }
        if (removeAppData) {
            // Whatever is still there: every workspace unless the user chose to delete them, and any folder that isn't the app's.
            const kept = workspaces.filter((root) => existsSync(root));
            for (const appData of appDataFolders.filter((folder) => existsSync(folder))) {
                await removeExcept(appData, kept);
                note(kept.some((root) => isInside(appData, root)) ? `Removed ${appData}, except the workspace in it` : `Removed ${appData}`);
            }
        }
    }
    await writeResult(result);
    return result.status === "ok" ? 0 : 1;
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
