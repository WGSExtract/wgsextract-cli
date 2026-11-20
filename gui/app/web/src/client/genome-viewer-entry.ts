import { openGenomeViewer } from "./genome-viewer-launcher.js";
import { elements } from "./model.js";
import { state } from "./state.js";

export function bindGenomeViewerEvents() {
    elements("[data-genome-viewer-open]").forEach((button) => {
        button.addEventListener("click", () => openGenomeViewer(state.localizationCode, state.labels, button));
    });
    elements<HTMLButtonElement>("[data-view-reads]").forEach((button) => {
        button.addEventListener("click", () => openGenomeViewer(state.localizationCode, state.labels, button, String(state.fieldValues[button.dataset.viewReads ?? ""] ?? "")));
    });
    elements<HTMLButtonElement>("[data-result-viewer]").forEach((button) => {
        button.addEventListener("click", () => openGenomeViewer(state.localizationCode, state.labels, button, button.dataset.resultViewer ?? "", button.dataset.viewerLocus ?? ""));
    });
}
