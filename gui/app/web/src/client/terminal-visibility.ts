import { state } from "./state.js";

/** Advanced mode reopens the Jobs pane the way it was last left; Basic always starts collapsed. */
const advancedExpandedKey = "wgsextractGui.advancedJobsPaneExpanded";

/** Sets whether the Jobs pane is expanded for the current UI mode: collapsed in Basic, the saved choice in Advanced. */
export function applyUIModeTerminalVisibility() {
    state.isTerminalVisible = state.uiMode === "advanced" && localStorage.getItem(advancedExpandedKey) !== "false";
}

export function setTerminalVisible(visible: boolean) {
    state.isTerminalVisible = visible;
    if (state.uiMode === "advanced") {
        localStorage.setItem(advancedExpandedKey, visible ? "true" : "false");
    }
}
