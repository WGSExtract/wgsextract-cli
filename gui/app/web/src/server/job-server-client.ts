import type { LooseRecord } from "../../../shared/types.js";
import type { Job as TransportJob } from "../../../shared/generated/server-transport.js";
import { errorMessage } from "./errors.js";
import { localJobServerURL } from "./local-job-server-url.js";

/** Where a running job server listens, as it announced on stdout. */
export interface JobServerAddress {
    url: string;
    token: string;
}

/** A job record; servers may add fields. `exit_code` is set once the job has finished. */
export interface JobRecord extends LooseRecord, Pick<TransportJob, "id"> {
    // This tolerant reader accepts future statuses and unvalidated errors.
    status: string;
    exit_code?: TransportJob["exit_code"] | null;
    error?: unknown;
}

export interface JobEvent {
    type: string;
    data: LooseRecord;
}

export type StartJobResult =
    | { kind: "accepted"; job: JobRecord }
    | { kind: "rejected" | "unavailable" | "indeterminate"; reason: string };

const submissionTimeoutMs = 15_000;

/** Starts a job once. A lost reply can be recovered by reading the original ID, not by submitting again. */
export async function startJob(address: JobServerAddress, id: string, operation: string, parameters: LooseRecord, card = false): Promise<StartJobResult> {
    if (!localJobServerURL(address.url)) {
        return { kind: "rejected", reason: "Local job server requires a numeric loopback URL." };
    }
    let reason: string;
    try {
        const response = await request(address, "POST", `/ops/${operation}${card ? "?card=1" : ""}`,
            parameters, AbortSignal.timeout(submissionTimeoutMs), { "X-WGSE-Job-ID": id });
        const body = await response.json().catch(() => undefined) as LooseRecord | undefined;
        if (response.ok && isJobRecord(body) && body.id === id) {
            return { kind: "accepted", job: body };
        }
        reason = `HTTP ${response.status}: ${describeError(body)}`;
        const code = responseErrorCode(body);
        if (code && rejectionStatus.get(code) === response.status) {
            return { kind: "rejected", reason };
        }
        if (response.status === 409 && code === "job_id_in_use") {
            return { kind: "indeterminate", reason };
        }
    }
    catch (error) {
        reason = errorMessage(error);
        if (error instanceof Error && error.cause instanceof Error
            && "code" in error.cause && error.cause.code === "ECONNREFUSED") {
            return { kind: "unavailable", reason };
        }
    }
    const job = await getJob(address, id, AbortSignal.timeout(submissionTimeoutMs)).catch(() => undefined);
    return job ? { kind: "accepted", job } : { kind: "indeterminate", reason };
}

// Only the job server's explicit pre-acceptance errors allow direct fallback.
const rejectionStatus = new Map([
    ["bad_length", 400], ["bad_json", 400], ["bad_headers", 400], ["usage", 400], ["invalid_job_id", 400],
    ["unauthorized", 401], ["bad_host", 403], ["bad_origin", 403], ["path_not_allowed", 403],
    ["destructive", 403], ["unknown_op", 404], ["not_found", 404], ["too_large", 413],
    ["stopping", 503],
]);

function responseErrorCode(body: LooseRecord | undefined): string | undefined {
    const error = body?.error;
    if (error && typeof error === "object" && "code" in error && typeof error.code === "string") {
        return error.code;
    }
    return undefined;
}

/** Reads the job's server-sent events until the server ends the stream, which it does once the job has finished. */
export async function followJob(address: JobServerAddress, id: string, onEvent: (event: JobEvent) => void, signal?: AbortSignal): Promise<void> {
    const response = await request(address, "GET", `/jobs/${encodeURIComponent(id)}/events`, undefined, signal);
    if (!response.ok || !response.body) {
        throw new Error(`Job events for ${id}: HTTP ${response.status}`);
    }
    let buffer = "";
    for await (const chunk of response.body.pipeThrough(new TextDecoderStream())) {
        buffer = (buffer + chunk).replaceAll("\r\n", "\n");
        let end: number;
        while ((end = buffer.indexOf("\n\n")) >= 0) {
            const event = parseEvent(buffer.slice(0, end));
            buffer = buffer.slice(end + 2);
            if (event) {
                onEvent(event);
            }
        }
    }
}

export async function cancelJob(address: JobServerAddress, id: string): Promise<void> {
    const response = await request(address, "DELETE", `/jobs/${encodeURIComponent(id)}`);
    // 409: it had already finished.
    if (!response.ok && response.status !== 409) {
        throw new Error(`Cancelling job ${id}: HTTP ${response.status}`);
    }
}

export async function getJob(address: JobServerAddress, id: string, signal?: AbortSignal): Promise<JobRecord> {
    const response = await request(address, "GET", `/jobs/${encodeURIComponent(id)}`, undefined, signal);
    const body = await response.json().catch(() => undefined) as LooseRecord | undefined;
    if (!response.ok || !isJobRecord(body) || body.id !== id) {
        throw new Error(`Job ${id}: HTTP ${response.status}: ${describeError(body)}`);
    }
    return body;
}

/** The job's whole log, or "" when the server has none. */
export async function jobLog(address: JobServerAddress, id: string): Promise<string> {
    const response = await request(address, "GET", `/jobs/${encodeURIComponent(id)}/log?tail=0`);
    return response.ok ? response.text() : "";
}

function request(address: JobServerAddress, method: string, path: string, body?: LooseRecord, signal?: AbortSignal, headers?: Record<string, string>): Promise<Response> {
    const local = localJobServerURL(address.url);
    if (!local) return Promise.reject(new Error("Local job server requires a numeric loopback URL."));
    return fetch(`${local}${path}`, {
        method,
        redirect: "error",
        headers: {
            authorization: `Bearer ${address.token}`,
            ...(body ? { "content-type": "application/json" } : {}),
            ...headers,
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
        ...(signal ? { signal } : {}),
    });
}

function parseEvent(block: string): JobEvent | undefined {
    let type = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
        if (line.startsWith("event:")) {
            type = line.slice(6).trim();
        }
        else if (line.startsWith("data:")) {
            data.push(line.slice(5).trimStart());
        }
    }
    if (data.length === 0) {
        return undefined;
    }
    try {
        const parsed: unknown = JSON.parse(data.join("\n"));
        return parsed && typeof parsed === "object" ? { type, data: parsed as LooseRecord } : undefined;
    }
    catch {
        return undefined;
    }
}

function isJobRecord(value: LooseRecord | undefined): value is JobRecord {
    return typeof value?.id === "string" && typeof value.status === "string" && value.status.trim() !== "";
}

function describeError(body: LooseRecord | undefined): string {
    const error = body?.error;
    if (error && typeof error === "object" && "message" in error) {
        return String(error.message);
    }
    return "no job in the response";
}
