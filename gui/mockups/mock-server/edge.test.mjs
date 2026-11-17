import assert from "node:assert/strict";
import test from "node:test";
import { loadCore, loadDemoData } from "./server.mjs";

const M = loadCore();
const config = loadDemoData(M);

function setup(machineKey = "mac-pro") {
  const data = new Map();
  const options = { storage: { get: k => data.get(k), set: (k, v) => data.set(k, v) }, realNow: () => 1794900000000, machineKey };
  const mock = M.createMock(config, options);
  mock.clock.setSpeed(0);
  const request = (method, path, body) => mock.handle(method, path, body);
  return { mock, request, options };
}

test("disk-full starts no job; reservations, alternate volumes and partial resume agree", () => {
  const { mock, request } = setup();
  request("POST", "/v1/mock/scenario", { name: "empty-jobs" });
  request("POST", "/v1/mock/scenario", { name: "disk-full", disk: "system" });
  const params = { install: "hg19", _mock: { gb: 1 } };
  assert.equal(request("POST", "/v1/ops/ref/library", params).body.error.code, "insufficient_space");
  assert.equal(request("GET", "/v1/jobs").body.jobs.length, 0);
  const elsewhere = request("POST", "/v1/ops/ref/library", { ...params, _mock: { gb: 1, targetDisk: "genomes" } });
  assert.equal(elsewhere.status, 202);
  assert.equal(elsewhere.body.ui.outputs[0].disk, "Genomes");
  request("DELETE", `/v1/jobs/${elsewhere.body.id}`);
  request("POST", "/v1/mock/scenario", { name: "restore-space" });
  const first = request("POST", "/v1/ops/ref/library", { install: "hg38", _mock: { gb: 300 } });
  assert.equal(first.status, 202);
  assert.equal(request("POST", "/v1/ops/ref/library", { install: "hg19", _mock: { gb: 300 } }).body.error.code, "insufficient_space");
  request("POST", "/v1/clock", { advance_s: 200 });
  request("DELETE", `/v1/jobs/${first.body.id}`);
  const resumed = request("POST", `/v1/jobs/${first.body.id}/rerun`);
  assert.equal(resumed.status, 202);
  assert.equal(resumed.body.ui.resumed_bytes, 200e9);
  assert.equal(mock.doc().jobs.filter(j => j.status === "running").length, 1);
});

for (const machineKey of ["mac-pro", "windows"]) {
  test(`removing a secondary merge input or its owner is atomic on ${machineKey} until cancellation`, () => {
    const { request } = setup(machineKey);
    const files = request("POST", "/v1/mock/files", { template: "seqcom-30x-cram", count: 2 }).body.files;
    const inputs = files.map(f => machineKey === "windows" ? f.path.replaceAll("/", "\\").toUpperCase() : f.path);
    const job = request("POST", "/v1/ops/bam/merge", { inputs }).body;
    assert.equal(job.status, "queued");
    const owner = request("GET", "/v1/library").body.people.find(p => p.files.some(f => f.id === files[1].id));
    assert.equal(request("DELETE", `/v1/library/files/${files[1].id}`).body.error.code, "in_use");
    assert.equal(request("DELETE", `/v1/library/people/${owner.id}`).body.error.code, "in_use");
    assert.equal(request("PATCH", `/v1/library/people/${owner.id}`, { removed: true }).body.error.code, "in_use");
    assert.equal(request("DELETE", "/v1/mock/files").body.error.code, "in_use");
    assert.equal(request("GET", "/v1/mock/files").body.files[files[1].id].name, files[1].name);
    request("DELETE", `/v1/jobs/${job.id}`);
    assert.equal(request("DELETE", `/v1/library/files/${files[1].id}`).status, 200);
    const seed = request("GET", "/v1/library").body.people.find(p => p.id === "alex").files.find(f => f.id === "alex");
    const active = request("POST", "/v1/ops/bam/index", { input: seed.path.replace(/^~/, "/Users/you") });
    assert.equal(active.status, 202);
    const assigned = request("POST", "/v1/library/files/alex/assign", { name: "Moved Input" }).body;
    const moved = assigned.people.find(p => p.files.some(f => f.id === "alex"));
    assert.equal(request("DELETE", `/v1/library/people/${moved.id}`).body.error.code, "in_use", "Reassignment does not bypass the guard when display paths contain ~");
  });
}

test("queued cancellation has a reason and zero duration; a failed retry can succeed", () => {
  const { request } = setup();
  const queued = request("GET", "/v1/jobs?detail=1").body.jobs.find(j => j.status === "queued");
  assert.equal(request("POST", `/v1/jobs/${queued.id}/rerun`).body.error.code, "active_job");
  request("DELETE", `/v1/jobs/${queued.id}`);
  assert.equal(request("GET", `/v1/jobs/${queued.id}`).body.ui.took_s, 0);
  assert.equal(request("GET", `/v1/jobs/${queued.id}?full=1`).body.result.provenance.elapsed_s, 0);
  assert.match(request("GET", `/v1/jobs/${queued.id}/log`).body, /Cancelled/);
  const job = request("POST", "/v1/ops/ref/library", { install: "hg19", _mock: { fail: { message: "Bad checksum" } } }).body;
  request("POST", "/v1/clock", { advance_s: 10 });
  assert.match(request("GET", `/v1/jobs/${job.id}`).body.error.message, /Bad checksum/);
  const retry = request("POST", `/v1/jobs/${job.id}/rerun`).body;
  request("POST", "/v1/clock", { advance_s: 10 });
  assert.equal(request("GET", `/v1/jobs/${retry.id}`).body.status, "succeeded");
});

test("empty review scenarios and mock settings survive a server reload", () => {
  const { request, options } = setup();
  request("POST", "/v1/mock/scenario", { name: "empty-library" });
  assert.equal(request("GET", "/v1/jobs").body.jobs.length, 0);
  const lib = request("GET", "/v1/library").body;
  assert(lib.people.every(p => p.removed && !p.files.length));
  assert.equal(lib.unassigned.length, 0);
  const person = request("POST", "/v1/library/people", { name: "No Files" }).body;
  assert.deepEqual(person.files, []);
  request("PATCH", "/v1/mock/settings", { phone_access: false, allowed_paths: ["~/Desktop"] });
  request("POST", "/v1/mock/approval", { action: "ask" });
  const reloaded = M.createMock(config, options);
  const settings = reloaded.handle("GET", "/v1/mock/settings").body;
  assert.equal(settings.phone_access, false);
  assert.deepEqual(settings.allowed_paths, ["~/Desktop"]);
  assert.equal(settings.requests.length, 1);
  assert(reloaded.handle("GET", "/v1/library").body.people.some(p => p.id === person.id && !p.files.length));
});

test("reference removal is guarded, and an explicit redownload restores availability", () => {
  const { request } = setup();
  assert.equal(request("DELETE", "/v1/references/hs38").body.error.code, "in_use");
  const download = request("POST", "/v1/ops/ref/library", { install: "clinvar" });
  assert.equal(download.status, 202);
  assert.equal(request("POST", "/v1/mock/approval", { action: "allow", resource: "clinvar" }).body.error.code, "in_use");
  assert.equal(request("GET", "/v1/mock/settings").body.requests.length, 0);
  request("DELETE", `/v1/jobs/${download.body.id}`);
  assert.equal(request("POST", "/v1/mock/approval", { action: "allow", resource: "clinvar" }).status, 200);
  request("POST", "/v1/mock/scenario", { name: "empty-jobs" });
  const input = request("POST", "/v1/mock/files", { template: "seqcom-30x-cram", count: 1 }).body.files[0].path;
  assert.equal(request("POST", "/v1/ops/bam/to-bam", { input }).status, 202);
  assert.equal(request("DELETE", "/v1/references/hs38").body.error.code, "in_use", "CRAM reference is protected even without an explicit ref parameter");
  request("POST", "/v1/mock/scenario", { name: "empty-jobs" });
  assert.equal(request("DELETE", "/v1/references/hs38").status, 200);
  assert.equal(request("GET", "/v1/references").body.references.find(r => r.id === "hs38").installed, false);
  assert.equal(request("POST", "/v1/ops/ref/library", { install: "hs38" }).status, 202);
  request("POST", "/v1/clock", { advance_s: 10 });
  assert.equal(request("GET", "/v1/references").body.references.find(r => r.id === "hs38").installed, true);
});

test("the analysis concurrency setting changes actual slots and the queue forecast", () => {
  const { request } = setup();
  request("PATCH", "/v1/mock/settings", { max_concurrent_jobs: 2 });
  const running = request("GET", "/v1/jobs?detail=1").body.jobs.filter(j => j.status === "running");
  assert.equal(running.length, 2);
  const short = running.find(j => j.op === "lineage/y-str");
  const input = request("GET", "/v1/library").body.people.find(p => p.id === "gordon").files[0].path;
  const job = request("POST", "/v1/ops/bam/index", { input }).body;
  assert.equal(job.status, "queued");
  assert.equal(job.ui.waiting.limit, 2);
  assert.equal(job.ui.waiting.behind, short.id);
  assert(job.ui.waiting.starts_in_s < 300);
});

test("disk-full during an automatic post-fix rerun stays actionable instead of breaking ticks", () => {
  const { request } = setup();
  request("POST", "/v1/mock/scenario", { name: "empty-jobs" });
  const input = request("GET", "/v1/library").body.people.find(p => p.id === "alex").files[0].path;
  const src = request("POST", "/v1/ops/vcf/snp", { input, _mock: { fail: true } }).body;
  request("POST", "/v1/clock", { advance_s: 300 });
  request("POST", "/v1/mock/scenario", { name: "disk-full", disk: "system" });
  const fix = request("POST", "/v1/ops/bam/index", { input, _mock: { thenRerun: src.id } });
  assert.equal(fix.status, 202);
  assert.equal(request("POST", "/v1/clock", { advance_s: 400 }).status, 200);
  assert.equal(request("GET", `/v1/jobs/${fix.body.id}`).body.status, "succeeded");
  assert.equal(request("GET", `/v1/jobs/${fix.body.id}`).body.ui.then_rerun, null);
  assert.match(request("GET", `/v1/jobs/${src.id}`).body.error.message, /could not restart.*Not enough space/);
});
