import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.window = { innerHeight: 900 };
globalThis.document = { querySelector: () => null };

const { missingPrerequisites, installPrerequisite } = await import("../dist/web/src/server/prerequisites.js");
const { loadLocalizedBundle } = await import("../dist/web/src/server/bundle-loader.js");
const { createInitialState, state } = await import("../dist/web/src/client/state.js");
const { runAction } = await import("../dist/web/src/client/operations.js");
const { cancelPendingPrerequisites, continuePendingPrerequisites, installPendingPrerequisites } = await import("../dist/web/src/client/operations-prerequisites-dialog.js");
const { renderPrerequisitesDialog } = await import("../dist/web/src/client/view/prerequisites.js");
const { localizationLabels, parseTomlStrings } = await import("../dist/shared/localization.js");

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const wgsExtractBundleRoot = path.join(repoRoot, "examples/WGSExtract");
const englishLabels = localizationLabels(parseTomlStrings(await readFile(path.join(repoRoot, "resources/BuiltinStrings/strings.en.toml"), "utf8")));
const cram = "/data/sample.cram";
const hs37d5 = { id: "hs37d5", kind: "genome", name: "hs37d5", installArguments: ["ref", "library", "--install", "hs37d5"] };

function manifest() {
  return {
    id: "tool",
    displayName: "Tool",
    pages: [],
    prerequisites: {
      command: { executable: "tool", arguments: ["needs", "--"] },
      executables: { tool: [], "tool-calls": ["calls"] },
      kindLabels: { genome: "%{name} reference genome" },
    },
  };
}

const context = { fieldValues: { bam_path: cram }, checkedOptions: {}, configValues: {}, rowValues: {} };

/** A runProcess whose check reports `missing` (the current value each time it runs), recording every command. */
function fakeProcesses(missing) {
  const calls = [];
  const runProcess = async (executable, args) => {
    calls.push([executable, ...args]);
    const isCheck = args[0] === "needs";
    return { exitCode: 0, stdout: isCheck ? `log line\n${JSON.stringify({ missing: missing.current })}\n` : "", stderr: "" };
  };
  return { calls, runProcess };
}

test("the check is given the action's command line, after its executable's prefix", async () => {
  const { calls, runProcess } = fakeProcesses({ current: [hs37d5] });
  const action = { id: "snp", title: "SNPs", command: { executable: "tool-calls", arguments: ["snp", "--input", "{{bam_path}}"] } };

  const missing = await missingPrerequisites(manifest(), action, context, tmpdir(), runProcess);

  assert.deepEqual(missing, [hs37d5]);
  assert.deepEqual(calls, [["tool", "needs", "--", "calls", "snp", "--input", cram]]);
});

test("an action of another executable, or a check that fails, needs nothing", async (t) => {
  t.mock.method(console, "warn", () => {});
  const { calls, runProcess } = fakeProcesses({ current: [hs37d5] });
  const other = { id: "fastqc", title: "FastQC", command: { executable: "fastqc", arguments: ["{{bam_path}}"] } };
  assert.deepEqual(await missingPrerequisites(manifest(), other, context, tmpdir(), runProcess), []);
  assert.deepEqual(calls, []);

  const action = { id: "info", title: "Info", command: { executable: "tool", arguments: ["info"] } };
  const failing = async () => ({ exitCode: 1, stdout: "", stderr: "boom" });
  assert.deepEqual(await missingPrerequisites(manifest(), action, context, tmpdir(), failing), []);
  const garbled = async () => ({ exitCode: 0, stdout: "not json", stderr: "" });
  assert.deepEqual(await missingPrerequisites(manifest(), action, context, tmpdir(), garbled), []);
});

test("an install runs the arguments the check reports now, never ones the page sends", async () => {
  const missing = { current: [hs37d5] };
  const { calls, runProcess } = fakeProcesses(missing);
  const action = { id: "info", title: "Info", command: { executable: "tool", arguments: ["info", "--input", "{{bam_path}}"] } };

  const result = await installPrerequisite(manifest(), action, "hs37d5", context, undefined, tmpdir(), runProcess, () => {}, () => undefined);

  assert.equal(result.exitCode, 0);
  assert.deepEqual(calls.at(-1), ["tool", "ref", "library", "--install", "hs37d5"]);
});

test("an item installed meanwhile completes at once; a failing check or an uninstallable item is an error", async () => {
  const missing = { current: [] };
  const { calls, runProcess } = fakeProcesses(missing);
  const action = { id: "info", title: "Info", command: { executable: "tool", arguments: ["info", "--input", "{{bam_path}}"] } };
  const events = [];

  const result = await installPrerequisite(manifest(), action, "hs37d5", context, undefined, tmpdir(), runProcess, (event) => events.push(event), () => undefined);

  assert.equal(result.exitCode, 0);
  assert.deepEqual(events.map((event) => event.type), ["complete"]);
  assert.equal(calls.length, 1, "only the check ran");
  const failing = async () => ({ exitCode: 1, stdout: "", stderr: "boom" });
  await assert.rejects(
    installPrerequisite(manifest(), action, "hs37d5", context, undefined, tmpdir(), failing, () => {}, () => undefined),
    /exited 1: boom/,
  );
  missing.current = [{ ...hs37d5, installArguments: null }];
  await assert.rejects(
    installPrerequisite(manifest(), action, "hs37d5", context, undefined, tmpdir(), runProcess, () => {}, () => undefined),
    /can't install hs37d5/,
  );
});

test("WGSExtract checks its wgsextract actions with `ref needs`, and Unalign through the command its wrapper runs", async (t) => {
  const workspace = await mkdtemp(path.join(tmpdir(), "gui-for-cli-prerequisites-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const bundle = await loadLocalizedBundle("en", repoRoot, wgsExtractBundleRoot, workspace);
  const actions = bundle.manifest.pages.flatMap((page) => page.sections ?? []).flatMap((section) => section.actions ?? []);
  const basicInfo = actions.find((action) => action.id === "basic-info");
  const unalign = actions.find((action) => action.id === "bam-unalign");
  const wgsContext = { ...context, fieldValues: { bam_path: cram, out_dir: "/out" }, bundleRootPath: workspace };
  const { calls, runProcess } = fakeProcesses({ current: [] });

  await missingPrerequisites(bundle.manifest, basicInfo, wgsContext, workspace, runProcess);
  await missingPrerequisites(bundle.manifest, unalign, wgsContext, workspace, runProcess);

  const checked = calls.map((call) => call.slice(1));
  assert.deepEqual(checked, [
    ["ref", "needs", "--report-json", "--", "info", "--input", cram],
    ["ref", "needs", "--report-json", "--", "bam", "unalign", "--input", cram],
  ]);
  assert.equal(bundle.manifest.prerequisites.kindLabels.genome, "%{name} reference genome");
});

test("WGSExtract's Microarray leaves --ref out until a genome is chosen, so it runs with one installed after the page loaded", async (t) => {
  const workspace = await mkdtemp(path.join(tmpdir(), "gui-for-cli-prerequisites-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const bundle = await loadLocalizedBundle("en", repoRoot, wgsExtractBundleRoot, workspace);
  const actions = bundle.manifest.pages.flatMap((page) => page.sections ?? []).flatMap((section) => section.actions ?? []);
  const generate = actions.find((action) => action.id === "microarray-generate");
  const { calls, runProcess } = fakeProcesses({ current: [] });
  const fields = { bam_path: cram, microarray_formats: "23andme_v5" };

  await missingPrerequisites(bundle.manifest, generate, { ...context, fieldValues: { ...fields, ref_fasta: "" }, bundleRootPath: workspace }, workspace, runProcess);
  await missingPrerequisites(bundle.manifest, generate, { ...context, fieldValues: { ...fields, ref_fasta: "/ref/hs37d5.fa.gz" }, bundleRootPath: workspace }, workspace, runProcess);

  const [unchosen, chosen] = calls.map((call) => call.slice(1));
  assert.equal(unchosen.includes("--ref"), false);
  assert.deepEqual(chosen.slice(chosen.indexOf("--ref"), chosen.indexOf("--ref") + 2), ["--ref", "/ref/hs37d5.fa.gz"]);
});

/**
 * Answers the client's requests: the check reports `missing.current` (after `onCheck`, which may change the
 * world meanwhile); an install installs everything; runs and installs finish with exit 0.
 */
function fakeServer(t, missing, onCheck = () => {}) {
  const requests = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body ?? "{}");
    requests.push({ url, body });
    if (url === "/api/prerequisites") {
      onCheck();
      return new Response(JSON.stringify({ missing: missing.current }));
    }
    if (url === "/api/prerequisites/install/stream") {
      missing.current = [];
    }
    const lines = [{ type: "start", command: url }, { type: "complete", result: { exitCode: 0, command: url } }];
    return new Response(lines.map((line) => JSON.stringify(line)).join("\n"));
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  return requests;
}

function holdBack(t, missing, onCheck) {
  Object.assign(state, createInitialState(), { manifest: manifest(), labels: englishLabels });
  const requests = fakeServer(t, missing, onCheck);
  const action = { id: "info", title: "Info", command: { executable: "tool", arguments: ["info", "--input", "{{bam_path}}"] } };
  const reference = { pageID: "main", sectionID: "info", actionID: "info" };
  return { action, reference, requests, urls: () => requests.map((request) => request.url) };
}

test("an action whose reference genome is missing doesn't run; the dialog names it", async (t) => {
  const missing = { current: [hs37d5] };
  const { action, reference, urls } = holdBack(t, missing);

  await runAction(action, context, reference);

  assert.deepEqual(urls(), ["/api/prerequisites"]);
  const dialog = renderPrerequisitesDialog();
  assert.match(dialog, /<li>hs37d5 reference genome<\/li>/);
  assert.match(dialog, /data-prerequisites-install[^>]*>Install and Continue</);
  assert.match(dialog, /data-prerequisites-continue>Continue</);
  assert.match(dialog, /data-prerequisites-cancel>Cancel</);

  cancelPendingPrerequisites();
  assert.equal(state.pendingPrerequisites, null);
  assert.deepEqual(urls(), ["/api/prerequisites"]);
});

test("Install and Continue installs the genome, then runs the action", async (t) => {
  const missing = { current: [hs37d5] };
  const { action, reference, requests, urls } = holdBack(t, missing);
  await runAction(action, context, reference);

  await installPendingPrerequisites();

  assert.deepEqual(urls(), ["/api/prerequisites", "/api/prerequisites", "/api/prerequisites/install/stream", "/api/prerequisites", "/api/run/stream"]);
  assert.equal(requests[2].body.id, "hs37d5");
  assert.equal(state.pendingPrerequisites, null);
});

test("Install and Continue skips what was installed while the dialog was open, and runs the action", async (t) => {
  const missing = { current: [hs37d5] };
  const { action, reference, urls } = holdBack(t, missing);
  await runAction(action, context, reference);
  missing.current = [];

  await installPendingPrerequisites();

  assert.deepEqual(urls(), ["/api/prerequisites", "/api/prerequisites", "/api/prerequisites", "/api/run/stream"]);
});

test("a check that finishes after another bundle was loaded neither runs the action nor opens the dialog", async (t) => {
  const missing = { current: [] };
  const { action, reference, urls } = holdBack(t, missing, () => {
    state.manifest = manifest();
  });

  await runAction(action, context, reference);

  assert.deepEqual(urls(), ["/api/prerequisites"]);
  assert.equal(state.pendingPrerequisites, null);
});

test("Continue re-checks: still missing keeps the dialog and says so; once installed the action runs", async (t) => {
  const missing = { current: [hs37d5] };
  const { action, reference, urls } = holdBack(t, missing);
  await runAction(action, context, reference);

  await continuePendingPrerequisites();
  assert.deepEqual(urls(), ["/api/prerequisites", "/api/prerequisites"]);
  assert.match(renderPrerequisitesDialog(), /It still isn&#39;t installed/);

  missing.current = [];
  await continuePendingPrerequisites();
  assert.deepEqual(urls(), ["/api/prerequisites", "/api/prerequisites", "/api/prerequisites", "/api/run/stream"]);
});

test("data the app can't install offers no Install button", async (t) => {
  const { action, reference } = holdBack(t, { current: [{ ...hs37d5, installArguments: null }] });

  await runAction(action, context, reference);

  assert.doesNotMatch(renderPrerequisitesDialog(), /data-prerequisites-install/);
});
