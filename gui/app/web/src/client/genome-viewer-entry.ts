import { interpolate } from "../../../shared/rendering.js";
import type { ValueMap } from "../../../shared/types.js";
import { openGenomeViewer } from "./genome-viewer-launcher.js";
import { commandContext, elements } from "./model.js";
import { state } from "./state.js";

export function bindGenomeViewerEvents() {
    elements("[data-genome-viewer-open]").forEach((button) => {
        button.addEventListener("click", () => openGenomeViewer(state.localizationCode, state.labels, button));
    });
    elements<HTMLButtonElement>("[data-view-reads]").forEach((button) => {
        button.addEventListener("click", () => openGenomeViewer(state.localizationCode, state.labels, button, String(state.fieldValues[button.dataset.viewReads ?? ""] ?? "")));
    });
    elements<HTMLButtonElement>("[data-viewer-action]").forEach((button) => {
        button.addEventListener("click", () => {
            const rowValues = parseViewerRow(button.dataset.viewerRow);
            const context = commandContext(rowValues);
            const input = interpolate(button.dataset.viewerInput ?? "", context).trim();
            const locus = button.dataset.viewerLocus ? interpolate(button.dataset.viewerLocus, context).trim() : "";
            openGenomeViewer(state.localizationCode, state.labels, button, input, locus);
        });
    });
    elements<HTMLButtonElement>("[data-result-viewer]").forEach((button) => {
        button.addEventListener("click", () => openGenomeViewer(state.localizationCode, state.labels, button, button.dataset.resultViewer ?? "", button.dataset.viewerLocus ?? ""));
    });
}

function parseViewerRow(raw: string | undefined): ValueMap {
    if (!raw) {
        return {};
    }
    try {
        const parsed: unknown = JSON.parse(raw);
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as ValueMap : {};
    } catch {
        return {};
    }
}
