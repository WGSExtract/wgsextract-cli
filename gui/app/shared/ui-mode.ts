/** How much of the GUI is shown: Basic for new users (the default after install), or Advanced for every feature. */
export type UIMode = "basic" | "advanced";

export const uiModes: readonly UIMode[] = ["basic", "advanced"];

/** Anything other than "advanced" is Basic, so a new install, a missing value or a bad one starts in Basic. */
export function normalizeUIMode(value: unknown): UIMode {
    return value === "advanced" ? "advanced" : "basic";
}
