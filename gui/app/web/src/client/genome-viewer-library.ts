import type { LibraryViewerSession } from "../../../shared/genome-viewer.js";
import { api } from "./api.js";

export type LibraryViewerResult = LibraryViewerSession | { problem: string; fileName?: string };

/** The iframe gets only a file-scoped grant, never the main app's API credential. */
export async function requestLibraryViewer(path: string, locus: string): Promise<LibraryViewerResult> {
    if (window.parent === window) {
        return api<LibraryViewerResult>("/api/viewer/open", { body: { path, locus } });
    }
    return new Promise((resolve, reject) => {
        const requestID = crypto.randomUUID();
        const timer = window.setTimeout(() => {
            window.removeEventListener("message", receive);
            reject(new Error("Local library request timed out."));
        }, 45_000);
        const receive = (event: MessageEvent) => {
            if (event.origin !== location.origin || event.source !== window.parent || event.data?.type !== "genomeViewerLibrary" || event.data.requestID !== requestID) {
                return;
            }
            window.clearTimeout(timer);
            window.removeEventListener("message", receive);
            resolve(event.data.result);
        };
        window.addEventListener("message", receive);
        window.parent.postMessage({ type: "genomeViewerOpenLibrary", requestID, path, locus }, location.origin);
    });
}
