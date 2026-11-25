import test from "node:test";
import assert from "node:assert/strict";

globalThis.window ??= { localStorage: { getItem: () => null, setItem: () => {} } };
globalThis.localStorage ??= globalThis.window.localStorage;
globalThis.document ??= {};

import { docUrl, renderTextWithDocLinks } from "../dist/shared/docs-links.js";

test("docUrl maps CLI and GUI markdown doc paths to wgse.bio rendered HTML URLs", () => {
    assert.equal(docUrl("privacy.md"), "https://wgse.bio/wgsextract-cli/docs/privacy.html");
    assert.equal(
        docUrl("docs/hardware.md#nvidia-gpus-parabricks"),
        "https://wgse.bio/wgsextract-cli/docs/hardware.html#nvidia-gpus-parabricks",
    );
    assert.equal(docUrl("docs/README.md"), "https://wgse.bio/wgsextract-cli/docs/index.html");
    assert.equal(docUrl("gui/docs/privacy.md"), "https://wgse.bio/wgsextract-cli/app/docs/privacy.html");
    assert.equal(
        docUrl("gui/devdocs/architecture.md#privacy-and-network-boundary"),
        "https://wgse.bio/wgsextract-cli/app/devdocs/architecture.html#privacy-and-network-boundary",
    );
});

test("renderTextWithDocLinks escapes HTML and linkifies wgse.bio URLs and repo doc references", () => {
    const rendered = renderTextWithDocLinks(
        "Missing data <tag> (see docs/privacy.md and https://wgse.bio/wgsextract-cli/app/docs/settings.html).",
    );
    assert.match(rendered, /&lt;tag&gt;/);
    assert.match(
        rendered,
        /<a class="doc-link" href="https:\/\/wgse\.bio\/wgsextract-cli\/docs\/privacy\.html" target="_blank" rel="noopener noreferrer">docs\/privacy\.md<\/a>/,
    );
    assert.match(
        rendered,
        /<a class="doc-link" href="https:\/\/wgse\.bio\/wgsextract-cli\/app\/docs\/settings\.html" target="_blank" rel="noopener noreferrer">https:\/\/wgse\.bio\/wgsextract-cli\/app\/docs\/settings\.html<\/a>\)\./,
    );
    // Backticks and braces terminate URLs cleanly without leaking into href.
    const wrapped = renderTextWithDocLinks("JSON: {\"url\": `https://wgse.bio/wgsextract-cli/docs/privacy.html`}");
    assert.match(
        wrapped,
        /`<a class="doc-link" href="https:\/\/wgse\.bio\/wgsextract-cli\/docs\/privacy\.html" target="_blank" rel="noopener noreferrer">https:\/\/wgse\.bio\/wgsextract-cli\/docs\/privacy\.html<\/a>`\}/,
    );
});

test("GUI error surfaces render clickable doc links for CLI and GUI doc references", async () => {
    const { renderInlineError } = await import("../dist/web/src/client/model.js");
    const { renderResultNotice, renderResultSummary } = await import("../dist/web/src/client/result-view.js");
    const { renderTerminalText } = await import("../dist/web/src/client/terminal-output.js");
    const msg = "Rerun with --download (see https://wgse.bio/wgsextract-cli/docs/privacy.html).";
    assert.match(renderInlineError(msg), /href="https:\/\/wgse\.bio\/wgsextract-cli\/docs\/privacy\.html"/);
    assert.match(
        renderResultNotice({ kind: "usage", message: "See docs/privacy.md for details." }),
        /href="https:\/\/wgse\.bio\/wgsextract-cli\/docs\/privacy\.html"/,
    );
    assert.match(
        renderResultSummary({
            status: "failed",
            command: "qc ancestry",
            error: { code: "download_required", message: msg },
            warnings: [{ code: "hint", message: "Check gui/docs/settings.md." }],
            metrics: {},
            outputs: [],
            raw: {},
        }),
        /href="https:\/\/wgse\.bio\/wgsextract-cli\/app\/docs\/settings\.html"/,
    );
    assert.match(
        renderTerminalText({ id: "t1", kind: "error", title: "Err", body: `${msg}\n` }),
        /href="https:\/\/wgse\.bio\/wgsextract-cli\/docs\/privacy\.html"/,
    );
});
