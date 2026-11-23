import type { LibraryViewerSession } from "../../../shared/genome-viewer.js";
import { api } from "./api.js";

export type LibraryViewerResult = LibraryViewerSession | { problem: string; fileName?: string };

const standaloneSessions = new Set<string>();
let lifecycleBound = false;
let pageHidden = false;

export async function releaseLibraryViewer(keepalive = false): Promise<void> {
    if (window.parent !== window) {
        const requestID = crypto.randomUUID();
        return new Promise((resolve) => {
            const timer = window.setTimeout(() => { window.removeEventListener("message", receive); resolve(); }, 5000);
            const receive = (event: MessageEvent) => {
                if (event.origin !== location.origin || event.source !== window.parent
                    || event.data?.type !== "genomeViewerReleased" || event.data.requestID !== requestID) return;
                window.clearTimeout(timer);
                window.removeEventListener("message", receive);
                resolve();
            };
            window.addEventListener("message", receive);
            window.parent.postMessage({ type: "genomeViewerRelease", requestID, keepalive }, location.origin);
        });
    }
    const sessions = [...standaloneSessions];
    standaloneSessions.clear();
    await Promise.all(sessions.map((id) => api("/api/viewer/close", { body: { id }, keepalive }).catch(() => {})));
}

export function bindStandaloneViewerLifecycle() {
    if (window.parent !== window) return;
    if (!lifecycleBound) {
        window.addEventListener("pagehide", () => {
            pageHidden = true;
            void releaseLibraryViewer(true);
        });
        window.addEventListener("pageshow", () => { pageHidden = false; });
        lifecycleBound = true;
    }
}

export async function rememberStandaloneViewer(id: string) {
    bindStandaloneViewerLifecycle();
    standaloneSessions.add(id);
    if (pageHidden) await releaseLibraryViewer(true);
    return !pageHidden;
}

/** The iframe gets only a file-scoped grant, never the main app's API credential. */
export async function requestLibraryViewer(path: string, locus: string, tracks: string[] = [], build = ""): Promise<LibraryViewerResult> {
    const required = build ? { build } : {};
    if (window.parent === window) {
        bindStandaloneViewerLifecycle();
        await releaseLibraryViewer();
        const result = await api<LibraryViewerResult>("/api/viewer/open", { body: { path, locus, tracks, ...required } });
        if ("id" in result) {
            await rememberStandaloneViewer(result.id);
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
        window.parent.postMessage({ type: "genomeViewerOpenLibrary", requestID, path, locus, tracks, ...required }, location.origin);
    });
}
