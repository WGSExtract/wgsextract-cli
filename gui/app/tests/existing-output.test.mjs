import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, unlink, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
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

  // actionWithForce appends --force for force commands and --restart for step workflows.
  const vepAction = { id: "vep-run", title: "VEP", command: { executable: "tool", arguments: ["vep", "run", "--input", "{{bam_path}}"] } };
  const forcedVep = await actionWithForce(vepAction, ctx, root);
  assert.deepEqual(forcedVep.command.optionalArguments, [["--force"]]);
  const realignAction = {
    id: "realign",
    title: "Realign",
    command: { executable: "tool", arguments: ["realign", "--input", "{{bam_path}}"] },
    serverOperation: { operation: "realign", parameters: {} },
  };
  const forcedRealign = await actionWithForce(realignAction, ctx, root);
  assert.deepEqual(forcedRealign.command.optionalArguments, [["--restart"]]);
  assert.equal(forcedRealign.serverOperation.parameters.restart, true);
  assert.equal(forcedRealign.serverOperation.parameters.force, undefined);
  const forcedMt = await actionWithForce(mtAction, ctx, root);
  assert.equal(forcedMt, mtAction);
});

test("findExistingOutput rejects untrusted manifests pointing outside outdir and matches prior --no-resume runs", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "wgse-existing-containment-"));
  t.after(async () => rm(root, { recursive: true, force: true }));
  const inputDir = path.join(root, "sample");
  const outdir = path.join(inputDir, "wgse");
  await mkdir(outdir, { recursive: true });
  const bam = path.join(inputDir, "s.bam");
  const outsideFile = path.join(root, "outside.html");
  await writeFile(bam, "bam");
  await writeFile(outsideFile, "<html>outside</html>");
  const now = new Date(Date.now() + 10_000);
  await utimes(outsideFile, now, now);

  // Crafted manifest pointing outside outdir must be rejected and never remembered in ResultPaths.
  await writeFile(path.join(outdir, "crafted.manifest.json"), JSON.stringify({
    schema: "wgsextract.result/1",
    command: "report",
    status: "ok",
    exit_code: 0,
    inputs: [{ path: bam, role: "input", size_bytes: 3 }],
    outputs: [{ path: outsideFile, role: "report_html", exists: true }],
    provenance: { argv: ["report", "--input", bam] },
  }));
  const reportAction = { id: "report", title: "Report", openOutput: ["report_html"], command: { executable: "tool", arguments: ["report", "--input", "{{bam_path}}"] } };
  const ctx = { fieldValues: { bam_path: bam }, checkedOptions: {}, configValues: {}, rowValues: {} };
  const resultPaths = new ResultPaths();
  assert.equal((await findExistingOutput(baseManifest, reportAction, ctx, root, resultPaths)).kind, "none");
  assert.equal(resultPaths.has(outsideFile), false);

  // Prior run recorded with --no-resume matches an ordinary rerun check.
  const localReport = path.join(outdir, "report.html");
  await writeFile(localReport, "<html>ok</html>");
  await utimes(localReport, now, now);
  await writeFile(path.join(outdir, "good.manifest.json"), JSON.stringify({
    schema: "wgsextract.result/1",
    command: "report",
    status: "ok",
    exit_code: 0,
    inputs: [{ path: bam, role: "input", size_bytes: 3 }],
    outputs: [{ path: "report.html", role: "report_html", exists: true }],
    provenance: { argv: ["report", "--input", bam, "--no-resume"] },
  }));
  const matched = await findExistingOutput(baseManifest, reportAction, ctx, root, resultPaths);
  assert.equal(matched.kind, "display");
  assert.equal(path.basename(matched.openPath), "report.html");
  assert.equal(resultPaths.has(matched.openPath), true);

  // Modifying config.toml after the cached output invalidates reuse (e.g., changed scientific defaults).
  const cfgPath = path.join(root, "config.toml");
  await writeFile(cfgPath, "microarray_min_dp = 10\n");
  const later = new Date(Date.now() + 20_000);
  await utimes(cfgPath, later, later);
  const cfgManifest = {
    ...baseManifest,
    pages: [{
      id: "settings",
      title: "Settings",
      sections: [{
        id: "settings-paths",
        title: "Settings",
        controls: [{ id: "wgs_settings", kind: "configEditor", configFile: { path: cfgPath, format: "toml" } }],
      }],
    }],
  };
  assert.equal((await findExistingOutput(cfgManifest, reportAction, ctx, root, resultPaths)).kind, "none");
});

test("client opens cached HTML directly for display actions and prompts before force-rerunning output-only actions", async (t) => {
  const requests = [];
  let serverResponse = { kind: "display", openPath: "/out/report.html", outputs: ["report.html"] };
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

  // Display reuse opens the report without running /api/run/stream and keeps Force Run Again available.
  await runAction(action, ctx, ref);
  assert.deepEqual(requests.map((r) => r.url), ["/api/existing-output", "/api/result/open"]);
  assert.equal(requests[1].body.path, "/out/report.html");
  assert.ok(state.pendingAlreadyRun, "openOutput-only reuse still offers Force Run Again");
  cancelPendingAlreadyRun();
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

test("Force Run Again preserves skipExistingCheck and forceRerun across prerequisite prompts", async (t) => {
  const { continuePendingPrerequisites } = await import("../dist/web/src/client/operations-prerequisites-dialog.js");
  const requests = [];
  let missingCalls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init?.body ?? "{}");
    requests.push({ url, body });
    if (url === "/api/prerequisites") {
      missingCalls += 1;
      return new Response(JSON.stringify({
        missing: missingCalls === 1 ? [{ id: "hs38", kind: "reference", name: "hs38" }] : [],
      }));
    }
    if (url === "/api/run/stream") {
      const lines = [{ type: "start", command: "tool" }, { type: "complete", result: { exitCode: 0, command: "tool" } }];
      return new Response(lines.map((line) => JSON.stringify(line)).join("\n"));
    }
    throw new Error(`Unexpected URL: ${url}`);
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  const prereqManifest = { ...baseManifest, prerequisites: { command: { executable: "tool", arguments: ["ref", "needs"] } } };
  Object.assign(state, createInitialState(), { manifest: prereqManifest, labels: englishLabels });
  const ctx = { fieldValues: { bam_path: "/data/s.bam" }, checkedOptions: {}, configValues: {}, rowValues: {} };
  const ref = { pageID: "extract", sectionID: "regions", actionID: "region-mt" };
  const extract = { id: "region-mt", title: "Extract MT", command: { executable: "tool", arguments: ["extract", "region", "--input", "{{bam_path}}"] } };

  await runAction(extract, ctx, ref, { skipExistingCheck: true, forceRerun: true });
  assert.equal(state.pendingPrerequisites?.skipExistingCheck, true);
  assert.equal(state.pendingPrerequisites?.forceRerun, true);
  await continuePendingPrerequisites();
  assert.equal(requests.some((r) => r.url === "/api/existing-output"), false);
  const streamReq = requests.find((r) => r.url === "/api/run/stream");
  assert.ok(streamReq, "Continue after prerequisites still runs /api/run/stream");
  assert.equal(streamReq.body.force, true);
});
