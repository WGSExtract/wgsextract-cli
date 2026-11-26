import { displayWorkspacePath, resolveWorkspaceDisplayPath } from "../../../../shared/workspace-paths.js";
import { workspaceRoots } from "../model.js";
import { escapeAttribute } from "../dom.js";


/**
 * A path field's shared attributes. Paths stay left-to-right in RTL locales, and the full path is the field's
 * hover text, because a long path is cut off in the field.
 */
export function pathInputAttributes(path: unknown, extraClass = "", display?: { resolvedPath: string | undefined }): string {
    const value = String(path ?? "");
    const roots = workspaceRoots();
    const resolved = display ? resolveWorkspaceDisplayPath(value, roots) : value;
    const absolute = /^(?:\/|[A-Za-z]:[\\/]|\\\\)/.test(resolved);
    const shown = display
        ? (!absolute && value && display.resolvedPath ? display.resolvedPath : resolved)
        : displayWorkspacePath(value, roots);
    const tooltip = display ? (value === shown ? displayWorkspacePath(shown, roots) : value) : shown;
    return `class="${extraClass ? `${extraClass} ` : ""}path-input" dir="ltr" value="${escapeAttribute(shown)}"${shown ? ` title="${escapeAttribute(tooltip === shown ? shown : `${shown}\n${tooltip}`)}"` : ""}`;
}

/** Scrolls each unfocused path field to its end, so a long path shows its file name rather than its first folders. */
export function revealPathInputEnds(root: ParentNode): void {
    for (const input of root.querySelectorAll<HTMLInputElement>("input.path-input")) {
        if (input !== document.activeElement) {
            input.scrollLeft = input.scrollWidth;
        }
    }
}
