import type { LibraryViewerSession } from "../../../shared/genome-viewer.js";
import { api } from "./api.js";

export type LibraryViewerResult = LibraryViewerSession | { problem: string; fileName?: string };

const standaloneSessions = new Set<string>();
let lifecycleBound = false;
let pageHidden = false;

export async function releaseLibraryViewer(keepalive = false): Promise<void> {
    const sessions = [...standaloneSessions];
    standaloneSessions.clear();
    await Promise.all(sessions.map((id) => api("/api/viewer/close", { body: { id }, keepalive }).catch(() => {})));
}

/** The iframe gets only a file-scoped grant, never the main app's API credential. */
export async function requestLibraryViewer(path: string, locus: string): Promise<LibraryViewerResult> {
    if (window.parent === window) {
        if (!lifecycleBound) {
            window.addEventListener("pagehide", () => {
                pageHidden = true;
                void releaseLibraryViewer(true);
            });
            window.addEventListener("pageshow", () => { pageHidden = false; });
            lifecycleBound = true;
        }
        await releaseLibraryViewer();
        const result = await api<LibraryViewerResult>("/api/viewer/open", { body: { path, locus } });
        if ("id" in result) {
            standaloneSessions.add(result.id);
            if (pageHidden) {
                await releaseLibraryViewer(true);
            }
        }
        return result;
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
