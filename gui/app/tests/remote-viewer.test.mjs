import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import test from "node:test";
import { createRequestHandler } from "../dist/web/src/server/routes.js";
import { RemoteViewerConnection } from "../dist/web/src/server/remote-viewer-connection.js";
import { isolateWorkspaceEnvironment } from "./workspace-fixture.mjs";

async function listen(t, handler) {
    const server = createServer(handler);
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    t.after(async () => {
        server.closeAllConnections();
        await new Promise((resolve) => server.close(resolve));
    });
    return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function fixture(t) {
    const output = path.resolve("../out/tests");
    await mkdir(output, { recursive: true });
    const root = await mkdtemp(path.join(output, "remote-viewer-"));
    isolateWorkspaceEnvironment(t, root);
    t.after(() => rm(root, { recursive: true, force: true }));
    const bundle = path.join(root, "bundle");
    await mkdir(bundle);
    const remoteToken = randomBytes(32).toString("base64url");
    const appToken = randomBytes(32).toString("base64url");
    const prefix = `/${randomBytes(12).toString("hex")}`;
    const data = `${prefix}/sample.bam`, index = `${data}.bai`, fasta = `${prefix}/ref.fa`;
    const files = new Map([[data, Buffer.from("0123456789")], [index, Buffer.from("index")],
        [fasta, Buffer.from(">chr1\nACGT\n")], [`${fasta}.fai`, Buffer.from("chr1\t4\t6\t4\t5\n")]]);
    const requests = [];
    let redirect = false;
    let delayedHead;
    const remote = await listen(t, async (request, response) => {
        if (request.headers.authorization !== `Bearer ${remoteToken}`) { response.writeHead(401).end(); return; }
        const url = new URL(request.url, "http://localhost");
        if (url.pathname !== "/v1/viewer-file") { response.writeHead(404).end(); return; }
        requests.push({ file: url.searchParams.get("path"), method: request.method, range: request.headers.range, cookie: request.headers.cookie });
        if (request.method === "HEAD" && delayedHead) {
            const gate = delayedHead;
            delayedHead = undefined;
            gate.arrived();
            await gate.wait;
            if (response.destroyed) return;
        }
        if (redirect) { response.writeHead(302, { location: `${url.origin}/unapproved` }).end(); return; }
        const bytes = files.get(url.searchParams.get("path"));
        if (!bytes) { response.writeHead(404).end(); return; }
        const range = /^bytes=(\d+)-(\d+)$/.exec(request.headers.range ?? "");
        const start = range ? Number(range[1]) : 0;
        const end = range ? Math.min(Number(range[2]), bytes.length - 1) : bytes.length - 1;
        if (start >= bytes.length) {
            response.writeHead(416, { "content-range": `bytes */${bytes.length}`, "content-length": "0" }).end();
            return;
        }
        response.writeHead(range ? 206 : 200, { "content-length": String(end - start + 1),
            ...(range ? { "content-range": `bytes ${start}-${end}/${bytes.length}` } : {}) });
        response.end(request.method === "HEAD" ? undefined : bytes.subarray(start, end + 1));
    });
    const selected = path.join(root, "operator-config.toml");
    await writeFile(selected, `url = ${JSON.stringify(`${remote.base}/v1`)}\ntoken = ${JSON.stringify(remoteToken)}\n`);
    // The native picker is an OS boundary; page JavaScript only asks to open it.
    let delayedPick;
    const picker = await listen(t, async (_request, response) => {
        if (delayedPick) {
            const gate = delayedPick;
            delayedPick = undefined;
            gate.arrived();
            await gate.wait;
        }
        response.end(JSON.stringify({ path: selected }));
    });
    const privateConfig = path.join(root, "private", "config.toml");
    const context = {
        apiToken: appToken, bundleRoot: bundle, manifest: { id: "wgs-extract", pages: [] },
        maxBodyBytes: 1048576, remoteViewerConfigFile: privateConfig,
        nativePicker: { port: picker.server.address().port, token: appToken },
    };
    const backend = await listen(t, createRequestHandler(context));
    const post = (route, body = {}, token = appToken) => fetch(`${backend.base}${route}`, {
        method: "POST", headers: { "content-type": "application/json", "x-gfc-token": token }, body: JSON.stringify(body),
    });
    const choice = await post("/api/network-settings", { consent: "deferred" });
    assert.equal(choice.status, 200);
    const configure = async () => {
        const response = await post("/api/viewer/remote/import", {
            title: "Choose configuration", requestID: randomBytes(24).toString("base64url"),
        });
        assert.equal(response.status, 200);
        const text = await response.text();
        assert.ok(!text.includes(remoteToken));
        return JSON.parse(text);
    };
    const open = async (connection, extra = {}) => post("/api/viewer/remote/open", {
        requestID: randomBytes(24).toString("base64url"),
        id: connection.id, path: data, index, reference: fasta, locus: "chr1:1-4", confirmed: true, ...extra,
    });
    return { ...backend, bundle, selected, privateConfig, remoteToken, files, requests, post, configure, open,
        data, index, fasta, context, setRedirect: (value) => { redirect = value; },
        delayHead() {
            let arrived, release;
            const started = new Promise((resolve) => { arrived = resolve; });
            const wait = new Promise((resolve) => { release = resolve; });
            delayedHead = { arrived, wait };
            return { started, release };
        },
        delayPick() {
            let arrived, release;
            const started = new Promise((resolve) => { arrived = resolve; });
            const wait = new Promise((resolve) => { release = resolve; });
            delayedPick = { arrived, wait };
            return { started, release };
        },
    };
}

test("#1160: configuring and loading a saved destination makes no remote request or token response", async (t) => {
    const f = await fixture(t);
    assert.deepEqual(await (await f.post("/api/viewer/remote/status")).json(), {});
    const destination = await f.configure();
    assert.match(destination.destination, /^http:\/\/127\.0\.0\.1:\d+\/v1$/);
    assert.equal(f.requests.length, 0);
    assert.equal((await f.open(destination, { confirmed: false })).status, 403);
    assert.equal(f.requests.length, 0);
    assert.match(await readFile(f.privateConfig, "utf8"), /token =/);
    if (process.platform !== "win32") assert.equal((await stat(f.privateConfig)).mode & 0o777, 0o600);
    const restarted = await listen(t, createRequestHandler(f.context));
    const response = await fetch(`${restarted.base}/api/viewer/remote/status`, {
        method: "POST", headers: { "content-type": "application/json", "x-gfc-token": f.context.apiToken }, body: "{}",
    });
    const publicStatus = await response.text();
    assert.ok(!publicStatus.includes(f.remoteToken));
    assert.equal(JSON.parse(publicStatus).destination, destination.destination);
    assert.equal(f.requests.length, 0);
    assert.equal((await f.post("/api/viewer/remote/import", { title: "Choose" }, "wrong")).status, 401);
    const stolen = await fetch(`${f.base}/bundle-file?path=../private/config.toml`);
    assert.equal(stolen.status, 404);
});

test("#1160: selected remote files use file-only grants, exact ranges and revocable backend credentials", async (t) => {
    const f = await fixture(t);
    const connection = await f.configure();
    const response = await f.open(connection);
    assert.equal(response.status, 200);
    const publicSession = await response.text();
    assert.ok(!publicSession.includes(f.remoteToken));
    assert.ok(!publicSession.includes(connection.destination));
    const session = JSON.parse(publicSession), track = session.files.tracks[0];
    const read = (url = track.url, headers = track.headers, method = "GET") => fetch(`${f.base}${url}`, { method, headers });
    assert.equal((await read(track.url, {})).status, 401);
    const range = await read(track.url, { ...track.headers, range: "bytes=2-5", cookie: "secret=not-forwarded" });
    assert.equal(range.status, 206);
    assert.equal(range.headers.get("content-range"), "bytes 2-5/10");
    assert.equal(await range.text(), "2345");
    assert.equal(range.headers.get("set-cookie"), null);
    assert.equal(f.requests.at(-1).cookie, undefined);
    const head = await read(track.url, track.headers, "HEAD");
    assert.equal(head.status, 200);
    assert.equal(head.headers.get("content-length"), "10");
    assert.equal(await head.text(), "");
    assert.equal(await (await read(track.indexURL)).text(), "index");
    assert.equal((await read(`${track.url}/../unknown`)).status, 401);
    await f.post("/api/viewer/close", { id: session.id });
    assert.equal((await read()).status, 401);
    const second = await (await f.open(connection)).json();
    await f.post("/api/viewer/remote/forget");
    assert.equal((await read(second.files.tracks[0].url, second.files.tracks[0].headers)).status, 401);
    assert.deepEqual(await (await f.post("/api/viewer/remote/status")).json(), {});
});

test("#1160: path traversal, redirects and changed destinations cannot expand grants", async (t) => {
    const f = await fixture(t);
    const connection = await f.configure();
    for (const value of ["/allowed/../private.bam", "//host/private.bam", "\\\\host\\share\\private.bam", "relative.bam"]) {
        assert.equal((await f.open(connection, { path: value })).status, 403);
    }
    assert.equal(f.requests.length, 0);
    const next = await f.configure();
    assert.notEqual(next.id, connection.id);
    assert.equal((await f.open(connection)).status, 401);
    f.setRedirect(true);
    assert.equal((await f.open(next)).status, 502);
    assert.equal(f.requests.length, 1, "redirects never receive a bearer token");
});

test("#1160: whole-file fallback is bounded but closed ranges stream larger bins", async (t) => {
    const f = await fixture(t);
    const size = 33 * 1024 * 1024;
    f.files.set(f.data, Buffer.alloc(size, 0x41));
    const connection = await f.configure();
    const session = await (await f.open(connection)).json(), track = session.files.tracks[0];
    const full = await fetch(`${f.base}${track.url}`, { headers: track.headers });
    assert.equal(full.status, 416);
    const range = await fetch(`${f.base}${track.url}`, { headers: { ...track.headers, range: `bytes=0-${size - 1}` } });
    assert.equal(range.status, 206);
    let received = 0;
    for await (const chunk of range.body) {
        assert.ok(chunk.every((byte) => byte === 0x41));
        received += chunk.byteLength;
    }
    assert.equal(received, size);
    f.files.set(f.data, Buffer.from("changed-size"));
    assert.equal((await fetch(`${f.base}${track.url}`, { headers: { ...track.headers, range: "bytes=0-3" } })).status, 502);
});

test("#1227: an un-ranged remote HEAD reports the size of a file over 32 MiB without a body", async (t) => {
    const f = await fixture(t);
    const size = 33 * 1024 * 1024;
    f.files.set(f.data, Buffer.alloc(size, 0x41));
    const connection = await f.configure();
    const session = await (await f.open(connection)).json(), track = session.files.tracks[0];
    const before = f.requests.length;
    const head = await fetch(`${f.base}${track.url}`, { method: "HEAD", headers: track.headers });
    assert.equal(head.status, 200);
    assert.equal(head.headers.get("content-length"), String(size));
    assert.equal(head.headers.get("accept-ranges"), "bytes");
    assert.equal(await head.text(), "");
    assert.deepEqual(f.requests.slice(before).map(({ file, method, range }) => ({ file, method, range })),
        [{ file: f.data, method: "HEAD", range: `bytes=0-${size - 1}` }]);
    assert.equal((await fetch(`${f.base}${track.url}`, { headers: track.headers })).status, 416);
    f.files.set(f.data, Buffer.alloc(size + 1, 0x41));
    assert.equal((await fetch(`${f.base}${track.url}`, { method: "HEAD", headers: track.headers })).status, 502);
});

test("#1160 review: concurrent initial status loads share one stable connection ID", async (t) => {
    const f = await fixture(t);
    await f.configure();
    const connection = new RemoteViewerConnection(f.privateConfig);
    const statuses = await Promise.all(Array.from({ length: 20 }, () => connection.status()));
    assert.equal(new Set(statuses.map((status) => status.id)).size, 1);
    assert.equal((await connection.status()).id, statuses[0].id);
    assert.equal(f.requests.length, 0);
});

test("#1160 review: Forget wins over an already pending credential import", async (t) => {
    const f = await fixture(t);
    const connection = new RemoteViewerConnection(f.privateConfig);
    const importing = connection.import(f.selected, connection.beginImport("pending-owner"));
    const forgetting = connection.forget();
    const [imported, forgotten] = await Promise.allSettled([importing, forgetting]);
    assert.equal(imported.status, "rejected");
    assert.equal(forgotten.status, "fulfilled");
    assert.deepEqual(await connection.status(), {});
    await assert.rejects(stat(f.privateConfig), { code: "ENOENT" });
    assert.equal(f.requests.length, 0);
});

test("#1160 review: Forget and owner closure stop pending opens before more paths are sent", async (t) => {
    for (const operation of ["forget", "owner-close", "replace"]) {
        const f = await fixture(t);
        const connection = await f.configure();
        const owner = randomBytes(24).toString("base64url");
        const gate = f.delayHead();
        const opening = f.open(connection, { requestID: owner });
        await gate.started;
        if (operation === "forget") await f.post("/api/viewer/remote/forget");
        else if (operation === "replace") await f.configure();
        else await f.post("/api/viewer/close", { id: owner });
        gate.release();
        const response = await opening;
        assert.ok(response.status >= 400);
        assert.equal(f.requests.length, 1, `${operation} must stop disclosure of index/reference paths`);
    }
});

test("#1160 review: closing a request owner before open arrives cannot issue a grant", async (t) => {
    const f = await fixture(t);
    const connection = await f.configure();
    const owner = randomBytes(24).toString("base64url");
    await f.post("/api/viewer/close", { id: owner });
    assert.equal((await f.open(connection, { requestID: owner })).status, 401);
    assert.equal(f.requests.length, 0);
});

test("#1160 review: Forget or viewer closure cancels a native import awaiting selection", async (t) => {
    for (const operation of ["forget", "owner-close"]) {
        const f = await fixture(t);
        const owner = randomBytes(24).toString("base64url");
        const gate = f.delayPick();
        const importing = f.post("/api/viewer/remote/import", { title: "Choose", requestID: owner });
        await gate.started;
        if (operation === "forget") await f.post("/api/viewer/remote/forget");
        else await f.post("/api/viewer/close", { id: owner });
        gate.release();
        assert.equal((await importing).status, 409);
        assert.deepEqual(await (await f.post("/api/viewer/remote/status")).json(), {});
        await assert.rejects(stat(f.privateConfig), { code: "ENOENT" });
        assert.equal(f.requests.length, 0);
    }
});
