/** The app's own, hidden copy of the bundle and its state: in the app's data folder, never moved by the user. */
export const bundleWorkspaceToken = "{{bundleWorkspace}}";
/** The user's visible folder (~/wgs-extract) for the settings file and the folders they manage; they may move it. */
export const usersWorkspaceToken = "{{usersWorkspace}}";

/** The workspaces a path may be shown relative to. Without a users workspace, the bundle's stands in for it. */
export interface WorkspaceRoots {
    bundle: string;
    users?: string | undefined;
}

/** Forward slashes, with no `.` or `..` segments, repeated slashes or trailing slash. */
function normalizePath(candidate: string) {
    const [first = "", ...rest] = candidate.replaceAll("\\", "/").split("/");
    const segments: string[] = [];
    for (const segment of rest) {
        if (segment === "..") {
            segments.pop();
        }
        else if (segment && segment !== ".") {
            segments.push(segment);
        }
    }
    return [first, ...segments].join("/");
}

/** `value` relative to `root` with forward slashes: "" for the root itself, null when it is outside. */
function pathRelativeToRoot(value: string, root: string): string | null {
    const normalizedValue = normalizePath(value);
    const normalizedRoot = normalizePath(root);
    if (!normalizedRoot) {
        return null;
    }
    // Drive-letter paths are Windows paths, which compare without case.
    const comparable = (candidate: string) => /^[A-Za-z]:/.test(normalizedRoot) ? candidate.toLowerCase() : candidate;
    if (comparable(normalizedValue) === comparable(normalizedRoot)) {
        return "";
    }
    return comparable(normalizedValue).startsWith(`${comparable(normalizedRoot)}/`) ? normalizedValue.slice(normalizedRoot.length + 1) : null;
}

/**
 * How the GUI shows a path: anything in a workspace is written relative to its token, the innermost
 * workspace when one holds the other.
 */
export function displayWorkspacePath(value: string, roots: WorkspaceRoots): string {
    const candidates: Array<[string, string]> = [[bundleWorkspaceToken, roots.bundle]];
    if (roots.users) {
        candidates.push([usersWorkspaceToken, roots.users]);
    }
    let shown: { token: string; relative: string; depth: number } | null = null;
    for (const [token, root] of candidates) {
        const relative = pathRelativeToRoot(value, root);
        const depth = normalizePath(root).length;
        if (relative != null && (!shown || depth > shown.depth)) {
            shown = { token, relative, depth };
        }
    }
    if (!shown) {
        return value;
    }
    return shown.relative ? `${shown.token}/${shown.relative}` : shown.token;
}

/** Whether `candidate` is `parent` itself or inside it. */
export function isSameOrInsidePath(parent: string, candidate: string): boolean {
    return pathRelativeToRoot(candidate, parent) != null;
}

/** The same path after `from` moved to `to`; a path outside `from` is returned unchanged. */
export function relocatePath(value: string, from: string, to: string): string {
    const relative = pathRelativeToRoot(value, from);
    if (relative == null) {
        return value;
    }
    const separator = to.includes("\\") ? "\\" : "/";
    return relative ? `${to}${separator}${relative.replaceAll("/", separator)}` : to;
}
