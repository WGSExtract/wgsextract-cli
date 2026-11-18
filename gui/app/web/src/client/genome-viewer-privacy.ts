interface ClickState {
    genomicLocation: number;
}

interface AlignmentPopup {
    popupData(location: number, hiddenTags: unknown, showTags: unknown): unknown;
}

export interface IgvLocalTrack {
    type: string;
    alignmentTrack?: {
        hiddenTags: unknown;
        showTags: unknown;
        getClickedObject(state: ClickState): AlignmentPopup | undefined;
        popupData(state: ClickState): unknown;
    };
    coverageTrack?: {
        getClickedObject(state: ClickState): Record<string, unknown> | undefined;
    };
}

/** igv 3.8.10 performs ClinVar requests before trackclick, with no public offline option. */
export function disablePopupLookups(track: IgvLocalTrack) {
    if (track.type !== "alignment") {
        return;
    }
    const { alignmentTrack, coverageTrack } = track;
    if (!alignmentTrack || !coverageTrack) {
        throw new Error("Unsupported igv alignment popup API.");
    }
    // Omit the optional reference-base argument that triggers read HGVS/ClinVar lookups.
    alignmentTrack.popupData = (state) => alignmentTrack.getClickedObject(state)?.popupData(
        state.genomicLocation, alignmentTrack.hiddenTags, alignmentTrack.showTags,
    );
    const getCoverage = coverageTrack.getClickedObject.bind(coverageTrack);
    // Coverage uses the same lookup, gated by the popup object's reference (not the displayed sequence).
    coverageTrack.getClickedObject = (state) => {
        const coverage = getCoverage(state);
        return coverage ? { ...coverage, reference: undefined } : undefined;
    };
}

/** Never hand file-derived strings to igv's HTML formatter, including decoded VCF INFO values. */
export function localPopupHTML(_track: unknown, fields: unknown): string {
    const container = document.createElement("div");
    if (!Array.isArray(fields)) {
        return container.outerHTML;
    }
    for (const field of fields) {
        if (typeof field === "string" && /^<hr\s*\/?>$/i.test(field)) {
            container.append(document.createElement("hr"));
            continue;
        }
        const row = document.createElement("div");
        row.textContent = field && typeof field === "object"
            ? ("name" in field ? `${String(field.name)}: ${String(field.value ?? "")}` : String(field.html ?? ""))
            : String(field ?? "");
        container.append(row);
    }
    return container.outerHTML;
}
