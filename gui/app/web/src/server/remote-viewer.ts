import { randomBytes, timingSafeEqual } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { localViewerLocus, ViewerFileError, type LibraryViewerSession } from "../../../shared/genome-viewer.js";
import { HttpError } from "./errors.js";
import { RemoteViewerConnection } from "./remote-viewer-connection.js";
import { byteRange, requireViewerLoopback, viewerTokenHeader } from "./viewer-files.js";

interface Grant {
    connectionID: string;
    token: string;
    touched: number;
    files: Map<string, { path: string; size: number }>;
    controller: AbortController;
    owner: string;
}

/** Loopback-only, revocable file capabilities proxy to one operator-selected server. */
export class RemoteViewer {
    private readonly grants = new Map<string, Grant>();
    private readonly pending = new Map<string, AbortController>();
    private readonly revoked = new Set<string>();

    constructor(readonly connection: RemoteViewerConnection) {}

    revoke(id: string) {
        this.connection.cancelImport(id);
        this.revoked.add(id);
        if (this.revoked.size > 128) {
            const oldest = this.revoked.values().next().value;
            if (oldest) this.revoked.delete(oldest);
        }
        this.pending.get(id)?.abort();
        for (const [grantID, grant] of this.grants) {
            if (grant.owner === id) {
                grant.controller.abort();
                this.grants.delete(grantID);
            }
        }
        this.grants.get(id)?.controller.abort();
        this.grants.delete(id);
    }

    revokeAll() {
        this.connection.cancelImports();
        for (const controller of this.pending.values()) controller.abort();
        for (const id of this.grants.keys()) this.revoke(id);
    }

    beginImport(owner: string) {
        if (this.revoked.has(owner)) throw new HttpError(401, "remoteGrantExpired");
        this.revokeAll();
        return this.connection.beginImport(owner);
    }

    async open(owner: string, id: string, input: string, index: string, fasta: string, locus: string, confirmed: boolean, signal?: AbortSignal): Promise<LibraryViewerSession> {
        if (!confirmed) throw new HttpError(403, "remoteConsentRequired");
        if (this.revoked.has(owner)) throw new HttpError(401, "remoteGrantExpired");
        if (!/^[A-Za-z0-9_-]{1,128}$/.test(owner) || this.pending.has(owner)) throw new HttpError(400, "remoteUnavailable");
        const controller = new AbortController();
        const cancelled = AbortSignal.any([controller.signal, ...(signal ? [signal] : [])]);
        this.pending.set(owner, controller);
        try {
            return await this.createSession(owner, id, input, index, fasta, locus, cancelled);
        } catch (error) {
            if (error instanceof ViewerFileError) throw new HttpError(400, error.code);
            throw error;
        } finally {
            this.pending.delete(owner);
        }
    }

    private async createSession(owner: string, id: string, input: string, index: string, fasta: string, locus: string, cancelled: AbortSignal): Promise<LibraryViewerSession> {
        const position = localViewerLocus(locus);
        const address = await this.connection.require(id);
        const name = path.posix.basename(input.replaceAll("\\", "/"));
        const format = /\.bam$/i.test(name) ? "bam" : /\.cram$/i.test(name) ? "cram" : /\.(g\.?vcf|vcf)\.(gz|bgz)$/i.test(name) ? "vcf" : undefined;
        if (!format) throw new HttpError(400, "unsupportedFile");
        const referenceName = path.posix.basename(fasta.replaceAll("\\", "/"));
        if (!/\.(fa|fasta|fna)(\.gz)?$/i.test(referenceName)) throw new HttpError(400, "referenceMissing");
        const paths = [...new Set([input, index, fasta, `${fasta}.fai`, ...(/\.gz$/i.test(fasta) ? [`${fasta}.gzi`] : [])])];
        paths.forEach(remotePath);
        const entries: Grant["files"] = new Map();
        const urls = new Map<string, string>();
        const sessionID = randomBytes(24).toString("base64url");
        const token = randomBytes(32).toString("base64url");
        for (const file of paths) {
            cancelled.throwIfAborted();
            await this.connection.require(id);
            const response = await fetch(remoteURL(address.url, file), {
                method: "HEAD", headers: { authorization: `Bearer ${address.token}`, range: "bytes=0-0" },
                redirect: "error", signal: AbortSignal.any([cancelled, AbortSignal.timeout(30_000)]),
            }).catch(() => { throw new HttpError(502, "remoteUnavailable"); });
            const match = /^(?:bytes 0-0\/|bytes \*\/)(\d+)$/.exec(response.headers.get("content-range") ?? "");
            const size = Number(match?.[1]);
            if (![206, 416].includes(response.status) || !match || !Number.isSafeInteger(size) || size < 0
                || (response.status === 416 && size !== 0)) {
                throw new HttpError(response.status === 404 ? 404 : 502, "remoteUnavailable");
            }
            const fileID = randomBytes(24).toString("base64url");
            entries.set(fileID, { path: file, size });
            urls.set(file, `/viewer-remote-data/${sessionID}/${fileID}`);
        }
        await this.connection.require(id);
        cancelled.throwIfAborted();
        this.expire();
        while (this.grants.size >= 8) {
            const oldest = this.grants.keys().next().value;
            if (oldest) this.revoke(oldest);
        }
        this.grants.set(sessionID, { owner, connectionID: id, token, touched: Date.now(), files: entries, controller: new AbortController() });
        const url = (file: string) => {
            const value = urls.get(file);
            if (!value) throw new HttpError(500, "remoteUnavailable");
            return value;
        };
        const headers = { [viewerTokenHeader]: token };
        return {
            id: sessionID, locus: position,
            files: {
                reference: { id: "remote", name: referenceName, fastaURL: url(fasta), indexURL: url(`${fasta}.fai`),
                    ...(/\.gz$/i.test(fasta) ? { compressedIndexURL: url(`${fasta}.gzi`) } : {}), headers, wholeGenomeView: false },
                tracks: [{ name: name.replace(/[&<>"']/g, (character) => `&#${character.charCodeAt(0)};`), format,
                    type: format === "vcf" ? "variant" : "alignment", indexed: true, url: url(input), indexURL: url(index), headers }],
            },
        };
    }

    async serve(request: IncomingMessage, response: ServerResponse, pathname: string) {
        requireViewerLoopback(request);
        this.expire();
        const match = /^\/viewer-remote-data\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)$/.exec(pathname);
        const grant = match?.[1] ? this.grants.get(match[1]) : undefined;
        const file = match?.[2] ? grant?.files.get(match[2]) : undefined;
        const supplied = request.headers[viewerTokenHeader];
        if (!grant || !file || typeof supplied !== "string" || !equalToken(supplied, grant.token)) {
            throw new HttpError(401, "remoteGrantExpired");
        }
        if (request.method !== "GET" && request.method !== "HEAD") throw new HttpError(405, "remoteReadOnly");
        const address = await this.connection.require(grant.connectionID);
        const range = byteRange(request.headers.range, file.size);
        if (!range) {
            response.writeHead(416, { "content-range": `bytes */${file.size}`, "content-length": "0" });
            response.end();
            return;
        }
        const controller = new AbortController();
        const disconnect = () => controller.abort();
        response.on("close", disconnect);
        try {
            const { start, end, partial } = range;
            const length = Math.max(0, end - start + 1);
            const upstream = await fetch(remoteURL(address.url, file.path), {
                method: request.method, headers: { authorization: `Bearer ${address.token}`, ...(length ? { range: `bytes=${start}-${end}` } : {}) },
                redirect: "error", signal: AbortSignal.any([controller.signal, grant.controller.signal, AbortSignal.timeout(120_000)]),
            });
            if (upstream.status !== (length ? 206 : 200)
                || Number(upstream.headers.get("content-length")) !== length
                || (length && upstream.headers.get("content-range") !== `bytes ${start}-${end}/${file.size}`)) {
                await upstream.body?.cancel();
                throw new HttpError(502, "remoteFileChanged");
            }
            grant.touched = Date.now();
            response.writeHead(partial ? 206 : 200, { "content-type": "application/octet-stream", "cache-control": "no-store",
                "accept-ranges": "bytes", "content-length": String(length),
                ...(partial ? { "content-range": `bytes ${start}-${end}/${file.size}` } : {}) });
            if (request.method === "HEAD" || length === 0 || !upstream.body) {
                await upstream.body?.cancel();
                response.end();
                return;
            }
            await pipeline(Readable.from(rangeChunks(upstream.body, length)), response);
        } catch {
            if (response.headersSent) response.destroy();
            else throw new HttpError(502, "remoteUnavailable");
        } finally {
            response.off("close", disconnect);
            controller.abort();
        }
    }

    private expire() {
        for (const [id, grant] of this.grants) {
            if (Date.now() - grant.touched > 60 * 60 * 1000) this.revoke(id);
        }
    }
}

function remoteURL(base: string, file: string) {
    return `${base}/viewer-file?${new URLSearchParams({ path: file })}`;
}

function remotePath(value: string) {
    if (value.length > 4096 || value.includes("\0") || value.split(/[\\/]/).includes("..")
        || /^[/\\]{2}|^\\\\[?.]/.test(value) || !(value.startsWith("/") || /^[A-Za-z]:[\\/]/.test(value))) {
        throw new HttpError(403, "remotePathInvalid");
    }
}

function equalToken(actual: string, expected: string) {
    const a = Buffer.from(actual), b = Buffer.from(expected);
    return a.length === b.length && timingSafeEqual(a, b);
}

async function* rangeChunks(body: ReadableStream<Uint8Array>, length: number) {
    const reader = body.getReader();
    let remaining = length;
    try {
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            if (value.byteLength > remaining) throw new HttpError(502, "remoteFileChanged");
            remaining -= value.byteLength;
            yield value;
        }
        if (remaining !== 0) throw new HttpError(502, "remoteFileChanged");
    } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
    }
}
