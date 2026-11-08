/**
 * Path fields keep absolute paths (they go to settings files and commands), but show the bundle workspace
 * as its `{{bundleWorkspace}}` token so long per-user prefixes don't crowd out the part that matters.
 */
export const bundleWorkspaceToken = "{{bundleWorkspace}}";

/** Shows `value` with a leading workspace folder replaced by the `{{bundleWorkspace}}` token. */
export function displayWorkspacePath(value: unknown, workspaceRoot: string | undefined): string {
    const text = String(value ?? "");
    const root = trimTrailingSeparators(workspaceRoot ?? "");
    if (!root || text.length < root.length) {
        return text;
    }
    const head = text.slice(0, root.length);
    const sameRoot = isWindowsPath(root) ? head.toLowerCase() === root.toLowerCase() : head === root;
    const rest = text.slice(root.length);
    return sameRoot && (rest === "" || /^[\\/]/.test(rest)) ? `${bundleWorkspaceToken}${rest}` : text;
}

/** Turns a leading `{{bundleWorkspace}}` token typed or shown in a path field back into the workspace folder. */
export function expandWorkspacePath(value: string, workspaceRoot: string | undefined): string {
    const root = trimTrailingSeparators(workspaceRoot ?? "");
    if (!root || !value.startsWith(bundleWorkspaceToken)) {
        return value;
    }
    const rest = value.slice(bundleWorkspaceToken.length);
    return `${root}${isWindowsPath(root) ? rest.replaceAll("/", "\\") : rest}`;
}

function isWindowsPath(value: string): boolean {
    return /^[A-Za-z]:/.test(value) || value.startsWith("\\\\");
}

function trimTrailingSeparators(value: string): string {
    return value.length > 1 ? value.replace(/(?<!^[A-Za-z]:)[\\/]+$/, "") : value;
}
