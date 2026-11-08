import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.window = { innerHeight: 900 };
globalThis.document = { querySelector: () => null };

const { loadLocalizedBundle } = await import("../dist/web/src/server/bundle-loader.js");
const { isActionVisible } = await import("../dist/shared/rendering.js");
const { createInitialState, state } = await import("../dist/web/src/client/state.js");
const { runAction } = await import("../dist/web/src/client/operations.js");
const { renderConfirmationDialog } = await import("../dist/web/src/client/view.js");

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const wgsExtractBundleRoot = path.join(repoRoot, "examples/WGSExtract");

test("#528: WGSExtract's Open Bug Report on GitHub asks first, and runs nothing until confirmed", async (t) => {
  // A workspace of its own: loading writes default config files, which other test files write in the bundle at the same time.
  const workspace = await mkdtemp(path.join(tmpdir(), "gui-for-cli-confirmation-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const bundle = await loadLocalizedBundle("en", repoRoot, wgsExtractBundleRoot, workspace);
  const page = bundle.manifest.pages.find((candidate) => candidate.sections?.some((section) => section.id === "report-issue"));
  const section = page.sections.find((candidate) => candidate.id === "report-issue");
  const context = { fieldValues: {}, checkedOptions: {}, configValues: {}, rowValues: {}, bundleRootPath: "/bundle", placeholderLabels: {}, lastResultDocumentPaths: {} };
  const action = section.actions.find((candidate) => candidate.title === "Open Bug Report on GitHub" && isActionVisible(candidate, context));
  Object.assign(state, createInitialState(), { manifest: bundle.manifest, bundleRootPath: "/bundle" });
  const requests = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (url) => {
    requests.push(url);
    return Promise.reject(new Error("unexpected request"));
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  await runAction(action, context, { pageID: page.id, sectionID: section.id, actionID: action.id });

  assert.deepEqual(requests, []);
  assert.equal(state.pendingConfirmation?.action, action);
  const dialog = renderConfirmationDialog();
  assert.match(dialog, /Open the bug report on GitHub\?/);
  assert.match(dialog, /No issue is created until you review it there and submit it yourself\./);
  assert.match(dialog, /data-confirm-run[^>]*>Open in Browser</);
  assert.match(dialog, /data-confirm-cancel>Cancel</);
});
