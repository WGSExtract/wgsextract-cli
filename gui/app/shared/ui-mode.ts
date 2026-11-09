import type { BundleManifest, BundlePage, ControlOption, ControlSpec, PageSection, RowSpec } from "./types.js";

/** How much of the GUI is shown: Basic for new users (the default after install), or Advanced for every feature. */
export type UIMode = "basic" | "advanced";

export const uiModes: readonly UIMode[] = ["basic", "advanced"];

/** Anything other than "advanced" is Basic, so a new install, a missing value or a bad one starts in Basic. */
export function normalizeUIMode(value: unknown): UIMode {
    return value === "advanced" ? "advanced" : "basic";
}

/** A page, section, control, option, action or list row marked `"advancedOnly": true` shows only in Advanced mode. */
export function isShownInUIMode(item: object | null | undefined, mode: UIMode): boolean {
    return mode === "advanced" || (item as { advancedOnly?: unknown } | null | undefined)?.advancedOnly !== true;
}

/** Pages Basic shows: not marked advancedOnly, and with something left to show. */
export function visiblePages(manifest: BundleManifest, mode: UIMode): BundlePage[] {
    return manifest.pages.filter((page) => isShownInUIMode(page, mode) && (!(page.sections ?? []).length || visibleSections(page, mode).length > 0));
}

/** Sections Basic shows: not marked advancedOnly, and not left empty by hiding all their controls and actions. */
export function visibleSections(page: BundlePage, mode: UIMode): PageSection[] {
    return (page.sections ?? []).filter((section) => {
        if (!isShownInUIMode(section, mode)) {
            return false;
        }
        const items = [...(section.controls ?? []), ...(section.actions ?? [])];
        return items.length === 0 || items.some((item) => isShownInUIMode(item, mode));
    });
}

export function visibleControls(section: PageSection, mode: UIMode): ControlSpec[] {
    return (section.controls ?? []).filter((control) => isShownInUIMode(control, mode));
}

export function visibleOptions(options: ControlOption[] | undefined, mode: UIMode): ControlOption[] {
    return (options ?? []).filter((option) => isShownInUIMode(option, mode));
}

export function visibleRows(rows: RowSpec[] | undefined, mode: UIMode): RowSpec[] {
    return (rows ?? []).filter((row) => isShownInUIMode(row, mode));
}

/**
 * What Basic mode hides that would otherwise change a command: controls hidden everywhere they appear, and hidden
 * options of checkbox groups that stay visible. Basic runs commands with those controls (other than paths) at their
 * defaults and those options unchecked, so a setting made in Advanced can't change a Basic run unseen. The saved
 * values are kept.
 */
export interface HiddenInputs {
    controls: Map<string, ControlSpec>;
    options: Map<string, Set<string>>;
}

export function basicModeHiddenInputs(manifest: BundleManifest | null | undefined): HiddenInputs {
    const shown = new Set<string>();
    const hidden = new Map<string, ControlSpec>();
    const options = new Map<string, Set<string>>();
    for (const page of manifest?.pages ?? []) {
        for (const section of page.sections ?? []) {
            for (const control of section.controls ?? []) {
                const visible = isShownInUIMode(page, "basic") && isShownInUIMode(section, "basic") && isShownInUIMode(control, "basic");
                if (!visible) {
                    hidden.set(control.id, control);
                    continue;
                }
                shown.add(control.id);
                const hiddenOptions = (control.options ?? []).filter((option) => !isShownInUIMode(option, "basic")).map((option) => option.id);
                if (hiddenOptions.length) {
                    options.set(control.id, new Set([...(options.get(control.id) ?? []), ...hiddenOptions]));
                }
            }
        }
    }
    for (const id of shown) {
        hidden.delete(id);
    }
    return { controls: hidden, options };
}

/** The value a control has before anyone changes it, as a command sees it. */
export function controlDefaultValue(control: ControlSpec): string {
    if (control.kind === "checkboxGroup") {
        return (control.options ?? []).filter((option) => option.selected).map((option) => option.id).sort().join(",");
    }
    if (control.value != null) {
        return String(control.value);
    }
    return control.options?.find((option) => option.selected)?.id ?? "";
}
