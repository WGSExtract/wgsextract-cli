import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { copyFile, readFile, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { configEditorControls, serializeFlatToml } from "../../../shared/rendering.js";
import type { BundleManifest, ConfigBootstrapScriptSpec, ControlSpec, LooseRecord, StringMap } from "../../../shared/types.js";
import { writeFileAtomically } from "./atomic-file.js";
import { settingKey } from "./config-settings.js";
import { parseConfigToml as parseToml, readOptionalFlatToml, serializeConfigToml as serializeToml } from "./config-toml.js";
import { errnoCode } from "./errors.js";
import { bundleEnvironment, configPath, expandPathTokens, resolveUserPath } from "./paths.js";
import { resolvePlatformScriptPath } from "./platform-scripts.js";
import { withSettingsFileLock } from "./settings-lock.js";
import { assertCurrentUsersWorkspace } from "./users-workspace.js";
import { assertSettingsFileUnlocked, withWorkspaceMove, withWorkspaceUse } from "./workspace-activity.js";
import { saveMovedConfigPath } from "./workspace-location.js";

const bootstrapScriptTimeoutMs = 30_000;
const inheritedBootstrapEnvironmentKeys = [
    "PATH",
    "Path",
    "PSModulePath",
    "HOME",
    "USERPROFILE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "LOCALAPPDATA",
    "APPDATA",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
];

type BootstrapScript = ConfigBootstrapScriptSpec;

interface ConfigDocument {
    url: string;
    contents: string;
}

const bootstrapDocumentCache = new Map<string, Promise<ConfigDocument>>();

const bootstrapQueues = new Map<string, Promise<void>>();
/**
 * Creates or completes the bundle's config files. Calls for one bundle run one at a time: the startup
 * preload and the window's first request both load the bundle, and two first-run bootstraps would each
 * see no config file and rename onto the same target at once, which Windows refuses (EPERM).
 */
export function bootstrapConfigFiles(manifest: BundleManifest, bundleRoot: string, configFilePaths: StringMap): Promise<void> {
    const key = path.resolve(bundleRoot);
    const run = (bootstrapQueues.get(key) ?? Promise.resolve())
        .then(() => bootstrapConfigFilesNow(manifest, bundleRoot, configFilePaths));
    const settled = run.then(() => undefined, () => undefined);
    bootstrapQueues.set(key, settled);
    void settled.then(() => {
        if (bootstrapQueues.get(key) === settled) {
            bootstrapQueues.delete(key);
        }
    });
    return run;
}
async function bootstrapConfigFilesNow(manifest: BundleManifest, bundleRoot: string, configFilePaths: StringMap) {
    for (const control of configEditorControls(manifest)) {
        if (previousConfigFile(control, bundleRoot)) {
            await withWorkspaceMove(async () => {
                await assertCurrentUsersWorkspace(manifest, bundleRoot);
                await movePreviousConfigFile(control, bundleRoot);
            });
        }
    }
    await withWorkspaceUse(async () => {
        await assertCurrentUsersWorkspace(manifest, bundleRoot);
        await bootstrapMissingConfigFiles(manifest, bundleRoot, configFilePaths);
    });
}
async function bootstrapMissingConfigFiles(manifest: BundleManifest, bundleRoot: string, configFilePaths: StringMap) {
    for (const control of configEditorControls(manifest)) {
        const configFile = control.configFile;
        const bootstrap = configFile?.bootstrap;
        if (!configFile || !bootstrap || bootstrap.mode === "none") {
            continue;
        }
        const defaultURL = configPath(control, configFilePaths[control.id], bundleRoot);
        const mode = bootstrap.mode ?? "createIfMissing";
        const document = await bootstrapDocument(control, bundleRoot, defaultURL, bootstrap.script);
        const defaults = parseToml(document.contents);
        const existing = await readOptionalFlatToml(document.url);
        if (shouldSkipBootstrap(mode, existing, Object.keys(defaults))) {
            continue;
        }
        await bootstrapToml(mode, document.url, defaults);
    }
}
/**
 * Moves the settings file from where an earlier version kept it (configFile.previousPaths) to its declared
 * path, unless that already exists, then removes the old folder if that leaves it empty.
 */
async function movePreviousConfigFile(control: ControlSpec, bundleRoot: string) {
    const move = previousConfigFile(control, bundleRoot);
    if (!move) return;
    const { previous, target } = move;
    await assertSettingsFileUnlocked(previous);
    await assertSettingsFileUnlocked(target);
    const canonicalPrevious = await realpath(path.dirname(previous));
    // The move lease excludes admitted writers. Recheck under the target lock
    // for a saver that uses the shared lock but doesn't know about the lease.
    const moved = await withSettingsFileLock(target, async (file) => {
        if (existsSync(file)) return false;
        await moveFile(previous, file);
        try {
            await saveMovedConfigPath(path.join(canonicalPrevious, path.basename(previous)), target);
        } catch (error) {
            await moveFile(file, previous);
            throw error;
        }
        return true;
    });
    if (!moved) return;
    const folder = path.dirname(previous);
    const relative = path.relative(bundleRoot, folder);
    if (relative && !relative.startsWith("..") && !path.isAbsolute(relative)) {
        // rmdir only removes an empty folder; anything the user put beside the file stays.
        await rmdir(folder).catch(() => undefined);
    }
}
function previousConfigFile(control: ControlSpec, bundleRoot: string) {
    const declaredPath = control.configFile?.path;
    if (!declaredPath) {
        return;
    }
    const target = resolveUserPath(declaredPath, bundleRoot);
    if (existsSync(target)) {
        return;
    }
    for (const previousPath of control.configFile?.previousPaths ?? []) {
        const previous = resolveUserPath(previousPath, bundleRoot);
        if (!existsSync(previous)) {
            continue;
        }
        return { previous, target };
    }
}
function shouldSkipBootstrap(mode: string, existing: Record<string, unknown> | null, defaultKeys: string[]) {
    if (mode === "createIfMissing") {
        return existing != null;
    }
    if (mode !== "mergeMissing" || existing == null) {
        return false;
    }
    return defaultKeys.every((key) => Object.hasOwn(existing, key));
}
async function bootstrapDocument(control: ControlSpec, bundleRoot: string, defaultURL: string, script: BootstrapScript | undefined): Promise<ConfigDocument> {
    if (!script) {
        return {
            url: defaultURL,
            contents: serializeFlatToml(Object.fromEntries((control.settings ?? []).map((setting) => [settingKey(setting), setting.value ?? ""]))),
        };
    }
    const cacheKey = JSON.stringify({
        controlID: control.id,
        defaultURL,
        script,
    });
    const cachedDocument = bootstrapDocumentCache.get(cacheKey);
    if (cachedDocument) {
        return cachedDocument;
    }
    const document = loadScriptBootstrapDocument(script, control, bundleRoot, defaultURL)
        .catch((error) => {
            bootstrapDocumentCache.delete(cacheKey);
            throw error;
        });
    bootstrapDocumentCache.set(cacheKey, document);
    return document;
}
async function loadScriptBootstrapDocument(script: BootstrapScript, control: ControlSpec, bundleRoot: string, defaultURL: string): Promise<ConfigDocument> {
    const payload = await runBootstrapScript(script, control, bundleRoot, defaultURL);
    const payloadPath = String(payload.path ?? "").trim();
    return {
        url: payloadPath ? resolveConfigFilePath(payloadPath, bundleRoot) : defaultURL,
        contents: await scriptContents(payload, bundleRoot),
    };
}
async function runBootstrapScript(script: BootstrapScript, control: ControlSpec, bundleRoot: string, defaultURL: string): Promise<LooseRecord> {
    resolveBundledPath(script.path, bundleRoot, false);
    const scriptPath = await resolvePlatformScriptPath(script.path, bundleRoot);
    if (!existsSync(scriptPath)) {
        throw new Error(`Config bootstrap script does not exist: ${scriptPath}`);
    }
    const workingDirectory = resolveBundledPath(script.workingDirectory ?? "", bundleRoot, false);
    const command = bootstrapCommand(scriptPath);
    const args = [
        ...command.args,
        ...(script.arguments ?? []).map((argument) => expandConfigPath(argument, bundleRoot, defaultURL)),
    ];
    const stdout = await new Promise<string>((resolve, reject) => {
        execFile(command.executable, args, {
            cwd: workingDirectory,
            env: scriptEnvironment(script, control, bundleRoot, defaultURL),
            maxBuffer: 1024 * 1024,
            timeout: bootstrapScriptTimeoutMs,
            killSignal: "SIGTERM",
        }, (error, stdout, stderr) => {
            if (error) {
                const reason = error.killed ? `timed out after ${bootstrapScriptTimeoutMs}ms` : "failed";
                reject(new Error(`Config bootstrap script ${reason}: ${scriptPath}\n${[stdout, stderr].filter(Boolean).join("\n")}`));
                return;
            }
            resolve(stdout);
        });
    });
    let payload: unknown;
    try {
        payload = JSON.parse(stdout.trim());
    }
    catch (_error) {
        throw new Error(`Config bootstrap script did not return valid JSON: ${scriptPath}`);
    }
    if (payload == null || typeof payload !== "object" || Array.isArray(payload)) {
        throw new Error(`Config bootstrap script did not return a JSON object: ${scriptPath}`);
    }
    return payload as LooseRecord;
}
function bootstrapCommand(scriptPath: string) {
    if (process.platform === "win32" && path.extname(scriptPath).toLowerCase() === ".ps1") {
        return {
            executable: "powershell.exe",
            args: ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", scriptPath],
        };
    }
    return { executable: bootstrapShell(), args: [scriptPath] };
}
function bootstrapShell() {
    if (process.platform !== "win32") {
        return "/bin/sh";
    }
    const gitShell = "C:\\Program Files\\Git\\bin\\sh.exe";
    return existsSync(gitShell) ? gitShell : "sh";
}
function scriptEnvironment(script: BootstrapScript, control: ControlSpec, bundleRoot: string, defaultURL: string): NodeJS.ProcessEnv {
    return {
        ...Object.fromEntries(inheritedBootstrapEnvironmentKeys.flatMap((key) => process.env[key] == null ? [] : [[key, process.env[key]]])),
        ...bundleEnvironment(bundleRoot),
        WGSEXTRACT_GUI_CONFIG_PATH: defaultURL,
        WGSEXTRACT_GUI_CONFIG_DIR: path.dirname(defaultURL),
        WGSEXTRACT_GUI_CONFIG_CONTROL_ID: control.id,
        WGSEXTRACT_GUI_CONFIG_CONTROL_LABEL: control.label,
        WGSEXTRACT_GUI_DRY_RUN: "0",
        ...Object.fromEntries(Object.entries(script.environment ?? {}).map(([key, value]) => [
            key,
            expandConfigPath(value, bundleRoot, defaultURL),
        ])),
    };
}
async function scriptContents(payload: LooseRecord, bundleRoot: string): Promise<string> {
    if (payload.contents != null) {
        return String(payload.contents);
    }
    const contentsPath = String(payload.contentsPath ?? "").trim();
    if (contentsPath) {
        return readFile(resolveConfigFilePath(contentsPath, bundleRoot), "utf8");
    }
    if (payload.values != null && typeof payload.values === "object") {
        return serializeFlatToml(payload.values as LooseRecord);
    }
    return "";
}
async function bootstrapToml(mode: string, filePath: string, defaults: Record<string, unknown>) {
    if (mode !== "createIfMissing" && mode !== "mergeMissing") {
        throw new Error(`Unsupported config bootstrap mode: ${mode}`);
    }
    await withSettingsFileLock(filePath, (target) => bootstrapTomlLocked(mode, target, defaults));
}
async function bootstrapTomlLocked(mode: string, filePath: string, defaults: Record<string, unknown>) {
    const existing = await readOptionalFlatToml(filePath);
    if (mode === "createIfMissing" && existing != null) {
        return;
    }
    const next = mode === "mergeMissing" && existing
        ? { ...existing }
        : { ...defaults };
    if (mode === "mergeMissing" && existing) {
        for (const [key, value] of Object.entries(defaults)) {
            // A key that is there but blank is the user's choice: a blank log directory turns logging off.
            if (!Object.hasOwn(existing, key)) {
                next[key] = value;
            }
        }
    }
    await writeFileAtomically(filePath, serializeToml(next));
}

function resolveConfigFilePath(value: string, bundleRoot: string) {
    const expanded = expandPathTokens(value, bundleRoot);
    return path.isAbsolute(expanded) ? expanded : path.resolve(bundleRoot, expanded);
}
function expandConfigPath(value: unknown, bundleRoot: string, configURL: string) {
    return expandPathTokens(String(value ?? ""), bundleRoot).replaceAll("{{configPath}}", configURL).replaceAll("{{configDir}}", path.dirname(configURL));
}
function resolveBundledPath(value: string, bundleRoot: string, mustExist: boolean) {
    if (!value) {
        return bundleRoot;
    }
    if (path.isAbsolute(value) || value.split(/[\\/]/).includes("..")) {
        throw new Error(`Config bootstrap script path must be relative and stay inside the bundle: ${value}`);
    }
    const candidate = path.resolve(bundleRoot, value);
    const relative = path.relative(bundleRoot, candidate);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
        throw new Error(`Config bootstrap script path must be relative and stay inside the bundle: ${value}`);
    }
    if (mustExist && !existsSync(candidate)) {
        throw new Error(`Config bootstrap script does not exist: ${candidate}`);
    }
    return candidate;
}

/** Renames `from` to `to`, or copies it there and deletes it when they are on different disks. */
async function moveFile(from: string, to: string) {
    try {
        await rename(from, to);
    }
    catch (error) {
        if (errnoCode(error) !== "EXDEV") {
            throw error;
        }
        await copyFile(from, to);
        await rm(from, { force: true });
    }
}
