import { state } from "./state.js";

/** Advanced mode reopens the Jobs pane the way it was last left; Basic always starts collapsed. */
const advancedExpandedKey = "wgsextractGui.advancedJobsPaneExpanded";

/** The UI mode the pane's expanded state was last set for. */
let appliedUIMode: string | null = null;

/** Sets whether the Jobs pane is expanded for the current UI mode: collapsed in Basic, the saved choice in Advanced. */
export function applyUIModeTerminalVisibility() {
    state.isTerminalVisible = state.uiMode === "advanced" && localStorage.getItem(advancedExpandedKey) !== "false";
    appliedUIMode = state.uiMode;
}

/** Applies the mode's default only when the mode changed, so reloading in the same mode keeps the pane as it is. */
export function applyUIModeTerminalVisibilityIfChanged() {
    if (state.uiMode !== appliedUIMode) {
        applyUIModeTerminalVisibility();
    }
}

export function setTerminalVisible(visible: boolean) {
    state.isTerminalVisible = visible;
    if (state.uiMode === "advanced") {
        localStorage.setItem(advancedExpandedKey, visible ? "true" : "false");
    }
}
