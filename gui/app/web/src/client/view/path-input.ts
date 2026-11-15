import { displayWorkspacePath } from "../../../../shared/workspace-paths.js";
import { escapeAttribute } from "../dom.js";
import { state } from "../state.js";

/**
 * A path field's shared attributes. Paths stay left-to-right in RTL locales, and the full path is the field's
 * hover text, because a long path is cut off in the field.
 */
export function pathInputAttributes(path: unknown, extraClass = ""): string {
    const shown = displayWorkspacePath(String(path ?? ""), state.bundleRootPath);
    return `class="${extraClass ? `${extraClass} ` : ""}path-input" dir="ltr" value="${escapeAttribute(shown)}"${shown ? ` title="${escapeAttribute(shown)}"` : ""}`;
}

/** Scrolls each unfocused path field to its end, so a long path shows its file name rather than its first folders. */
export function revealPathInputEnds(root: ParentNode): void {
    for (const input of root.querySelectorAll<HTMLInputElement>("input.path-input")) {
        if (input !== document.activeElement) {
            input.scrollLeft = input.scrollWidth;
        }
    }
}
