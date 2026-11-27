import { state } from "./state.js";
import { invalidateDataSources } from "./operations-data.js";
import type { ActionSpec } from "../../../shared/types.js";

/**
 * Library lists (Tools, Reference Data, Reference Genomes) run a slow command, so their results stay cached across
 * page visits for the life of the app. Only what can change a list drops it: an action run from its page, an action
 * of the same command family run from elsewhere (an "Install and Continue" `deps install` changes Tools), or a change
 * to a field its command uses. Other data sources are dropped as before.
 */
interface CachedList {
    pageID: string;
    family: string;
    source: string;
}

function cachedLists(): Map<string, CachedList> {
    const lists = new Map<string, CachedList>();
    for (const page of state.manifest?.pages ?? []) {
        for (const section of page.sections ?? []) {
            for (const control of section.controls ?? []) {
                if (control.kind === "libraryList" && control.dataSource) {
                    const args = control.dataSource.arguments;
                    lists.set(`control:${control.id}`, {
                        pageID: page.id,
                        family: commandFamily(Array.isArray(args) ? args.map(String) : []),
                        source: JSON.stringify(control.dataSource),
                    });
                }
            }
        }
    }
    return lists;
}

/** A command's first word, its group: "deps" for `--json deps list`; "" when it has none. */
export function commandFamily(args: readonly string[] = []): string {
    const word = args.find((arg) => !arg.startsWith("-"));
    return word && !word.includes("{{") ? word : "";
}

/** Options that fetch or install something, which can change any library list (a Microarray run's --download). */
const DOWNLOAD_OPTIONS = new Set(["--download", "--install"]);

/** After `action` ran from page `pageID`: drops every data source but the library lists it can't have changed. */
export function invalidateAfterAction(action: ActionSpec, pageID: string | undefined) {
    const args = action.command?.arguments ?? [];
    const family = commandFamily(args);
    const downloads = [...args, ...(action.command?.optionalArguments ?? []).flat()].some((arg) => DOWNLOAD_OPTIONS.has(arg));
    const lists = cachedLists();
    invalidateDataSources((key) => {
        const list = lists.get(key);
        return !list || downloads || list.pageID === pageID || (family !== "" && list.family === family);
    });
}

/** After fields `ids` changed: drops every data source but the library lists whose commands don't use them. */
export function invalidateAfterFieldChange(ids: readonly string[]) {
    const lists = cachedLists();
    invalidateDataSources((key) => {
        const list = lists.get(key);
        return !list || ids.some((id) => list.source.includes(`{{${id}`));
    });
}
