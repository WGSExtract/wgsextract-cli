import type { Labels } from "../../../shared/types.js";
import type { LibraryViewerResult } from "./genome-viewer-library.js";
import { api } from "./api.js";

let pane: HTMLElement | undefined;

/** A sibling of #app survives command-output renders without unloading either document or its streams. */
export function openGenomeViewer(locale: string, labels: Labels, opener: HTMLElement, inputPath = "", locus = "") {
    if (pane) {
        return;
    }
    const app = document.querySelector<HTMLElement>("#app");
    if (!app) {
        return;
    }
    const previousDisplay = app.style.display;
    const focusSelector = openerSelector(opener);
    const container = document.createElement("section");
    container.className = "genome-viewer-shell";
    const back = document.createElement("button");
    back.type = "button";
    back.className = "genome-viewer-close";
    back.textContent = labels.viewerStrings?.back ?? "";
    const frame = document.createElement("iframe");
    frame.title = labels.viewerTitle ?? "";
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
    frame.src = "/viewer";
    const sessions = new Set<string>();
    let closed = false;
    const onMessage = (event: MessageEvent) => {
        if (event.origin === location.origin && event.source === frame.contentWindow && event.data?.type === "genomeViewerReady") {
            frame.contentWindow?.postMessage({
                type: "genomeViewerLabels",
                locale,
                direction: labels.layoutDirection ?? "ltr",
                strings: labels.viewerStrings ?? {},
                inputPath,
                locus,
            }, location.origin);
        }
        if (event.origin === location.origin && event.source === frame.contentWindow && event.data?.type === "genomeViewerOpenLibrary"
            && typeof event.data.requestID === "string" && typeof event.data.path === "string" && typeof event.data.locus === "string") {
            void api<LibraryViewerResult>("/api/viewer/open", { body: { path: event.data.path, locus: event.data.locus } })
                .catch((): LibraryViewerResult => ({ problem: "libraryUnavailable" }))
                .then((result) => {
                    if ("id" in result) {
                        if (closed) {
                            void api("/api/viewer/close", { body: { id: result.id } }).catch(() => {});
                            return;
                        }
                        for (const id of sessions) {
                            void api("/api/viewer/close", { body: { id } }).catch(() => {});
                        }
                        sessions.clear();
                        sessions.add(result.id);
                    }
                    frame.contentWindow?.postMessage({ type: "genomeViewerLibrary", requestID: event.data.requestID, result }, location.origin);
                });
        }
    };
    window.addEventListener("message", onMessage);
    back.addEventListener("click", () => {
        closed = true;
        for (const id of sessions) {
            void api("/api/viewer/close", { body: { id } }).catch(() => {});
        }
        window.removeEventListener("message", onMessage);
        container.remove();
        pane = undefined;
        app.style.display = previousDisplay;
        app.inert = false;
        app.removeAttribute("aria-hidden");
        (app.querySelector<HTMLElement>(focusSelector) ?? app.querySelector<HTMLElement>("[data-genome-viewer-open]"))?.focus();
    });
    container.append(back, frame);
    document.body.append(container);
    pane = container;
    app.style.display = "none";
    app.inert = true;
    app.setAttribute("aria-hidden", "true");
    back.focus();
}

/** Background job renders may replace the clicked control while the viewer stays mounted. */
function openerSelector(opener: HTMLElement): string {
    if (opener.dataset.resultViewer != null) {
        return `[data-result-viewer="${CSS.escape(opener.dataset.resultViewer)}"][data-viewer-locus="${CSS.escape(opener.dataset.viewerLocus ?? "")}"]`;
    }
    if (opener.dataset.viewReads != null) {
        return `[data-view-reads="${CSS.escape(opener.dataset.viewReads)}"]`;
    }
    return "[data-genome-viewer-open]";
}
