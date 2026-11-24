import { randomBytes, timingSafeEqual } from "node:crypto";
import { constants } from "node:fs";
import { open, realpath, stat } from "node:fs/promises";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import { pipeline } from "node:stream/promises";
import { HttpError, isErrnoCode } from "./errors.js";
import type { ViewerRsidIndex } from "./viewer-rsids.js";

const maximumReadBytes = 32 * 1024 * 1024;
const idleLifetimeMs = 60 * 60 * 1000;
const maximumSessions = 8;
const outsideLibraries = "Viewer path is outside the configured libraries.";
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

function rethrowUnlessMissing(error: unknown): undefined {
    if (!isErrnoCode(error, "ENOENT", "ENOTDIR")) {
        throw error;
    }
    return undefined;
}

class MissingViewerPath extends HttpError {}

export function isMissingViewerPath(error: unknown): boolean {
    return error instanceof MissingViewerPath || isErrnoCode(error, "ENOENT", "ENOTDIR");
}

/** Any explicit local file chosen by the user: absolute, regular file, no traversal. */
export async function localViewerPath(file: string): Promise<string> {
    if (!path.isAbsolute(file) || file.includes("\0") || file.split(/[\\/]/).includes("..")) {
        throw new HttpError(403, outsideLibraries);
    }
    const canonical = await realpath(path.resolve(file)).catch(rethrowUnlessMissing);
    if (canonical === undefined) {
        throw new MissingViewerPath(404, "Viewer file not found.");
    }
    const info = await stat(canonical).catch(rethrowUnlessMissing);
    if (!info) {
        throw new MissingViewerPath(404, "Viewer file not found.");
    }
    if (!info.isFile()) {
        throw new HttpError(403, "Viewer paths must be regular files.");
    }
    return canonical;
}

/** Both the spelling and canonical target must be inside the same configured library. */
export async function guardedViewerPath(file: string, roots: string[]): Promise<string> {
    if (!path.isAbsolute(file) || file.includes("\0") || file.split(/[\\/]/).includes("..")) {
        throw new HttpError(403, outsideLibraries);
    }
    const absolute = path.resolve(file);
    let canonical: string | undefined;
    let hasUnavailableRoot = false;
    for (const root of roots) {
        if (!inside(absolute, root)) {
            continue;
        }
        const canonicalRoot = await realpath(root).catch(rethrowUnlessMissing);
        if (canonicalRoot === undefined || !(await stat(canonicalRoot).catch(rethrowUnlessMissing))?.isDirectory()) {
            hasUnavailableRoot = true;
            continue;
        }
        canonical ??= await realpath(absolute).catch(rethrowUnlessMissing);
        if (canonical === undefined) {
            throw new MissingViewerPath(404, "Viewer file not found.");
        }
        if (inside(canonical, canonicalRoot)) {
            const info = await stat(canonical).catch(rethrowUnlessMissing);
            if (!info) {
                throw new MissingViewerPath(404, "Viewer file not found.");
            }
            if (!info.isFile()) {
                throw new HttpError(403, "Viewer paths must be regular files.");
            }
            return canonical;
        }
    }
    if (canonical === undefined && hasUnavailableRoot) {
        throw new MissingViewerPath(403, outsideLibraries);
    }
    throw new HttpError(403, outsideLibraries);
}

interface Grant {
    token: string;
    rootKey: string;
    touched: number;
    files: Map<string, { path: string; device: number; inode: number; size: number; modified: number; guarded: boolean }>;
    rsidIndex?: ViewerRsidIndex;
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

    async grant(files: string[], roots: string[], rootKey: string, unguardedFiles: string[] = [], rsidIndex?: ViewerRsidIndex) {
        this.expire();
        const id = randomBytes(24).toString("base64url");
        const token = randomBytes(32).toString("base64url");
        const entries: Grant["files"] = new Map();
        const urls = new Map<string, string>();
        const grantList: Array<{ file: string; guarded: boolean }> = [
            ...[...new Set(files)].map((file) => ({ file, guarded: true })),
            ...[...new Set(unguardedFiles)].filter((file) => !files.includes(file)).map((file) => ({ file, guarded: false })),
        ];
        for (const { file, guarded } of grantList) {
            const canonical = guarded ? await guardedViewerPath(file, roots) : await localViewerPath(file);
            const info = await stat(canonical);
            const fileID = randomBytes(24).toString("base64url");
            entries.set(fileID, { path: file, device: info.dev, inode: info.ino, size: info.size, modified: info.mtimeMs, guarded });
            urls.set(file, `/viewer-data/${id}/${fileID}`);
        }
        for (const stale of [...this.sessions.keys()].slice(0, Math.max(0, this.sessions.size - maximumSessions + 1))) {
            this.sessions.delete(stale);
        }
        this.sessions.set(id, { token, rootKey, touched: Date.now(), files: entries, ...(rsidIndex ? { rsidIndex } : {}) });
        return { id, headers: { [viewerTokenHeader]: token }, urls };
    }

    rsidIndexFor(id: string, rootKey: string): ViewerRsidIndex | undefined {
        this.expire();
        const grant = this.sessions.get(id);
        if (!grant || grant.rootKey !== rootKey) {
            return undefined;
        }
        grant.touched = Date.now();
        return grant.rsidIndex;
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
        const canonical = file.guarded ? await guardedViewerPath(file.path, roots) : await localViewerPath(file.path);
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
            const range = byteRange(request.headers.range, info.size, request.method === "HEAD");
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
            const stream = handle.createReadStream({ start, end, autoClose: false, highWaterMark: 64 * 1024 });
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

/** The whole-file fallback limit bounds response bodies, so a bodyless HEAD can report any size. */
export function byteRange(header: string | undefined, size: number, bodyless = false) {
    if (!header) {
        return bodyless || size <= maximumReadBytes ? { start: 0, end: size - 1, partial: false } : undefined;
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
    // BAI bins at high-depth loci can exceed the fallback limit. Explicit
    // bounds are streamed with backpressure, never buffered as a whole.
    const explicitBounds = first !== undefined && last !== undefined;
    return start >= 0 && start <= end && start < size && (explicitBounds || end - start + 1 <= maximumReadBytes)
        ? { start, end, partial: true } : undefined;
}
