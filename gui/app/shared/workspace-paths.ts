export const workspaceToken = "{{bundleWorkspace}}";

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

/** How the GUI shows a path: anything in the workspace is written relative to {{bundleWorkspace}}. */
export function displayWorkspacePath(value: string, workspaceRoot: string): string {
    const relative = pathRelativeToRoot(value, workspaceRoot);
    if (relative == null) {
        return value;
    }
    return relative ? `${workspaceToken}/${relative}` : workspaceToken;
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
