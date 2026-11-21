import type { LibraryViewerSession } from "../../../shared/genome-viewer.js";
import { api } from "./api.js";
import { bindStandaloneViewerLifecycle, rememberStandaloneViewer } from "./genome-viewer-library.js";

export type RemoteViewerAction = "status" | "import" | "forget" | "open";
export interface RemoteViewerStatus { id?: string; destination?: string; problem?: string; cancelled?: boolean }
export type RemoteViewerResult = RemoteViewerStatus | LibraryViewerSession;

/** The parent brokers a fixed set of operations; neither frame receives server credentials. */
export async function requestRemoteViewer(action: RemoteViewerAction, body: Record<string, unknown> = {}): Promise<RemoteViewerResult> {
    if (window.parent === window) {
        bindStandaloneViewerLifecycle();
        const requestID = crypto.randomUUID();
        if ((action === "open" || action === "import") && !await rememberStandaloneViewer(requestID)) return { cancelled: true };
        const result = await api<RemoteViewerResult>(`/api/viewer/remote/${action}`, { body: { ...body, requestID } });
        if ("files" in result && result.id) await rememberStandaloneViewer(result.id);
        return result;
    }
    return new Promise((resolve, reject) => {
        const requestID = crypto.randomUUID();
        const timer = window.setTimeout(() => {
            window.removeEventListener("message", receive);
            reject(new Error("remoteUnavailable"));
        }, action === "import" ? 120_000 : 180_000);
        const receive = (event: MessageEvent) => {
            if (event.origin !== location.origin || event.source !== window.parent || event.data?.type !== "genomeViewerRemoteResult" || event.data.requestID !== requestID) return;
            window.clearTimeout(timer);
            window.removeEventListener("message", receive);
            resolve(event.data.result);
        };
        window.addEventListener("message", receive);
        window.parent.postMessage({ type: "genomeViewerRemoteRequest", requestID, action, body }, location.origin);
    });
}
