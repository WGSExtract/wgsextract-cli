export * from "./rendering-controls.js";
export * from "./rendering-context.js";
export * from "./rendering-placeholders.js";
export * from "./rendering-conditions.js";
export * from "./rendering-rows.js";
export * from "./rendering-toml.js";

/** Whether every word of `filter` appears in `text`. */
export function rowMatchesFilter(text: string, filter: string): boolean {
    const haystack = text.toLowerCase();
    return filter.toLowerCase().split(/\s+/).filter(Boolean).every((word) => haystack.includes(word));
}
