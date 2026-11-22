import assert from "node:assert/strict";
import test from "node:test";

test("#1112/#1149: current selection and originating controls survive asynchronous renders", async () => {
    const messages = [];
    class Element extends EventTarget {
        style = {};
        dataset = {};
        isConnected = true;
        setAttribute() {}
        removeAttribute() {}
        focus() { document.activeElement = this; }
        append() {}
        remove() {}
    }
    const button = new Element();
    button.dataset.viewReads = "bam_path";
    const viewerAction = new Element();
    viewerAction.dataset.viewerAction = "info-bam/info-commands/viewer";
    viewerAction.dataset.viewerInput = "{{bam_path}}";
    const app = new Element();
    const result = new Element();
    result.dataset.resultViewer = "/genomes/region.bam";
    result.dataset.viewerLocus = "chr1:200-400";
    app.querySelectorAll = (selector) => selector === "[data-view-reads]" ? [button]
        : selector === "[data-viewer-action]" ? [viewerAction]
        : selector === "[data-result-viewer]" ? [result] : [];
    let currentInput = button;
    let currentViewerAction = viewerAction;
    let currentResult = result;
    const sidebar = new Element();
    app.querySelector = (selector) => selector.startsWith("[data-result-viewer=") ? currentResult
        : selector.startsWith("[data-view-reads=") ? currentInput
        : selector.startsWith("[data-viewer-action=") ? currentViewerAction : sidebar;
    const frameWindow = { postMessage: (message) => messages.push(message) };
    globalThis.localStorage = { getItem: () => null };
    globalThis.window = new EventTarget();
    globalThis.location = { origin: "http://localhost" };
    globalThis.CSS = { escape: (value) => value.replaceAll("\\", "\\\\").replaceAll('"', '\\"') };
    globalThis.HTMLElement = Element;
    const created = [];
    globalThis.document = {
        querySelector: () => app,
        createElement(tag) {
            const element = new Element();
            created.push({ tag, element });
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
    document.activeElement = button;
    button.dispatchEvent(new Event("click"));
    window.dispatchEvent(Object.assign(new Event("message"), {
        origin: location.origin, source: frameWindow, data: { type: "genomeViewerReady" },
    }));
    assert.equal(messages[0].inputPath, "{{usersWorkspace}}/mygenomes/new.bam");
    currentInput = new Element();
    button.isConnected = false;
    created.find((item) => item.tag === "button").element.dispatchEvent(new Event("click"));
    assert.equal(document.activeElement, currentInput, "Returning focuses the re-rendered input opener.");
    document.activeElement = document.body;
    result.dispatchEvent(new Event("click"));
    window.dispatchEvent(Object.assign(new Event("message"), {
        origin: location.origin, source: frameWindow, data: { type: "genomeViewerReady" },
    }));
    assert.equal(messages[1].inputPath, "/genomes/region.bam");
    assert.equal(messages[1].locus, "chr1:200-400");
    currentResult = new Element();
    result.isConnected = false;
    created.findLast((item) => item.tag === "button").element.dispatchEvent(new Event("click"));
    assert.equal(document.activeElement, currentResult, "#1149: body focus and a replaced result opener restore the originating result action.");
    state.fieldValues.bam_path = "{{usersWorkspace}}/mygenomes/sample.cram";
    document.activeElement = viewerAction;
    viewerAction.dispatchEvent(new Event("click"));
    window.dispatchEvent(Object.assign(new Event("message"), {
        origin: location.origin, source: frameWindow, data: { type: "genomeViewerReady" },
    }));
    assert.equal(messages[2].inputPath, "{{usersWorkspace}}/mygenomes/sample.cram");
    assert.equal(messages[2].locus, "");
    currentViewerAction = new Element();
    viewerAction.isConnected = false;
    created.findLast((item) => item.tag === "button").element.dispatchEvent(new Event("click"));
    assert.equal(document.activeElement, currentViewerAction, "Returning restores focus to the re-rendered Viewer action button.");
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
