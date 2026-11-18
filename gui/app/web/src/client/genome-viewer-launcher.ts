import type { Labels } from "../../../shared/types.js";

let pane: HTMLElement | undefined;

/** A sibling of #app survives command-output renders without unloading either document or its streams. */
export function openGenomeViewer(locale: string, labels: Labels) {
    if (pane) {
        return;
    }
    const app = document.querySelector<HTMLElement>("#app");
    if (!app) {
        return;
    }
    const previousDisplay = app.style.display;
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
    const onMessage = (event: MessageEvent) => {
        if (event.origin === location.origin && event.source === frame.contentWindow && event.data?.type === "genomeViewerReady") {
            frame.contentWindow?.postMessage({
                type: "genomeViewerLabels",
                locale,
                direction: labels.layoutDirection ?? "ltr",
                strings: labels.viewerStrings ?? {},
            }, location.origin);
        }
    };
    window.addEventListener("message", onMessage);
    back.addEventListener("click", () => {
        window.removeEventListener("message", onMessage);
        container.remove();
        pane = undefined;
        app.style.display = previousDisplay;
        app.inert = false;
        app.removeAttribute("aria-hidden");
        app.querySelector<HTMLElement>("[data-genome-viewer-open]")?.focus();
    });
    container.append(back, frame);
    document.body.append(container);
    pane = container;
    app.style.display = "none";
    app.inert = true;
    app.setAttribute("aria-hidden", "true");
    back.focus();
}
