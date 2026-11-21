import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";

globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.window = { innerHeight: 900 };

const { runBundleTest } = await import("../dist/web/src/server/bundle-test-runner.js");
const { commandWithResultArguments, parseResultDocument, preferredOutput, resultCardOutput, resultNotice } = await import("../dist/shared/result-document.js");
const { createInitialState, state } = await import("../dist/web/src/client/state.js");
const { renderResultNotice, renderResultSummary } = await import("../dist/web/src/client/result-view.js");

test("929 writer-generated record types require emitted defaults without narrowing normalized future errors", async (t) => {
  const directory = path.resolve(import.meta.dirname, "../../out/tests/result-records-929", `${process.pid}-${Date.now()}`);
  await mkdir(directory, { recursive: true });
  t.after(() => rm(directory, { recursive: true, force: true }));
  const file = path.join(directory, "contract.mts");
  const modulePath = (name) => path.relative(directory, path.resolve(import.meta.dirname, "../shared", name)).replaceAll("\\", "/");
  const source = `
import type { ResultDocumentRecord, ResultErrorRecord, ResultFileRecord, ResultProvenanceRecord, ResultReferenceRecord, ResultWarningRecord } from "${modulePath("generated/result-records.js")}";
import type { ReportDocumentRecord, ReportFindingDocument, ReportPgxDocument } from "${modulePath("generated/report-records.js")}";
import type { ResultError, ResultFile } from "${modulePath("result-document.js")}";
type OptionalKeys<T> = { [Key in keyof T]-?: {} extends Pick<T, Key> ? Key : never }[keyof T];
type RequireNone<T extends never> = T;
// Reject even one optional key, including nullable/defaulted wire fields.
type FileFieldsRequired = RequireNone<OptionalKeys<ResultFileRecord>>;
type WarningFieldsRequired = RequireNone<OptionalKeys<ResultWarningRecord>>;
type ErrorFieldsRequired = RequireNone<OptionalKeys<ResultErrorRecord>>;
type ResultFieldsRequired = RequireNone<OptionalKeys<ResultDocumentRecord>>;
type ProvenanceFieldsRequired = RequireNone<OptionalKeys<ResultProvenanceRecord>>;
type ReferenceFieldsRequired = RequireNone<OptionalKeys<ResultReferenceRecord>>;
type ReportFieldsRequired = RequireNone<OptionalKeys<ReportDocumentRecord>>;
type ReportSampleFieldsRequired = RequireNone<OptionalKeys<ReportDocumentRecord["sample"]>>;
type RequiredKeys<T> = Exclude<keyof T, OptionalKeys<T>>;
type LegacySectionsStayOptional = RequireNone<Extract<"ancestry" | "carrier_screening" | "acmg_research", RequiredKeys<ReportDocumentRecord["sections"]>>>;
// @ts-expect-error The emitted report has a versioned literal schema identity.
const wrongSchema: ReportDocumentRecord["schema"] = "wgsextract.result/1";
// @ts-expect-error Enum metadata constrains the actual finding writer.
const wrongConfidence: ReportFindingDocument["confidence"] = "future_confidence";
// @ts-expect-error PGx enum metadata must not become an unconstrained string.
const wrongPgx: ReportPgxDocument["status"] = "future_status";
const wire: ResultFileRecord = {
  path: "out.bam", role: "output", kind: "bam", exists: false,
  size_bytes: null, mtime: null, fingerprint: null, index: null,
  metrics: { count: 1, ratio: 0.5, flag: true, missing: null, label: "test" },
};
const warning: ResultWarningRecord = { code: "low_coverage", message: "", details: { depth: 1 } };
const error: ResultErrorRecord = { kind: "usage", message: "" };
// @ts-expect-error A canonical writer error uses a declared ErrorKind.
const unknown: ResultErrorRecord = { kind: "future_kind", message: "" };
const normalized: ResultFile = { path: wire.path, role: wire.role, kind: wire.kind,
  exists: wire.exists, sizeBytes: wire.size_bytes, metrics: wire.metrics };
const tolerant: ResultError = { kind: "future_kind", message: error.message };
`;
  await writeFile(file, source);
  const require = createRequire(import.meta.url);
  const packageFile = require.resolve("typescript/package.json");
  const compiler = path.resolve(path.dirname(packageFile), require(packageFile).bin.tsc);
  const compiled = spawnSync(process.execPath, [compiler, "--ignoreConfig", "--strict", "--noEmit",
    "--skipLibCheck", "--target", "es2022", "--module", "nodenext", "--moduleResolution", "nodenext",
    "--types", "node", "--typeRoots", path.resolve(import.meta.dirname, "../node_modules/@types"), file],
  { encoding: "utf8", cwd: path.resolve(import.meta.dirname, "..") });
  assert.equal(compiled.status, 0, `${compiled.stdout}${compiled.stderr}${compiled.error?.message ?? ""}`);
});

// A CLI following the wgsextract.result/1 contract: logs on stderr, a human summary on stdout, and
// the document in the --result-file file or, with --json, alone on stdout. MODE picks the run: ok
// writes report.txt (5 bytes); wrong-size records 99 bytes; two-reports lists a second report
// it did not write; usage refuses its options with exit 2.
const fakeCli = String.raw`
import { writeFileSync } from "node:fs";
import path from "node:path";
const [mode, ...options] = process.argv.slice(2);
console.error("log: running " + mode);
const report = path.resolve("report.txt");
const okReport = { path: report, role: "report", kind: "text", size_bytes: 5 };
const documents = {
  ok: { status: "ok", exit_code: 0, outputs: [okReport] },
  "wrong-size": { status: "ok", exit_code: 0, outputs: [{ ...okReport, size_bytes: 99 }] },
  "two-reports": { status: "ok", exit_code: 0, outputs: [okReport, { ...okReport, path: path.resolve("report-2.txt") }] },
  usage: { status: "failed", exit_code: 2, outputs: [], error: { kind: "usage", message: "--a and --b cannot be combined." } },
};
if (mode !== "usage") writeFileSync(report, "hello");
const document = JSON.stringify({ schema: "wgsextract.result/1", command: "fake", warnings: [], metrics: { reads: 10 }, ...documents[mode] }, null, 2);
if (options[0] === "--result-file") writeFileSync(options[1], document);
console.log(options[0] === "--json" ? document : "a human summary of " + mode);
process.exit(documents[mode].exit_code);
`;

async function withResultBundle(t, resultArguments = ["--result-file", "{{resultDocumentPath}}"]) {
  const root = await mkdtemp(path.join(tmpdir(), "gfc-result-document-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const bundleRoot = path.join(root, "Bundle");
  await mkdir(path.join(bundleRoot, "scripts"), { recursive: true });
  await writeFile(path.join(bundleRoot, "scripts", "cli.mjs"), fakeCli, "utf8");
  await writeFile(path.join(bundleRoot, "manifest.json"), JSON.stringify({
    id: "dev.wgsextract-gui.result-document-fixture",
    displayName: "Result Document Fixture",
    summary: "Fixture bundle.",
    resultDocument: { format: "wgsextract.result/1", executables: [process.execPath], arguments: resultArguments },
    pages: [{
      id: "main",
      title: "Main",
      summary: "Main page.",
      sections: [{
        id: "inputs",
        controls: [{ id: "mode", label: "Mode", kind: "text" }],
        actions: [{ id: "run", title: "Run", command: { executable: process.execPath, arguments: ["{{bundleRoot}}/scripts/cli.mjs", "{{mode}}"] } }],
      }],
    }],
  }), "utf8");
  return { bundleRoot, workspaceRoot: path.join(root, "Workspace") };
}

function step(mode, extra) {
  return { kind: "action", id: mode, actionID: "run", inputs: { fieldValues: { mode } }, continueOnFailure: true, ...extra };
}

test("bundle tests check the result document instead of the printed output", async (t) => {
  const { bundleRoot, workspaceRoot } = await withResultBundle(t);

  const report = await runBundleTest(bundleRoot, {
    steps: [
      step("ok", { expectedResult: { status: "ok", outputs: ["report"] } }),
      step("ok", { expectedResult: { outputs: ["coverage"] } }),
      step("wrong-size", { expectedResult: { status: "ok", outputs: ["report"] } }),
      step("two-reports", { expectedResult: { outputs: ["report"] } }),
      step("usage", { expectedExitCodes: [2], expectedResult: { status: "failed", errorKind: "usage" } }),
      step("usage", { expectedExitCodes: [2], expectedResult: { errorKind: "missing_dependency" } }),
    ],
  }, { workspaceURL: workspaceRoot });

  assert.deepEqual(report.steps.map((entry) => entry.status), ["passed", "failed", "failed", "failed", "passed", "failed"]);
  const [ok, missingRole, wrongSize, twoReports, usage, wrongKind] = report.steps;
  assert.equal(ok.result.metrics.reads, 10);
  // The human summary still streams; the document is kept in the report, and its file removed.
  assert.match(ok.output, /log: running ok/);
  assert.match(ok.output, /a human summary of ok/);
  assert.doesNotMatch(ok.output, /wgsextract\.result/);
  const documentFile = ok.command.match(/ok --result-file '?(.+result\.json)'?$/)?.[1];
  assert.ok(documentFile);
  assert.equal(existsSync(documentFile), false);
  assert.match(missingRole.error, /no output with role coverage/);
  assert.match(wrongSize.error, /5 bytes on disk but the result recorded 99/);
  assert.match(twoReports.error, /Output report is missing on disk: .*report-2\.txt/);
  assert.equal(usage.exitCode, 2);
  assert.match(wrongKind.error, /Expected result error kind missing_dependency but got usage/);
});

test("a result document printed on stdout is read instead of shown", async (t) => {
  const { bundleRoot, workspaceRoot } = await withResultBundle(t, ["--json"]);

  const report = await runBundleTest(bundleRoot, {
    steps: [step("ok", { expectedResult: { status: "ok", outputs: ["report"] } })],
  }, { workspaceURL: workspaceRoot });

  assert.equal(report.steps[0].status, "passed");
  assert.match(report.steps[0].command, /cli\.mjs'? ok --json$/);
  assert.doesNotMatch(report.steps[0].output, /wgsextract\.result/);
});

test("a dry run shows the command with the result document arguments", async (t) => {
  const { bundleRoot, workspaceRoot } = await withResultBundle(t);

  const report = await runBundleTest(bundleRoot, { steps: [step("ok")] }, { workspaceURL: workspaceRoot, dryRun: true });

  assert.match(report.steps[0].command, /cli\.mjs'? ok --result-file '<result document>'$/);
});

test("failed runs map to a usage or missing-tool notice, from the document or the exit code", () => {
  const document = (error) => parseResultDocument({ schema: "wgsextract.result/1", status: "failed", error }, "wgsextract.result/1");

  assert.deepEqual(resultNotice(document({ kind: "usage", message: "Choose --ref." }), 2, "ignored\n"), { kind: "usage", message: "Choose --ref." });
  assert.deepEqual(
    resultNotice(document({ kind: "missing_dependency", message: "Tool not found: bcftools. Run: pixi install -e default" }), 3, ""),
    { kind: "missing_dependency", message: "Tool not found: bcftools. Run: pixi install -e default" },
  );
  assert.equal(resultNotice(document({ kind: "failure", message: "Input file not found" }), 1, ""), undefined);
  // Argument parsing and wrapper scripts fail before a document is written.
  assert.deepEqual(resultNotice(null, 2, "usage: wgsextract info\nwgsextract info: error: --input is required\n"), {
    kind: "usage",
    message: "wgsextract info: error: --input is required",
  });
  assert.deepEqual(resultNotice(null, 127, "Error: Pixi was not found. Run the WGS Extract setup action again.\n"), {
    kind: "missing_dependency",
    message: "Error: Pixi was not found. Run the WGS Extract setup action again.",
  });
  assert.equal(resultNotice(null, 1, "boom\n"), undefined);
  assert.equal(parseResultDocument({ schema: "other/1", status: "ok" }, "wgsextract.result/1"), null);
});

test("the result summary lists outputs to open, warnings and metrics; the notice offers setup", () => {
  Object.assign(state, createInitialState(), { labels: {} });
  const document = parseResultDocument({
    schema: "wgsextract.result/1",
    status: "ok",
    manifest: "/out/sample.manifest.json",
    metrics: { mean_depth: 0.123456789, sex: "<unknown>" },
    warnings: [{ code: "low_coverage", message: "Coverage is below 1x." }],
    outputs: [
      { path: "/out/sample.cram", role: "alignment", kind: "cram", size_bytes: 2048 },
      { path: "/out/gone.bai", role: "alignment_index", exists: false },
    ],
  }, "wgsextract.result/1");

  const summary = renderResultSummary(document);

  assert.match(summary, /low_coverage<\/code> Coverage is below 1x\./);
  assert.match(summary, /<dt>mean_depth<\/dt><dd>0\.123457<\/dd>/);
  assert.match(summary, /&lt;unknown&gt;/);
  assert.match(summary, /2\.0 KB/);
  assert.match(summary, /data-result-open="\/out\/sample\.cram"/);
  assert.match(summary, /data-result-reveal="\/out\/sample\.manifest\.json"/);
  // A listed output that no longer exists can't be opened.
  assert.doesNotMatch(summary, /data-result-open="\/out\/gone\.bai"/);
  assert.doesNotMatch(summary, /data-result-card/);

  assert.match(renderResultNotice({ kind: "missing_dependency", message: "Run: pixi install -e gpu" }), /data-result-open-setup[\s\S]*Open Setup/);
  assert.doesNotMatch(renderResultNotice({ kind: "usage", message: "Choose --ref." }), /data-result-open-setup/);
});

test("#1090: genomic results open in the built-in viewer at their own locus, never a remote lookup", () => {
  Object.assign(state, createInitialState(), {
    manifest: { id: "wgs-extract" },
    labels: { viewerStrings: { showInViewer: "Afficher dans le visualiseur" } },
  });
  const document = parseResultDocument({
    schema: "wgsextract.result/1", status: "ok",
    outputs: [
      { path: '/genomes/region&"one.bam', metrics: { locus: "chr1:200-400" } },
      { path: "/genomes/calls.vcf.gz", metrics: { locus: "chr1:250-250" } },
      { path: "/genomes/missing.cram", exists: false },
      { path: "/genomes/region.bam.bai" },
    ],
  }, "wgsextract.result/1");
  const summary = renderResultSummary(document);
  assert.match(summary, /data-result-viewer="\/genomes\/region&amp;&quot;one.bam" data-viewer-locus="chr1:200-400">Afficher dans le visualiseur/);
  assert.match(summary, /data-result-viewer="\/genomes\/calls.vcf.gz" data-viewer-locus="chr1:250-250"/);
  assert.doesNotMatch(summary, /data-result-viewer="\/genomes\/(?:missing.cram|region.bam.bai)"/);
  for (const locus of ["rs429358", "https://example.org", "chr1:0-100", 'chr1:2-1"><img>']) {
    document.outputs[0].metrics.locus = locus;
    assert.match(renderResultSummary(document), /region&amp;&quot;one.bam" data-viewer-locus=""/);
  }
  state.manifest.id = "git-helper";
  assert.doesNotMatch(renderResultSummary(document), /data-result-viewer/);
});

test("a run's result card gets a View Card button and is the document's card output", () => {
  Object.assign(state, createInitialState(), { labels: {} });
  const document = parseResultDocument({
    schema: "wgsextract.result/1",
    status: "ok",
    outputs: [
      { path: "/out/sample_info.card.html", role: "result_card", kind: "html", size_bytes: 5000 },
      { path: "/out/sample.tsv", role: "table" },
    ],
  }, "wgsextract.result/1");

  const summary = renderResultSummary(document);

  assert.match(summary, /data-result-card="\/out\/sample_info\.card\.html"[^>]*>View Card</);
  assert.equal((summary.match(/data-result-card=/g) ?? []).length, 1);
  assert.equal(resultCardOutput(document)?.path, "/out/sample_info.card.html");
  const gone = parseResultDocument({ schema: "wgsextract.result/1", status: "ok", outputs: [{ path: "/out/x.card.html", role: "result_card", exists: false }] }, "wgsextract.result/1");
  assert.equal(resultCardOutput(gone), undefined);
});

test("an analysis action's report is the first role in its openOutput the run wrote; cards are asked for only by resultCard", () => {
  const document = parseResultDocument({
    schema: "wgsextract.result/1",
    status: "ok",
    outputs: [
      { path: "/out/r1_fastqc.html", role: "fastqc_report_html" },
      { path: "/out/r2_fastqc.html", role: "fastqc_report_html" },
      { path: "/out/old_multiqc.html", role: "multiqc_report", exists: false },
    ],
  }, "wgsextract.result/1");
  assert.equal(preferredOutput(document, ["multiqc_report", "fastqc_report_html"])?.path, "/out/r1_fastqc.html");
  document.outputs.push({ ...document.outputs[0], path: "/out/multiqc.html", role: "multiqc_report", exists: true });
  assert.equal(preferredOutput(document, ["multiqc_report", "fastqc_report_html"])?.path, "/out/multiqc.html");
  assert.equal(preferredOutput(document, undefined), undefined);

  const spec = { format: "wgsextract.result/1", executables: ["tool"], arguments: ["--result-file", "{{resultDocumentPath}}"], cardArguments: ["--card"] };
  const command = { executable: "tool", arguments: ["info"] };
  assert.deepEqual(commandWithResultArguments(command, spec, "/tmp/r.json").optionalArguments, [["--result-file", "/tmp/r.json"]]);
  assert.deepEqual(commandWithResultArguments(command, spec, "/tmp/r.json", true).optionalArguments, [["--result-file", "/tmp/r.json", "--card"]]);
});
