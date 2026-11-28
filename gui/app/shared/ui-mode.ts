import type { ActionSpec, BundleManifest, BundlePage, ControlOption, ControlSpec, PageSection, RowSpec } from "./types.js";

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

/** An action titled for a mode: Advanced uses its `advancedTitle` when it has one. */
export function actionTitledForUIMode(action: ActionSpec, mode: UIMode): ActionSpec {
    return mode === "advanced" && action.advancedTitle ? { ...action, title: action.advancedTitle } : action;
}

/** The actions a mode shows, each titled for that mode. */
export function visibleActions(actions: ActionSpec[] | undefined, mode: UIMode): ActionSpec[] {
    return (actions ?? []).filter((action) => isShownInUIMode(action, mode)).map((action) => actionTitledForUIMode(action, mode));
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

/** The leading words of a command an action runs; `open` when a placeholder there can name any subcommand. */
export interface CommandPrefix {
    words: string[];
    open: boolean;
}

/** An action's command words: its arguments from the first word up to the first option or placeholder. */
export function commandPrefix(args: readonly string[] = []): CommandPrefix {
    const words: string[] = [];
    for (const arg of args) {
        if (arg.includes("{{")) {
            return { words, open: true };
        }
        if (arg.startsWith("-")) {
            if (words.length) {
                break;
            }
            continue;
        }
        words.push(arg);
    }
    return { words, open: false };
}

/** The commands Basic mode's actions run, leaving out those of `except` (a list's own row actions). */
export function basicCommandPrefixes(manifest: BundleManifest, except?: ControlSpec): CommandPrefix[] {
    const actions: ActionSpec[] = [];
    for (const page of visiblePages(manifest, "basic")) {
        for (const section of visibleSections(page, "basic")) {
            actions.push(...visibleActions(section.actions, "basic"));
            for (const control of visibleControls(section, "basic")) {
                actions.push(...visibleActions(control.actions, "basic"));
                if (control !== except && control.id !== except?.id) {
                    actions.push(...visibleActions(control.rowActions, "basic"));
                }
            }
        }
    }
    return actions.map((action) => commandPrefix(action.command?.arguments)).filter((prefix) => prefix.words.length || prefix.open);
}

/** Whether command `path` ("vcf variantqc") is one some prefix runs. */
export function commandRunBy(path: string, prefixes: readonly CommandPrefix[]): boolean {
    const words = path.split(/\s+/).filter(Boolean);
    const startsWith = (whole: string[], start: string[]) => start.every((word, index) => whole[index] === word);
    return prefixes.some((prefix) => startsWith(prefix.words, words) || (prefix.open && startsWith(words, prefix.words)));
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
