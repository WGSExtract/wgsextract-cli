import assert from "node:assert/strict";
import test from "node:test";

const origin = "http://localhost";
const settle = () => new Promise(setImmediate);
const message = (source, data) => Object.assign(new Event("message"), { origin, source, data });

class Element extends EventTarget {
    style = {};
    dataset = {};
    hidden = false;
    disabled = false;
    checked = false;
    textContent = "";
    setAttribute() {}
    removeAttribute() {}
    append() {}
    remove() {}
    focus() {}
}

test("#1160 review: a failed embedded Forget keeps the destination and reports failure", async () => {
    class Details extends Element { open = true; }
    globalThis.HTMLElement = Element;
    globalThis.HTMLDetailsElement = Details;
    globalThis.location = { origin };
    const child = new EventTarget();
    child.setTimeout = setTimeout;
    child.clearTimeout = clearTimeout;
    const parent = {
        postMessage(data) {
            queueMicrotask(() => child.dispatchEvent(message(parent, {
                type: "genomeViewerRemoteResult", requestID: data.requestID,
                result: data.action === "forget" ? { problem: "remoteUnavailable" }
                    : { id: "configured-server", destination: "https://genomes.example/v1" },
            })));
        },
    };
    child.parent = parent;
    globalThis.window = child;
    const details = new Details();
    const controls = new Map(["[data-remote-form]", '[name="remote-consent"]', "[data-remote-destination]",
        "[data-remote-forget]", "[data-remote-plaintext]", "[data-remote-import]"].map((name) => [name, new Element()]));
    details.querySelector = (selector) => controls.get(selector);
    details.querySelectorAll = () => [...controls.values()];
    const root = { querySelector: () => details };
    const statuses = [];
    let cleared = 0;
    const { bindRemoteViewer } = await import("../dist/web/src/client/genome-viewer-remote-form.js");
    bindRemoteViewer(root, (key) => key === "remoteDestination" ? "Destination: %{destination}" : key,
        (key) => statuses.push(key), async () => {}, () => { cleared++; }, () => {});
    details.dispatchEvent(new Event("toggle"));
    await settle();
    assert.equal(controls.get("[data-remote-form]").hidden, false);
    controls.get("[data-remote-forget]").dispatchEvent(new Event("click"));
    await settle();
    assert.equal(cleared, 0);
    assert.equal(controls.get("[data-remote-form]").hidden, false);
    assert.match(controls.get("[data-remote-destination]").textContent, /genomes\.example/);
    assert.equal(statuses.at(-1), "remoteUnavailable");
});

test("#1160 review: superseded remote results cannot replace local files, and embedded release/pagehide revoke grants", async (t) => {
    const parent = new EventTarget(), child = new EventTarget();
    parent.parent = parent;
    child.parent = parent;
    child.setTimeout = setTimeout;
    child.clearTimeout = clearTimeout;
    const replies = [];
    child.postMessage = (data) => { replies.push(data); child.dispatchEvent(message(parent, data)); };
    parent.postMessage = (data) => parent.dispatchEvent(message(child, data));
    globalThis.window = parent;
    globalThis.location = { origin };
    globalThis.HTMLElement = Element;
    globalThis.CSS = { escape: (value) => value };
    globalThis.__GFC_API_TOKEN__ = "main-app-test";
    const app = new Element(), opener = new Element(), created = [];
    app.querySelector = () => opener;
    globalThis.document = {
        querySelector: () => app,
        createElement(tag) {
            const element = new Element();
            if (tag === "iframe") element.contentWindow = child;
            created.push({ tag, element });
            return element;
        },
        body: new Element(),
    };
    const oldFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = oldFetch; globalThis.window = parent; created.find((value) => value.tag === "button")?.element.dispatchEvent(new Event("click")); });
    const closed = [];
    let completeRemote;
    let pending = true;
    globalThis.fetch = async (url, options) => {
        const body = JSON.parse(options.body);
        if (url === "/api/viewer/close") {
            closed.push({ id: body.id, keepalive: options.keepalive });
            return Response.json({});
        }
        if (url === "/api/viewer/open") return Response.json({ id: "new-local", files: {}, locus: "chr1:1-4" });
        assert.equal(url, "/api/viewer/remote/open");
        // Network-boundary delay models a response already queued when cancellation arrives.
        if (pending) return new Promise((resolve) => { completeRemote = resolve; });
        return Response.json({ id: "remote-exit", files: {}, locus: "chr1:1-4" });
    };
    const { openGenomeViewer } = await import("../dist/web/src/client/genome-viewer-launcher.js");
    const { releaseLibraryViewer } = await import("../dist/web/src/client/genome-viewer-library.js");
    openGenomeViewer("en", { viewerStrings: { back: "Back" } }, opener);
    parent.dispatchEvent(message(child, { type: "genomeViewerRemoteRequest", action: "open", requestID: "remote-pending", body: {} }));
    parent.dispatchEvent(message(child, { type: "genomeViewerOpenLibrary", requestID: "local-request", path: "/genomes/reads.bam", locus: "chr1:1-4" }));
    await settle();
    assert.equal(replies.find((reply) => reply.type === "genomeViewerLibrary").result.id, "new-local");
    completeRemote(Response.json({ id: "obsolete-remote", files: {}, locus: "chr1:1-4" }));
    await settle();
    assert.ok(closed.some((value) => value.id === "obsolete-remote"));
    assert.ok(!closed.some((value) => value.id === "new-local"));
    assert.ok(!replies.some((reply) => reply.type === "genomeViewerRemoteResult" && reply.result.files));
    globalThis.window = child;
    await releaseLibraryViewer();
    assert.ok(closed.some((value) => value.id === "new-local"));
    globalThis.window = parent;
    pending = false;
    parent.dispatchEvent(message(child, { type: "genomeViewerRemoteRequest", action: "open", requestID: "remote-final", body: {} }));
    await settle();
    parent.dispatchEvent(new Event("pagehide"));
    await settle();
    assert.ok(closed.some((value) => value.id === "remote-exit" && value.keepalive === true));
});

test("#1160 review: standalone pagehide closes a native-import owner before selection completes", async (t) => {
    const window = new EventTarget();
    window.parent = window;
    globalThis.window = window;
    const oldFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = oldFetch; });
    const closed = [];
    let owner, finish;
    let started;
    const importing = new Promise((resolve) => { started = resolve; });
    globalThis.fetch = async (url, options) => {
        const body = JSON.parse(options.body);
        if (url === "/api/viewer/close") {
            closed.push({ id: body.id, keepalive: options.keepalive });
            return Response.json({});
        }
        assert.equal(url, "/api/viewer/remote/import");
        owner = body.requestID;
        started();
        return new Promise((resolve) => { finish = resolve; });
    };
    const { requestRemoteViewer } = await import("../dist/web/src/client/genome-viewer-remote.js");
    const pending = requestRemoteViewer("import", { title: "Choose configuration" });
    await importing;
    window.dispatchEvent(new Event("pagehide"));
    await settle();
    assert.ok(closed.some((value) => value.id === owner && value.keepalive === true));
    finish(Response.json({ problem: "remoteConnectionChanged" }));
    assert.equal((await pending).problem, "remoteConnectionChanged");
});
