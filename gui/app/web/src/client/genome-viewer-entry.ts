import { openGenePage } from "./gene-page.js";
import { openGenomeViewer } from "./genome-viewer-launcher.js";
import { commandContext, elements } from "./model.js";
import { runAction } from "./operations.js";
import { state } from "./state.js";

function viewerActionFor(controlID: string) {
    for (const page of state.manifest?.pages ?? []) {
        for (const section of page.sections ?? []) {
            for (const action of section.actions ?? []) {
                if (action.viewerInput === controlID && action.prerequisiteArguments?.length) {
                    return {
                        action,
                        reference: { pageID: page.id, sectionID: String(section.id ?? ""), actionID: String(action.id ?? "") },
                    };
                }
            }
        }
    }
    return null;
}

export function bindGenomeViewerEvents() {
    elements("[data-gene-page-open]").forEach((button) => {
        button.addEventListener("click", () => openGenePage(button));
    });
    elements("[data-genome-viewer-open]").forEach((button) => {
        button.addEventListener("click", () => openGenomeViewer(state.localizationCode, state.labels, button));
    });
    elements<HTMLButtonElement>("[data-view-reads]").forEach((button) => {
        button.addEventListener("click", () => {
            const controlID = button.dataset.viewReads ?? "";
            const matched = viewerActionFor(controlID);
            if (matched && state.manifest?.prerequisites) {
                void runAction(matched.action, commandContext(), matched.reference, { opener: button });
                return;
            }
            openGenomeViewer(state.localizationCode, state.labels, button, String(state.fieldValues[controlID] ?? ""));
        });
    });
    elements<HTMLButtonElement>("[data-result-viewer]").forEach((button) => {
        button.addEventListener("click", () => openGenomeViewer(state.localizationCode, state.labels, button, button.dataset.resultViewer ?? "", button.dataset.viewerLocus ?? "", { build: button.dataset.viewerBuild ?? "" }));
    });
}
