import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, unlink, utimes, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

globalThis.localStorage ??= { getItem: () => null, setItem() {} };
globalThis.window ??= { innerHeight: 900 };
globalThis.document ??= { querySelector: () => null };

const { actionWithForce, findExistingOutput } = await import("../dist/web/src/server/existing-output.js");
const { ResultPaths } = await import("../dist/web/src/server/result-paths.js");
const { createInitialState, state } = await import("../dist/web/src/client/state.js");
const { runAction } = await import("../dist/web/src/client/operations-actions.js");
const { cancelPendingAlreadyRun, forceRunPendingAlreadyRun } = await import("../dist/web/src/client/operations-already-run.js");
const { renderAlreadyRunDialog } = await import("../dist/web/src/client/view/already-run.js");
const { localizationLabels, parseTomlStrings } = await import("../dist/shared/localization.js");
const { readFile } = await import("node:fs/promises");

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const englishLabels = localizationLabels(parseTomlStrings(await readFile(path.join(repoRoot, "resources/BuiltinStrings/strings.en.toml"), "utf8")));

const baseManifest = {
  id: "tool",
  displayName: "Tool",
  pages: [],
  resultDocument: {
    format: "wgsextract.result/1",
    executables: ["tool"],
    arguments: ["--result-file", "{{resultDocumentPath}}"],
    cardArguments: ["--card"],
  },
};

async function makeWorkspace(t) {
  const base = path.resolve("../out/tests");
  await mkdir(base, { recursive: true });
  const root = await mkdtemp(path.join(base, "existing-output-"));
  t.after(() => rm(root, { force: true, recursive: true }));
  const sampleDir = path.join(root, "sample");
  const wgseDir = path.join(sampleDir, "wgse");
  await mkdir(wgseDir, { recursive: true });
  const bam = path.join(sampleDir, "sample.bam");
  await writeFile(bam, "bam-bytes");
  const older = new Date("2026-01-01T00:00:00Z");
  await utimes(bam, older, older);
  return { root, sampleDir, wgseDir, bam };
}

async function writeManifest(dir, name, doc, mtime = new Date("2026-01-02T00:00:00Z")) {
  const filePath = path.join(dir, name);
  await writeFile(filePath, JSON.stringify(doc, null, 2));
  await utimes(filePath, mtime, mtime);
  return filePath;
}

test("fresh result-card and HTML-report outputs return kind=display and distinguish options", async (t) => {
  const { root, wgseDir, bam } = await makeWorkspace(t);
  const card = path.join(wgseDir, "sample.bam_info.card.html");
  await writeFile(card, "<html>card</html>");
  const newer = new Date("2026-01-02T00:00:00Z");
  await utimes(card, newer, newer);

  await writeManifest(wgseDir, "sample.bam_info.manifest.json", {
    schema: "wgsextract.result/1",
    command: "info",
    status: "ok",
    exit_code: 0,
    inputs: [{ path: bam, kind: "bam", role: "input", exists: true, size_bytes: 9 }],
    outputs: [{ path: card, kind: "html", role: "result_card", exists: true, size_bytes: 17 }],
    metrics: {},
    warnings: [],
    provenance: {
      argv: ["--config", "/tmp/c.toml", "info", "--input", bam, "--result-file", "/tmp/r.json", "--card"],
      params: { input: bam, detailed: false },
      finished: "2026-01-02T00:00:00Z",
    },
  });

  const paths = new ResultPaths();
  const ctx = { fieldValues: { bam_path: bam }, checkedOptions: {}, configValues: {}, rowValues: {} };
  const basicInfo = { id: "basic-info", title: "Basic Info", resultCard: true, command: { executable: "tool", arguments: ["info", "--input", "{{bam_path}}"] } };
  const detailedInfo = { id: "detailed-info", title: "Detailed Info", resultCard: true, command: { executable: "tool", arguments: ["info", "--input", "{{bam_path}}", "--detailed"] } };

  const found = await findExistingOutput(baseManifest, basicInfo, ctx, root, paths);
  assert.equal(found.kind, "display");
  assert.equal(found.cardPath, card);
  assert.equal(paths.has(card), true, "Cached card path is authorized for /api/result/card");

  const detailed = await findExistingOutput(baseManifest, detailedInfo, ctx, root, new ResultPaths());
  assert.equal(detailed.kind, "none", "Basic Info card must not satisfy Detailed Info");

  // HTML report (openOutput): fresh report displays, but a newer manifest in wgse/ makes `report` stale.
  const reportHtml = path.join(wgseDir, "sample_report.html");
  await writeFile(reportHtml, "<html>report</html>");
  await utimes(reportHtml, newer, newer);
  await writeManifest(wgseDir, "sample_report.html.manifest.json", {
    schema: "wgsextract.result/1",
    command: "report",
    status: "ok",
    exit_code: 0,
    inputs: [{ path: bam, kind: "bam", role: "input", exists: true, size_bytes: 9 }],
    outputs: [{ path: reportHtml, kind: "html", role: "report_html", exists: true, size_bytes: 19 }],
    metrics: {},
    warnings: [],
    provenance: { argv: ["report", "--input", bam], params: { input: bam }, finished: "2026-01-02T00:00:00Z" },
  });
  const reportAction = { id: "report", title: "Report", openOutput: ["report_html"], command: { executable: "tool", arguments: ["report", "--input", "{{bam_path}}"] } };
  const reportPaths = new ResultPaths();
  const reportHit = await findExistingOutput(baseManifest, reportAction, ctx, root, reportPaths);
  assert.equal(reportHit.kind, "display");
  assert.equal(reportHit.openPath, reportHtml);
  assert.equal(reportPaths.has(reportHtml), true);

  // A newer analysis manifest invalidates the summary report.
  await writeManifest(wgseDir, "other.manifest.json", { schema: "wgsextract.result/1", command: "info", status: "ok", exit_code: 0, outputs: [] }, new Date("2026-01-03T00:00:00Z"));
  assert.equal((await findExistingOutput(baseManifest, reportAction, ctx, root, new ResultPaths())).kind, "none");
});

test("output-only commands return kind=already-run when fresh, and kind=none when stale, deleted, or forced", async (t) => {
  const { root, wgseDir, bam } = await makeWorkspace(t);
  const mtBam = path.join(wgseDir, "sample_MT.bam");
  await writeFile(mtBam, "mt-reads");
  const newer = new Date("2026-01-02T00:00:00Z");
  await utimes(mtBam, newer, newer);

  await writeManifest(wgseDir, "sample_MT.bam.manifest.json", {
    schema: "wgsextract.result/1",
    command: "extract region",
    status: "ok",
    exit_code: 0,
    inputs: [{ path: bam, kind: "bam", role: "input", exists: true, size_bytes: 9 }],
    outputs: [{ path: mtBam, kind: "bam", role: "mt_bam", exists: true, size_bytes: 8 }],
    metrics: {},
    warnings: [],
    provenance: { argv: ["extract", "region", "--input", bam, "--region", "chrM"], params: { input: bam, region: "chrM" } },
  });

  const ctx = { fieldValues: { bam_path: bam }, checkedOptions: {}, configValues: {}, rowValues: {} };
  const mtAction = { id: "region-mt", title: "Extract MT", command: { executable: "tool", arguments: ["extract", "region", "--input", "{{bam_path}}", "--region", "chrM"] } };
  const yAction = { id: "region-y", title: "Extract Y", command: { executable: "tool", arguments: ["extract", "region", "--input", "{{bam_path}}", "--region", "chrY"] } };

  const already = await findExistingOutput(baseManifest, mtAction, ctx, root, new ResultPaths());
  assert.equal(already.kind, "already-run");
  assert.deepEqual(already.outputs, ["sample_MT.bam"]);
  assert.equal((await findExistingOutput(baseManifest, yAction, ctx, root, new ResultPaths())).kind, "none");

  // Updating the input BAM makes the output stale.
  const newest = new Date("2026-01-05T00:00:00Z");
  await utimes(bam, newest, newest);
  assert.equal((await findExistingOutput(baseManifest, mtAction, ctx, root, new ResultPaths())).kind, "none");

  // Restore input timestamp, then delete the output file -> kind=none.
  const older = new Date("2026-01-01T00:00:00Z");
  await utimes(bam, older, older);
  await unlink(mtBam);
  assert.equal((await findExistingOutput(baseManifest, mtAction, ctx, root, new ResultPaths())).kind, "none");

  // Excluded/forced actions are never intercepted.
  const indexAction = { id: "bam-index", title: "Index", command: { executable: "tool", arguments: ["bam", "index", "--input", "{{bam_path}}"] } };
  assert.equal((await findExistingOutput(baseManifest, indexAction, ctx, root, new ResultPaths())).kind, "none");

  // actionWithForce appends --force only to CLI commands that accept it.
  const vepAction = { id: "vep-run", title: "VEP", command: { executable: "tool", arguments: ["vep", "run", "--input", "{{bam_path}}"] } };
  const forcedVep = await actionWithForce(vepAction, ctx, root);
  assert.deepEqual(forcedVep.command.optionalArguments, [["--force"]]);
  const forcedMt = await actionWithForce(mtAction, ctx, root);
  assert.equal(forcedMt, mtAction);
});

test("client opens cached HTML directly for display actions and prompts before force-rerunning output-only actions", async (t) => {
  const requests = [];
  let serverResponse = { kind: "display", openPath: "/out/report.html" };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init?.body ?? "{}");
    requests.push({ url, body });
    if (url === "/api/existing-output") {
      return new Response(JSON.stringify(serverResponse));
    }
    if (url === "/api/result/open") {
      return new Response(JSON.stringify({ ok: true }));
    }
    if (url === "/api/run/stream") {
      const lines = [{ type: "start", command: "tool" }, { type: "complete", result: { exitCode: 0, command: "tool" } }];
      return new Response(lines.map((line) => JSON.stringify(line)).join("\n"));
    }
    throw new Error(`Unexpected URL: ${url}`);
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  Object.assign(state, createInitialState(), { manifest: baseManifest, labels: englishLabels });
  const ctx = { fieldValues: { bam_path: "/data/s.bam" }, checkedOptions: {}, configValues: {}, rowValues: {} };
  const ref = { pageID: "workflows", sectionID: "reports", actionID: "report" };
  const action = { id: "report", title: "Report", openOutput: ["report_html"], command: { executable: "tool", arguments: ["report", "--input", "{{bam_path}}"] } };

  // Display reuse opens the report without running /api/run/stream.
  await runAction(action, ctx, ref);
  assert.deepEqual(requests.map((r) => r.url), ["/api/existing-output", "/api/result/open"]);
  assert.equal(requests[1].body.path, "/out/report.html");
  assert.equal(state.pendingAlreadyRun, null);

  // Output-only reuse opens the already-run pop-up; Cancel keeps it from running; Force Run Again runs with force=true.
  requests.length = 0;
  serverResponse = { kind: "already-run", outputs: ["sample_MT.bam"] };
  const extract = { id: "region-mt", title: "Extract MT", command: { executable: "tool", arguments: ["extract", "region", "--input", "{{bam_path}}"] } };
  await runAction(extract, ctx, { pageID: "extract", sectionID: "regions", actionID: "region-mt" });
  assert.deepEqual(requests.map((r) => r.url), ["/api/existing-output"]);
  const dialog = renderAlreadyRunDialog();
  assert.match(dialog, /Command already run/);
  assert.match(dialog, /<li>sample_MT\.bam<\/li>/);
  assert.match(dialog, /data-already-run-force[^>]*>Force Run Again</);
  assert.match(dialog, /data-already-run-cancel>Cancel</);

  cancelPendingAlreadyRun();
  assert.equal(state.pendingAlreadyRun, null);
  assert.deepEqual(requests.map((r) => r.url), ["/api/existing-output"]);

  await runAction(extract, ctx, { pageID: "extract", sectionID: "regions", actionID: "region-mt" });
  await forceRunPendingAlreadyRun();
  assert.equal(state.pendingAlreadyRun, null);
  const streamReq = requests.find((r) => r.url === "/api/run/stream");
  assert.ok(streamReq, "Force Run Again submits /api/run/stream");
  assert.equal(streamReq.body.force, true);
});
