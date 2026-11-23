import { execFile } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { platform } from "node:os";
import path from "node:path";
import { BUNDLE_FILE_EXTENSIONS } from "./bundle-archive.js";
import { resolveUserPath } from "./paths.js";
import { apiTokenHeader } from "./request-guard.js";

/** `bundle` picks a bundle's manifest.json or a bundle archive; native dialogs cannot offer folders and files at once. */
export type PathPickerKind = "directory" | "file" | "bundle";

/** The Tauri shell's native file dialog listener, authenticated with the shared API token. */
export interface NativePickerEndpoint {
    port: number;
    token: string;
}

export interface PathPickerOptions {
    kind?: unknown;
    title?: unknown;
    defaultPath?: unknown;
    multiple?: unknown;
    bundleRoot?: string;
    nativePicker?: NativePickerEndpoint | undefined;
}

export type PathPickResult =
    | { path: string; paths?: string[]; kind: PathPickerKind; cancelled: false }
    | { kind: PathPickerKind; cancelled: true };

export const macOSPickerScript = `
on run argv
  set pickerKind to item 1 of argv
  set dialogTitle to item 2 of argv
  set defaultPath to item 3 of argv
  set allowMultiple to "false"
  if (count of argv) > 3 then set allowMultiple to item 4 of argv
  set fileTypes to {}
  if (count of argv) > 4 then set fileTypes to items 5 thru -1 of argv

  activate

  if pickerKind is "file" and allowMultiple is "true" then
    if defaultPath is not "" then
      set defaultLocation to POSIX file defaultPath as alias
      if fileTypes is not {} then
        set chosenItems to choose file with prompt dialogTitle default location defaultLocation of type fileTypes with multiple selections allowed
      else
        set chosenItems to choose file with prompt dialogTitle default location defaultLocation with multiple selections allowed
      end if
    else
      if fileTypes is not {} then
        set chosenItems to choose file with prompt dialogTitle of type fileTypes with multiple selections allowed
      else
        set chosenItems to choose file with prompt dialogTitle with multiple selections allowed
      end if
    end if
    set outputLines to {}
    repeat with anItem in chosenItems
      set end of outputLines to POSIX path of anItem
    end repeat
    set AppleScript's text item delimiters to linefeed
    return outputLines as text
  end if

  if defaultPath is not "" then
    set defaultLocation to POSIX file defaultPath as alias
    if pickerKind is "directory" then
      set chosenItem to choose folder with prompt dialogTitle default location defaultLocation
    else if fileTypes is not {} then
      set chosenItem to choose file with prompt dialogTitle default location defaultLocation of type fileTypes
    else
      set chosenItem to choose file with prompt dialogTitle default location defaultLocation
    end if
  else
    if pickerKind is "directory" then
      set chosenItem to choose folder with prompt dialogTitle
    else if fileTypes is not {} then
      set chosenItem to choose file with prompt dialogTitle of type fileTypes
    else
      set chosenItem to choose file with prompt dialogTitle
    end if
  end if

  return POSIX path of chosenItem
end run
`;

export async function pickPath(options: PathPickerOptions = {}): Promise<PathPickResult> {
    const kind = normalizePathPickerKind(options.kind);
    const multiple = kind === "file" && options.multiple === true;
    const title = String(options.title || (kind === "directory" ? "Choose directory" : multiple ? "Choose files" : "Choose file"));
    const defaultLocation = defaultLocationForPath(options.defaultPath, options.bundleRoot);
    if (options.nativePicker) {
        const result = await runTauriNativePicker(options.nativePicker, kind, title, defaultLocation, multiple);
        if (!result) {
            return { kind, cancelled: true };
        }
        return multiple ? { path: result.paths.join("\n"), paths: result.paths, kind, cancelled: false } : { path: result.path, kind, cancelled: false };
    }
    if (platform() !== "darwin") {
        throw new Error("Native file and directory picking requires the Tauri desktop app on this platform.");
    }
    const result = await runMacOSPicker(kind, title, defaultLocation, multiple);
    if (!result) {
        return { kind, cancelled: true };
    }
    if (multiple) {
        const paths = result.split(/\r?\n/).map((entry) => entry.trim()).filter(Boolean);
        return paths.length ? { path: paths.join("\n"), paths, kind, cancelled: false } : { kind, cancelled: true };
    }
    return { path: result, kind, cancelled: false };
}

function normalizePathPickerKind(value: unknown): PathPickerKind {
    if (value === "directory" || value === "folder") {
        return "directory";
    }
    if (value === "file" || value === "bundle") {
        return value;
    }
    throw new Error("Path picker kind must be file, directory, or bundle.");
}

async function runMacOSPicker(kind: PathPickerKind, title: string, defaultLocation: string, multiple: boolean): Promise<string | null> {
    return new Promise((resolve, reject) => {
        const fileTypes = kind === "bundle" ? BUNDLE_FILE_EXTENSIONS : [];
        execFile("/usr/bin/osascript", ["-e", macOSPickerScript, kind, title, defaultLocation, multiple ? "true" : "false", ...fileTypes], { encoding: "utf8" }, (error, stdout, stderr) => {
            if (!error) {
                resolve(stdout.trim());
                return;
            }
            if (isUserCancelled(error, stderr)) {
                resolve(null);
                return;
            }
            reject(new Error(stderr.trim() || error.message));
        });
    });
}

/** Reads and removes the Tauri picker endpoint from the environment so child commands never inherit it. */
export function takeNativePickerEndpoint(token: string, environment: NodeJS.ProcessEnv = process.env): NativePickerEndpoint | undefined {
    const rawPort = environment.GFC_NATIVE_PICKER_PORT;
    delete environment.GFC_NATIVE_PICKER_PORT;
    if (rawPort == null || rawPort === "") {
        return undefined;
    }
    const port = Number(rawPort);
    if (!Number.isInteger(port) || port <= 0 || port > 65535) {
        throw new Error(`Invalid GFC_NATIVE_PICKER_PORT: ${rawPort}`);
    }
    return { port, token };
}

async function runTauriNativePicker(endpoint: NativePickerEndpoint, kind: PathPickerKind, title: string, defaultLocation: string, multiple: boolean): Promise<{ path: string; paths: string[] } | null> {
    const params = new URLSearchParams({
        kind,
        title,
        defaultPath: defaultLocation ? path.resolve(defaultLocation) : "",
        ...(multiple ? { multiple: "true" } : {}),
    });
    const response = await fetch(`http://127.0.0.1:${endpoint.port}/pick?${params}`, {
        headers: { [apiTokenHeader]: endpoint.token },
    });
    const payload = await response.json() as { cancelled?: boolean; path?: unknown; paths?: unknown; error?: unknown };
    if (!response.ok) {
        throw new Error(String(payload?.error || `Native picker failed with HTTP ${response.status}`));
    }
    if (payload.cancelled) {
        return null;
    }
    const paths = Array.isArray(payload.paths)
        ? payload.paths.map((entry) => String(entry || "").trim()).filter(Boolean)
        : String(payload.path || "").split(/\r?\n/).map((entry) => entry.trim()).filter(Boolean);
    if (!paths.length) {
        return null;
    }
    return { path: paths[0] ?? "", paths };
}

function isUserCancelled(error: { code?: string | number | null } | null, stderr: string | Buffer | undefined): boolean {
    return String(error?.code) === "1" && /User canceled|User cancelled/i.test(String(stderr ?? ""));
}

function defaultLocationForPath(rawPath: unknown, bundleRoot: string | undefined): string {
    const candidate = normalizeDefaultPath(rawPath, bundleRoot);
    if (!candidate) {
        return existingDirectory(bundleRoot) ?? "";
    }
    const exactDirectory = existingDirectory(candidate);
    if (exactDirectory) {
        return exactDirectory;
    }
    const exactParent = existingParentDirectory(candidate);
    if (exactParent) {
        return exactParent;
    }
    return existingDirectory(bundleRoot) ?? "";
}

function normalizeDefaultPath(rawPath: unknown, bundleRoot: string | undefined): string {
    const first = typeof rawPath === "string" ? (rawPath.split(/\r?\n/).map((line) => line.trim()).find(Boolean) ?? "") : "";
    if (!first) {
        return "";
    }
    return resolveUserPath(first, bundleRoot || process.cwd());
}

function existingParentDirectory(candidate: string): string | undefined {
    let current = path.dirname(candidate);
    while (current && current !== path.dirname(current)) {
        const directory = existingDirectory(current);
        if (directory) {
            return directory;
        }
        current = path.dirname(current);
    }
    return existingDirectory(current);
}

function existingDirectory(candidate: unknown): string | undefined {
    if (typeof candidate !== "string" || !candidate || !existsSync(candidate)) {
        return undefined;
    }
    const info = statSync(candidate);
    if (info.isDirectory()) {
        return candidate;
    }
    if (info.isFile()) {
        return path.dirname(candidate);
    }
    return undefined;
}
