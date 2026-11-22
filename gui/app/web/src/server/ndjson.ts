import type { ServerResponse } from "node:http";
import type { LooseRecord } from "../../../shared/types.js";
import { asError, errorMessage } from "./errors.js";

export type EmitEvent = (event: LooseRecord) => Promise<void>;

/**
 * Streams events as NDJSON. The run never starts for a client that has already gone, is aborted when the
 * client disconnects or a write fails, and a failure is reported as a final `failureEvent` while the
 * response is still writable.
 */
export async function streamNDJSON(
    response: ServerResponse,
    run: (emit: EmitEvent, signal: AbortSignal) => Promise<unknown>,
    failureEvent: (message: string, error: unknown) => LooseRecord,
) {
    // The client may have gone while the route was still reading the request.
    if (response.destroyed) {
        return;
    }
    const abortController = new AbortController();
    const { signal } = abortController;
    response.once("close", () => {
        if (!response.writableEnded) {
            abortController.abort();
        }
    });
    response.writeHead(200, { "content-type": "application/x-ndjson; charset=utf-8", "cache-control": "no-store" });
    let writeQueue = Promise.resolve();
    let writeError: unknown;
    // Never rejects: the first write failure is recorded and cancels the run.
    const emit: EmitEvent = (event) => {
        writeQueue = writeQueue
            .then(() => (writeError == null ? writeNDJSON(response, event, signal) : undefined))
            .catch((error: unknown) => {
                writeError ??= error;
                abortController.abort();
            });
        return writeQueue;
    };
    try {
        await run(emit, signal);
        await writeQueue;
        if (writeError != null) {
            throw writeError;
        }
        response.end();
    }
    catch (error) {
        if (response.destroyed || response.writableEnded) {
            return;
        }
        try {
            await writeQueue;
            if (writeError != null) {
                throw writeError;
            }
            await writeNDJSON(response, failureEvent(errorMessage(error), error), signal);
            response.end();
        }
        catch (_writeError) {
            response.destroy(asError(error));
        }
    }
}

/** Settles once the event is flushed or can no longer be: on drain, error, close or abort. */
async function writeNDJSON(response: ServerResponse, event: LooseRecord, signal: AbortSignal) {
    if (response.destroyed || signal.aborted) throw new Error("Response closed.");
    if (response.write(`${JSON.stringify(event)}\n`)) {
        return;
    }
    await new Promise<void>((resolve, reject) => {
        const cleanup = () => {
            response.off("drain", onDrain);
            response.off("error", onError);
            response.off("close", onClose);
            signal.removeEventListener("abort", onClose);
        };
        const onDrain = () => {
            cleanup();
            resolve();
        };
        const onError = (error: Error) => {
            cleanup();
            reject(error);
        };
        const onClose = () => onError(new Error("Response closed."));
        response.once("drain", onDrain);
        response.once("error", onError);
        response.once("close", onClose);
        signal.addEventListener("abort", onClose, { once: true });
        // write() may have destroyed the response before these listeners were attached.
        if (response.destroyed || signal.aborted) onClose();
    });
}
