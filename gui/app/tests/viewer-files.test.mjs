import assert from "node:assert/strict";
import { mkdir, mkdtemp, open, rm, symlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import test from "node:test";
import { HttpError } from "../dist/web/src/server/errors.js";
import { applySecurityHeaders, guardRequest } from "../dist/web/src/server/request-guard.js";
import { guardedViewerPath, requireViewerLoopback, ViewerFiles } from "../dist/web/src/server/viewer-files.js";

async function fixture(t) {
    const output = path.resolve("../out/tests");
    await mkdir(output, { recursive: true });
    const root = await mkdtemp(path.join(output, "viewer-"));
    t.after(() => rm(root, { force: true, recursive: true }));
    const library = path.join(root, "library");
    await mkdir(library);
    const file = path.join(library, "reads.bam");
    await writeFile(file, "0123456789");
    const reads = [];
    const files = new ViewerFiles((read) => reads.push(read));
    const grant = await files.grant([file], [library], "bundle");
    const server = createServer(async (request, response) => {
        applySecurityHeaders(response);
        try {
            const pathname = new URL(request.url, "http://localhost").pathname;
            guardRequest(request, pathname, { apiToken: "unused" });
            await files.serve(request, response, pathname, [library], "bundle");
        } catch (error) {
            if (!response.headersSent) response.writeHead(error.status ?? 500);
            response.end();
        }
    });
    t.after(async () => {
        server.closeAllConnections();
        await new Promise((resolve) => server.close(resolve));
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    const url = `${base}${grant.urls.get(file)}`;
    return { root, library, file, reads, files, grant, url, base };
}

test("#1090: viewer Range endpoint requires a scoped header credential, exact origin and loopback", async (t) => {
    const f = await fixture(t);
    for (const headers of [{}, { "x-gfc-token": "unused" }, { "x-gfc-viewer-token": "wrong" }]) {
        assert.equal((await fetch(f.url, { headers })).status, 401);
    }
    assert.equal((await fetch(`${f.url}?token=${Object.values(f.grant.headers)[0]}`)).status, 401);
    assert.equal((await fetch(f.url, { headers: { ...f.grant.headers, origin: "https://evil.example" } })).status, 403);
    for (const address of ["192.168.1.1", "10.0.0.1", ""]) {
        assert.throws(() => requireViewerLoopback({ socket: { remoteAddress: address, localAddress: "127.0.0.1" } }), { status: 403 });
        assert.throws(() => requireViewerLoopback({ socket: { remoteAddress: "127.0.0.1", localAddress: address } }), { status: 403 });
    }
    const response = await fetch(f.url, { headers: f.grant.headers });
    assert.equal(await response.text(), "0123456789");
    assert.equal(response.headers.get("access-control-allow-origin"), null);
    assert.equal(response.headers.get("cache-control"), "no-store");
    f.files.revoke(f.grant.id);
    assert.equal((await fetch(f.url, { headers: f.grant.headers })).status, 401);
});

test("#1090: byte ranges, HEAD, suffixes and unsatisfiable ranges have correct HTTP semantics", async (t) => {
    const f = await fixture(t);
    for (const [range, text, contentRange] of [
        ["bytes=2-5", "2345", "bytes 2-5/10"],
        ["bytes=8-99", "89", "bytes 8-9/10"],
        ["bytes=-3", "789", "bytes 7-9/10"],
        ["bytes=8-", "89", "bytes 8-9/10"],
    ]) {
        const response = await fetch(f.url, { headers: { ...f.grant.headers, range } });
        assert.equal(response.status, 206);
        assert.equal(response.headers.get("content-range"), contentRange);
        assert.equal(response.headers.get("content-length"), String(text.length));
        assert.equal(await response.text(), text);
    }
    for (const range of ["bytes=10-", "bytes=5-2", "bytes=-0", "bytes=0-1,4-5", "bytes=-", "bytes=0-NaN", "bytes=99999999999999999-", "items=0-1"]) {
        const response = await fetch(f.url, { headers: { ...f.grant.headers, range } });
        assert.equal(response.status, 416, range);
        assert.equal(response.headers.get("content-range"), "bytes */10");
        assert.equal(await response.text(), "");
    }
    const count = f.reads.length;
    const head = await fetch(f.url, { method: "HEAD", headers: { ...f.grant.headers, range: "bytes=2-5" } });
    assert.equal(head.status, 206);
    assert.equal(head.headers.get("content-length"), "4");
    assert.equal(await head.text(), "");
    assert.equal(f.reads.length, count);
    assert.equal((await fetch(f.url, { method: "POST", headers: f.grant.headers })).status, 405);
});

test("#1090: only touched blocks are served from a multi-gigabyte file; whole-file fallback is refused", async (t) => {
    const f = await fixture(t);
    const handle = await open(f.file, "r+");
    await handle.truncate(4 * 1024 ** 3);
    await handle.close();
    const grant = await f.files.grant([f.file], [f.library], "bundle");
    const url = `${f.base}${grant.urls.get(f.file)}`;
    const response = await fetch(url, { headers: { ...grant.headers, range: "bytes=0-9" } });
    assert.equal(response.status, 206);
    assert.equal(await response.text(), "0123456789");
    assert.equal(f.reads.reduce((sum, read) => sum + read.bytes, 0), 10);
    assert.equal((await fetch(url, { headers: grant.headers })).status, 416);
    for (const range of ["bytes=0-", "bytes=-67108864"]) {
        assert.equal((await fetch(url, { headers: { ...grant.headers, range } })).status, 416);
    }
    assert.equal((await fetch(f.url, { headers: f.grant.headers })).status, 409);
});

test("#1148: a high-depth region larger than 32 MiB streams completely and can be cancelled", async (t) => {
    const f = await fixture(t);
    const size = 33 * 1024 ** 2;
    const handle = await open(f.file, "r+");
    await handle.truncate(size);
    await handle.write(Buffer.from("end"), 0, 3, size - 3);
    await handle.close();
    const grant = await f.files.grant([f.file], [f.library], "bundle");
    const url = `${f.base}${grant.urls.get(f.file)}`;
    const headers = { ...grant.headers, range: `bytes=0-${size - 1}` };
    const response = await fetch(url, { headers });
    assert.equal(response.status, 206);
    assert.equal(response.headers.get("content-range"), `bytes 0-${size - 1}/${size}`);
    assert.equal(response.headers.get("content-length"), String(size));
    let received = 0;
    let last;
    for await (const chunk of response.body) {
        if (received === 0) assert.equal(Buffer.from(chunk).subarray(0, 10).toString(), "0123456789");
        received += chunk.length;
        last = chunk;
    }
    assert.equal(received, size);
    assert.equal(Buffer.from(last).subarray(-3).toString(), "end");
    const controller = new AbortController();
    const cancelled = await fetch(url, { headers, signal: controller.signal });
    await cancelled.body.getReader().read();
    controller.abort();
    const next = await fetch(url, { headers: { ...grant.headers, range: `bytes=${size - 3}-${size - 1}` } });
    assert.equal(next.status, 206);
    assert.equal(await next.text(), "end");
    f.files.revoke(grant.id);
    assert.equal((await fetch(url, { headers })).status, 401);
});

test("#1227: an un-ranged HEAD reports the size of a file over 32 MiB without reading it", async (t) => {
    const f = await fixture(t);
    const size = 33 * 1024 ** 2;
    const handle = await open(f.file, "r+");
    await handle.truncate(size);
    await handle.close();
    const grant = await f.files.grant([f.file], [f.library], "bundle");
    const url = `${f.base}${grant.urls.get(f.file)}`;
    const head = await fetch(url, { method: "HEAD", headers: grant.headers });
    assert.equal(head.status, 200);
    assert.equal(head.headers.get("content-length"), String(size));
    assert.equal(head.headers.get("accept-ranges"), "bytes");
    assert.equal(head.headers.get("content-range"), null);
    assert.equal(await head.text(), "");
    assert.deepEqual(f.reads, []);
    assert.equal((await fetch(url, { headers: grant.headers })).status, 416);
    assert.equal((await fetch(url, { method: "HEAD", headers: { "x-gfc-viewer-token": "wrong" } })).status, 401);
    assert.equal((await fetch(f.url, { method: "HEAD", headers: f.grant.headers })).status, 409);
});

test("#1212: a deleted viewer file returns 404 and a missing library returns 403", async (t) => {
    const f = await fixture(t);
    const missingRoot = path.join(f.library, "optional");
    const missingFile = path.join(missingRoot, "reads.bam");
    await assert.rejects(guardedViewerPath(missingFile, [missingRoot]), { status: 403 });
    await assert.rejects(guardedViewerPath(missingFile, [missingRoot, f.library]), { status: 404 });
    await rm(f.file);
    await assert.rejects(guardedViewerPath(f.file, [f.library]), (error) => error instanceof HttpError && error.status === 404);
    assert.equal((await fetch(f.url, { headers: f.grant.headers })).status, 404);
    await rm(f.library, { recursive: true });
    assert.equal((await fetch(f.url, { headers: f.grant.headers })).status, 403);
    await writeFile(f.library, "not a directory");
    assert.equal((await fetch(f.url, { headers: f.grant.headers })).status, 403);
});

test("#1090: traversal, directories, symlink escapes and changed file targets cannot be served", async (t) => {
    const f = await fixture(t);
    const secret = path.join(f.root, "secret.bam");
    await writeFile(secret, "private");
    for (const candidate of [secret, `${f.library}/../secret.bam`, f.library, `${f.file}\0`, "relative.bam"]) {
        await assert.rejects(guardedViewerPath(candidate, [f.library]), { status: 403 });
    }
    const escape = path.join(f.library, "escape.bam");
    try {
        await symlink(secret, escape);
    } catch (error) {
        if (error.code === "EPERM" && process.platform === "win32") return;
        throw error;
    }
    await assert.rejects(f.files.grant([escape], [f.library], "bundle"), { status: 403 });
    await rm(f.file);
    await symlink(secret, f.file);
    assert.equal((await fetch(f.url, { headers: f.grant.headers })).status, 403);
    const directoryLink = path.join(f.library, "other");
    await symlink(f.root, directoryLink, process.platform === "win32" ? "junction" : "dir");
    await assert.rejects(guardedViewerPath(path.join(directoryLink, "secret.bam"), [f.library]), { status: 403 });
});
