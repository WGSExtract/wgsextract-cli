import type { ManifestResponse, StringMap } from "../../../shared/types.js";
import { api } from "./api.js";

interface ViewerLabels {
    locale: string;
    direction: string;
    strings: StringMap;
}

/** Embedded viewers receive labels, never a token: launcher token injection is not portable across subframes. */
export async function genomeViewerLabels(): Promise<ViewerLabels> {
    if (window.parent === window) {
        const locale = new URLSearchParams(location.search).get("locale");
        const bundle = await api<ManifestResponse>(`/api/manifest${locale ? `?locale=${encodeURIComponent(locale)}` : ""}`);
        return {
            locale: bundle.localizationCode || "en",
            direction: bundle.labels.layoutDirection || "ltr",
            strings: bundle.labels.viewerStrings ?? {},
        };
    }
    return new Promise((resolve, reject) => {
        const timeout = window.setTimeout(() => {
            window.removeEventListener("message", onMessage);
            reject(new Error("Viewer labels were not received."));
        }, 10_000);
        const onMessage = (event: MessageEvent) => {
            if (event.origin !== location.origin || event.source !== window.parent || event.data?.type !== "genomeViewerLabels") {
                return;
            }
            const data = event.data;
            if (typeof data.locale !== "string" || typeof data.direction !== "string" || !data.strings || typeof data.strings !== "object") {
                return;
            }
            window.clearTimeout(timeout);
            window.removeEventListener("message", onMessage);
            resolve({
                locale: data.locale,
                direction: data.direction,
                strings: Object.fromEntries(Object.entries(data.strings).filter((entry): entry is [string, string] => typeof entry[1] === "string")),
            });
        };
        window.addEventListener("message", onMessage);
        window.parent.postMessage({ type: "genomeViewerReady" }, location.origin);
    });
}
