import { randomBytes, timingSafeEqual } from "node:crypto";
import { constants } from "node:fs";
import { open, realpath, stat } from "node:fs/promises";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import { pipeline } from "node:stream/promises";
import { HttpError } from "./errors.js";

const maximumReadBytes = 32 * 1024 * 1024;
const idleLifetimeMs = 60 * 60 * 1000;
const maximumSessions = 8;
export const viewerTokenHeader = "x-gfc-viewer-token";

export function requireViewerLoopback(request: IncomingMessage) {
    if (!["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(request.socket.remoteAddress ?? "")
        || !["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(request.socket.localAddress ?? "")) {
        throw new HttpError(403, "Viewer files are available on loopback only.");
    }
}

function inside(file: string, root: string) {
    const relative = path.relative(root, file);
    return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

/** Both the spelling and canonical target must be inside the same configured library. */
export async function guardedViewerPath(file: string, roots: string[]): Promise<string> {
    if (!path.isAbsolute(file) || file.includes("\0") || file.split(/[\\/]/).includes("..")) {
        throw new HttpError(403, "Viewer path is outside the configured libraries.");
    }
    const absolute = path.resolve(file);
    const canonical = await realpath(absolute);
    for (const root of roots) {
        if (inside(absolute, root) && inside(canonical, await realpath(root))) {
            if (!(await stat(canonical)).isFile()) {
                throw new HttpError(403, "Viewer paths must be regular files.");
            }
            return canonical;
        }
    }
    throw new HttpError(403, "Viewer path is outside the configured libraries.");
}

interface Grant {
    token: string;
    rootKey: string;
    touched: number;
    files: Map<string, { path: string; device: number; inode: number; size: number; modified: number }>;
}

export interface ViewerByteRead {
    fileID: string;
    start: number;
    bytes: number;
}

/** Opaque file IDs and a revocable, viewer-only header token; never paths or credentials in URLs. */
export class ViewerFiles {
    private readonly sessions = new Map<string, Grant>();

    constructor(private readonly onRead?: (read: ViewerByteRead) => void) {}

    async grant(files: string[], roots: string[], rootKey: string) {
        this.expire();
        const id = randomBytes(24).toString("base64url");
        const token = randomBytes(32).toString("base64url");
        const entries: Grant["files"] = new Map();
        const urls = new Map<string, string>();
        for (const file of new Set(files)) {
            const canonical = await guardedViewerPath(file, roots);
            const info = await stat(canonical);
            const fileID = randomBytes(24).toString("base64url");
            entries.set(fileID, { path: file, device: info.dev, inode: info.ino, size: info.size, modified: info.mtimeMs });
            urls.set(file, `/viewer-data/${id}/${fileID}`);
        }
        for (const stale of [...this.sessions.keys()].slice(0, Math.max(0, this.sessions.size - maximumSessions + 1))) {
            this.sessions.delete(stale);
        }
        this.sessions.set(id, { token, rootKey, touched: Date.now(), files: entries });
        return { id, headers: { [viewerTokenHeader]: token }, urls };
    }

    revoke(id: string) {
        this.sessions.delete(id);
    }

    async serve(request: IncomingMessage, response: ServerResponse, pathname: string, roots: string[], rootKey: string) {
        requireViewerLoopback(request);
        this.expire();
        const match = /^\/viewer-data\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)$/.exec(pathname);
        const grant = match?.[1] ? this.sessions.get(match[1]) : undefined;
        const file = match?.[2] ? grant?.files.get(match[2]) : undefined;
        const supplied = request.headers[viewerTokenHeader];
        if (!grant || !file || typeof supplied !== "string" || !sameToken(supplied, grant.token) || grant.rootKey !== rootKey) {
            throw new HttpError(401, "Missing or expired viewer file grant.");
        }
        if (request.method !== "GET" && request.method !== "HEAD") {
            throw new HttpError(405, "Viewer files are read-only.");
        }
        // Recheck after configuration changes and pin the opened inode before streaming.
        const canonical = await guardedViewerPath(file.path, roots);
        const handle = await open(canonical, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
        try {
            const info = await handle.stat();
            if (!info.isFile() || info.dev !== file.device || info.ino !== file.inode || info.size !== file.size || info.mtimeMs !== file.modified) {
                throw new HttpError(409, "Viewer file changed. Open it again.");
            }
            grant.touched = Date.now();
            response.setHeader("accept-ranges", "bytes");
            response.setHeader("cache-control", "no-store");
            response.setHeader("content-type", "application/octet-stream");
            const range = byteRange(request.headers.range, info.size);
            if (!range) {
                response.setHeader("content-range", `bytes */${info.size}`);
                response.writeHead(416);
                response.end();
                return;
            }
            const { start, end, partial } = range;
            const length = Math.max(0, end - start + 1);
            response.setHeader("content-length", length);
            if (partial) {
                response.setHeader("content-range", `bytes ${start}-${end}/${info.size}`);
            }
            response.writeHead(partial ? 206 : 200);
            if (request.method === "HEAD" || length === 0) {
                response.end();
                return;
            }
            const stream = handle.createReadStream({ start, end, autoClose: false });
            let bytes = 0;
            stream.on("data", (chunk: Buffer) => { bytes += chunk.length; });
            try {
                await pipeline(stream, response);
            } finally {
                this.onRead?.({ fileID: match?.[2] ?? "", start, bytes });
            }
        } finally {
            await handle.close();
        }
    }

    private expire() {
        for (const [id, grant] of this.sessions) {
            if (Date.now() - grant.touched > idleLifetimeMs) {
                this.sessions.delete(id);
            }
        }
    }
}

function sameToken(actual: string, expected: string) {
    const a = Buffer.from(actual);
    const b = Buffer.from(expected);
    return a.length === b.length && timingSafeEqual(a, b);
}

function byteRange(header: string | undefined, size: number) {
    if (!header) {
        return size <= maximumReadBytes ? { start: 0, end: size - 1, partial: false } : undefined;
    }
    const match = /^bytes=(\d*)-(\d*)$/.exec(header);
    if (!match || (!match[1] && !match[2]) || size === 0) {
        return undefined;
    }
    const first = match[1] ? Number(match[1]) : undefined;
    const last = match[2] ? Number(match[2]) : undefined;
    if ([first, last].some((value) => value !== undefined && !Number.isSafeInteger(value))) {
        return undefined;
    }
    const start = first ?? Math.max(0, size - (last ?? 0));
    const end = first === undefined ? size - 1 : Math.min(size - 1, last ?? size - 1);
    return start >= 0 && start <= end && start < size && end - start + 1 <= maximumReadBytes
        ? { start, end, partial: true } : undefined;
}
