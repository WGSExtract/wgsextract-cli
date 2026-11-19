import assert from "node:assert/strict";
import test from "node:test";

test("#1112: View reads opens the current selection even before the edited form rerenders", async () => {
    const messages = [];
    class Element extends EventTarget {
        style = {};
        dataset = {};
        setAttribute() {}
        removeAttribute() {}
        focus() {}
        append() {}
    }
    const button = new Element();
    button.dataset.viewReads = "bam_path";
    const app = new Element();
    app.querySelectorAll = (selector) => selector === "[data-view-reads]" ? [button] : [];
    const frameWindow = { postMessage: (message) => messages.push(message) };
    globalThis.localStorage = { getItem: () => null };
    globalThis.window = new EventTarget();
    globalThis.location = { origin: "http://localhost" };
    globalThis.document = {
        querySelector: () => app,
        createElement(tag) {
            const element = new Element();
            if (tag === "iframe") element.contentWindow = frameWindow;
            return element;
        },
        body: new Element(),
    };
    const { state } = await import("../dist/web/src/client/state.js");
    const { bindGenomeViewerEvents } = await import("../dist/web/src/client/genome-viewer-entry.js");
    state.fieldValues.bam_path = "/genomes/old.bam";
    bindGenomeViewerEvents();
    // Saving is asynchronous; the already-rendered button must not capture old.bam.
    state.fieldValues.bam_path = "{{usersWorkspace}}/mygenomes/new.bam";
    button.dispatchEvent(new Event("click"));
    window.dispatchEvent(Object.assign(new Event("message"), {
        origin: location.origin, source: frameWindow, data: { type: "genomeViewerReady" },
    }));
    assert.equal(messages[0].inputPath, "{{usersWorkspace}}/mygenomes/new.bam");
});

test("#1112: standalone grants are revoked on replacement, manual selection and page exit with authentication", async (t) => {
    const window = new EventTarget();
    window.parent = window;
    globalThis.window = window;
    globalThis.__GFC_API_TOKEN__ = "standalone-test";
    const grants = new Set();
    let counter = 0;
    let keepaliveRevocations = 0;
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });
    globalThis.fetch = async (url, options) => {
        assert.equal(options.headers["x-gfc-token"], "standalone-test");
        const body = JSON.parse(options.body);
        if (url === "/api/viewer/open") {
            const id = String(++counter);
            grants.add(id);
            return Response.json({ id, locus: "chr1:1-4", files: {} });
        }
        assert.equal(url, "/api/viewer/close");
        if (options.keepalive) keepaliveRevocations++;
        grants.delete(body.id);
        return Response.json({});
    };
    const { requestLibraryViewer, releaseLibraryViewer } = await import("../dist/web/src/client/genome-viewer-library.js");
    for (let index = 0; index < 10; index++) {
        const result = await requestLibraryViewer("sample.bam", "");
        assert.deepEqual([...grants], [result.id], "abandoned grants cannot fill the eight-session limit");
    }
    await releaseLibraryViewer();
    assert.equal(grants.size, 0);
    await requestLibraryViewer("sample.bam", "");
    window.dispatchEvent(new Event("pagehide"));
    assert.equal(grants.size, 0);
    assert.equal(keepaliveRevocations, 1);
    window.dispatchEvent(new Event("pageshow"));
    await requestLibraryViewer("sample.bam", "");
    assert.equal(grants.size, 1);
    await releaseLibraryViewer();
});
