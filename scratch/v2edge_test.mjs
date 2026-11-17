import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";

const OUT = process.env.OUT;
assert(OUT, "Set OUT to the worktree out/edge directory");

export default async (page) => {
  // Headless Chromium clamps --window-size below 500px. Override the owned
  // driver's target so the phone checks actually render at 390 CSS pixels.
  let emulation;
  if (process.argv.includes("390")) {
    const args = execFileSync("ps", ["--ppid", String(process.pid), "-o", "args="], { encoding: "utf8" });
    const port = /--remote-debugging-port=(\d+)/.exec(args)?.[1];
    assert(port, "Find this driver's Chromium debugger");
    const target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page");
    emulation = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise(resolve => emulation.addEventListener("open", resolve, { once: true }));
    const done = new Promise(resolve => emulation.addEventListener("message", e => {
      if (JSON.parse(e.data).id === 1) resolve();
    }));
    emulation.send(JSON.stringify({ id: 1, method: "Emulation.setDeviceMetricsOverride", params: { width: 390, height: 844, deviceScaleFactor: 1, mobile: false } }));
    await done;
  }
  const api = (method, path, body) => page.eval(`WGSM.v2api.request(${JSON.stringify(method)}, ${JSON.stringify(path)}, ${JSON.stringify(body ?? null)})`);
  const jobs = async () => (await api("GET", "/v1/jobs?detail=1&full=1")).body.jobs;
  const reset = async (path = "jobs.html") => {
    await page.eval("WGSM.v2api.reset()");
    await page.goto(path);
    await page.eval("WGSM.v2clock.setSpeed(0)");
  };
  await page.goto("jobs.html?api=browser&theme=light");
  await page.eval("localStorage.clear()");
  await page.goto("jobs.html?api=browser&theme=light");
  const size = await page.eval("innerWidth < 600 ? 'phone' : 'desktop'");
  if (size === "phone") assert.equal(await page.eval("innerWidth"), 390);
  const shot = (name) => page.shot(`${OUT}/edge-${name}-${size}.png`);
  await reset();

  // Heavy jobs explain their queue; cancelling before start has no bogus duration.
  await page.waitFor("document.querySelector('[data-section=queued]').textContent.includes('Queue position 1')");
  assert.match(await page.text("[data-section=queued]"), /one analysis job runs at a time/);
  const queued = (await jobs()).find(j => j.status === "queued");
  await page.click(`[data-section=queued] [data-job-id="${queued.id}"] button`, "Cancel");
  await page.waitFor(`document.querySelector('[data-section=done] [data-job-id="${queued.id}"]')`);
  assert.match(await page.text(`[data-job-id="${queued.id}"]`), /Cancelled/);
  assert(!/years|months|NaN/.test(await page.text(`[data-job-id="${queued.id}"]`)));
  await page.click(`[data-job-id="${queued.id}"] summary`, "View log");
  await page.waitFor(`document.querySelector('[data-job-id="${queued.id}"] pre').textContent.includes('Cancelled')`);
  await shot("cancelled");
  await page.click(`[data-job-id="${queued.id}"] button`, "Run again");
  assert((await jobs()).some(j => j.ui.rerun_of === queued.id));

  // A real failed download has its reason, a log, and a working retry.
  const failed = (await api("POST", "/v1/ops/ref/library", { install: "hg19", _mock: { fail: { message: "Checksum mismatch in downloaded data." } } })).body;
  await api("POST", "/v1/clock", { advance_s: 10 });
  await page.waitFor(`document.querySelector('[data-section=needs-you] [data-job-id="${failed.id}"]')`);
  assert.match(await page.text(`[data-job-id="${failed.id}"]`), /Checksum mismatch/);
  await page.click(`[data-job-id="${failed.id}"] summary`, "View log");
  await page.waitFor(`document.querySelector('[data-job-id="${failed.id}"] pre').textContent.includes('Checksum mismatch')`);
  await shot("failed");
  await page.click(`[data-job-id="${failed.id}"] button`, "Run again");
  const retry = (await jobs()).find(j => j.ui.rerun_of === failed.id);
  assert(retry && !retry.params._mock.fail, "Retry does not repeat an injected failure");
  await api("POST", "/v1/clock", { advance_s: 10 });
  assert.equal((await api("GET", `/v1/jobs/${retry.id}`)).body.status, "succeeded");
  assert.equal((await api("POST", `/v1/jobs/${queued.id}/rerun`)).status, 202);

  // Removing a person used by running/queued work is blocked without local mutation.
  await page.goto("person-gordon.html");
  await page.click('[data-open="#person-menu"]');
  await page.click("[data-v2-remove]");
  await page.waitFor("document.querySelector('[data-edge-error=in_use]')");
  assert(await page.eval("document.querySelector('#person-menu').open"));
  assert.equal((await api("GET", "/v1/library")).body.people.find(p => p.id === "gordon").removed, false);
  assert.match(await page.text("[data-edge-error]"), /Cancel.*Jobs/);
  await shot("person-in-use");
  await page.click("#person-menu [data-close]", "Done");

  // Generated file removal and bulk cleanup use the same guard, including second inputs.
  await reset("add.html");
  const made = (await api("POST", "/v1/mock/files", { template: "seqcom-30x-cram", count: 2 })).body.files;
  const active = (await api("POST", "/v1/ops/bam/merge", { inputs: made.map(f => f.path) })).body;
  assert.equal((await api("DELETE", `/v1/library/files/${made[1].id}`)).body.error.code, "in_use");
  assert.equal((await api("DELETE", "/v1/mock/files")).body.error.code, "in_use");
  await api("DELETE", `/v1/jobs/${active.id}`);
  assert.equal((await api("DELETE", `/v1/library/files/${made[1].id}`)).status, 200);

  // Empty lanes and library, then a person who has no files yet.
  await api("POST", "/v1/mock/scenario", { name: "empty-jobs" });
  await page.goto("jobs.html");
  await page.waitFor("document.querySelector('[data-section=running]').textContent.includes('Nothing is running')");
  assert.match(await page.text("[data-section=downloads]"), /No downloads/);
  assert.match(await page.text("[data-section=done]"), /Nothing has finished/);
  await shot("empty-jobs");
  await api("POST", "/v1/mock/scenario", { name: "empty-library" });
  await page.goto("home.html");
  await page.waitFor("document.querySelector('[data-edge-empty]') && !document.querySelector('[data-edge-empty]').hidden");
  assert.match(await page.text(".v2-head__sub"), /0 people, 0 files/);
  await shot("empty-library");
  await page.goto("add.html");
  await page.click('[data-edge="choose-files"]');
  await page.waitFor("document.querySelector('#v2-generate[open]')");
  await page.click("#v2-generate [data-close]");
  await page.click('[data-edge="create-person"]');
  await page.set('[aria-label="Person name"]', "Empty Person");
  await page.click("#v2-edge-dialog button", "Add person");
  await page.waitFor("location.pathname.endsWith('person-added.html') && document.querySelector('.v2-empty')");
  assert.match(await page.text("[data-annot-id=files]"), /No files yet/);
  await shot("empty-person");
  await page.goto("home.html");
  await page.waitFor("document.querySelector('[data-v2-gen-group]')");
  assert.match(await page.text("[data-v2-gen-group]"), /No files yet/);
  await page.waitFor("document.querySelector('[data-annot-id=unassigned]').hidden");

  // Jobs and downloads cannot overspend free disk. Recovery picks a real mock volume.
  await reset("convert.html");
  await api("POST", "/v1/mock/scenario", { name: "disk-full" });
  const n = (await jobs()).length;
  await page.click('[data-edge="convert"]');
  await page.waitFor("document.querySelector('[data-edge-error=insufficient_space]')");
  assert.equal((await jobs()).length, n, "Disk-full starts no job");
  await shot("disk-full");
  await reset("tool-convert-format.html");
  await api("POST", "/v1/mock/scenario", { name: "disk-full" });
  const beforeTool = (await jobs()).length;
  await page.click("[data-v2-go]");
  await page.waitFor("document.querySelector('[data-edge-error=insufficient_space]')");
  await page.wait(1100);
  assert(await page.eval("!!document.querySelector('[data-edge-error=insufficient_space]')"), "Tool refusals survive run-bar and clock refreshes");
  assert.equal((await jobs()).length, beforeTool);
  await api("POST", "/v1/mock/scenario", { name: "restore-space" });
  await api("POST", "/v1/mock/scenario", { name: "disk-full", disk: "system" });
  await page.goto("references.html");
  await page.click('[data-edge-ref="T2Tv20"]');
  await page.waitFor("document.querySelector('[data-edge-error=insufficient_space]')");
  await page.click("[data-edge-error] button", "Pick another volume");
  await page.waitFor("document.querySelector('#v2-edge-dialog [aria-label=Volume]')");
  await page.set('#v2-edge-dialog [aria-label="Volume"]', "genomes");
  await page.click("[data-edge-choose]");
  await page.waitFor("WGSM.v2api.request('GET','/v1/jobs?detail=1').then(r=>r.body.jobs.some(j=>j.op==='ref/library' && j.ui.outputs[0].disk==='Genomes'))");
  assert.equal(await page.eval("!!document.querySelector('[data-edge-error]')"), false);
  await shot("alternate-volume");

  // Missing CRAM reference is offered explicitly, never fetched by Start.
  await reset("tool-convert-format.html");
  await api("POST", "/v1/mock/scenario", { name: "empty-jobs" });
  assert.equal((await api("DELETE", "/v1/references/hs38")).status, 200);
  await page.eval("(() => { WGSM.v2state.pick('reginald'); WGSM.v2tools.refresh(); })()");
  await page.set('[data-v2-key="conversion"]', "bam/to-bam");
  await page.click("[data-v2-go]");
  await page.waitFor("document.querySelector('#v2-edge-dialog[open]')");
  assert.match(await page.text("#v2-edge-dialog"), /Missing reference genome/);
  assert.equal((await jobs()).length, 0, "No automatic download or analysis");
  await shot("missing-reference");
  await page.click("#v2-edge-dialog button", "Download hs38");
  assert((await jobs()).some(j => j.op === "ref/library"));
  await api("POST", "/v1/clock", { advance_s: 10 });
  assert.equal((await api("GET", "/v1/references")).body.references.find(r => r.id === "hs38").installed, true);
  await page.click("[data-v2-go]");
  await page.waitFor("WGSM.v2api.request('GET','/v1/jobs').then(r=>r.body.jobs.some(j=>j.op==='bam/to-bam'))");

  // Former toast-only actions now submit jobs, browse mock files, and save settings.
  await reset("add.html");
  const beforeAdd = (await jobs()).length;
  await page.click('[data-edge="add-preview"]');
  await page.waitFor("document.querySelector('[data-edge-error=usage]')");
  assert.equal((await jobs()).length, beforeAdd, "An unnamed new person starts no work");
  await page.set('[aria-label="Name of the new person"]', "Dana Example");
  await page.set("#who-bam", "Gordon");
  await page.click('[data-annot-id="found-bam"] input[type=checkbox]');
  await page.click('[data-edge="add-preview"]');
  await page.waitFor("document.querySelector('[data-edge=add-preview]').disabled");
  const added = (await api("GET", "/v1/library")).body.people;
  assert(added.find(p => p.name === "Dana Example").files.some(f => f.id === "fastq"));
  assert(added.find(p => p.id === "gordon").files.some(f => f.id === "alex"));
  assert.equal((await jobs()).length, beforeAdd, "Leaving fix unchecked adds files without running work");
  await reset("add.html");
  await page.set("#who-bam", "Gordon");
  await page.set("#who-fq", "Helen");
  if (!await page.eval("document.querySelector('[data-annot-id=found-bam] input[type=checkbox]').checked")) {
    await page.click('[data-annot-id="found-bam"] input[type=checkbox]');
  }
  await page.click('[data-edge="add-preview"]');
  await page.waitFor("WGSM.v2api.request('GET','/v1/jobs?detail=1').then(r=>r.body.jobs.some(j=>j.op==='bam/index' && j.ui.person==='gordon'))");
  assert((await api("GET", "/v1/library")).body.people.find(p => p.id === "helen").files.some(f => f.id === "fastq"));
  await reset("convert.html");
  await page.click('[name=to][value="bam/unalign"]');
  await page.set('[aria-label="Threads"]', "4");
  await page.click('[data-edge="convert"]');
  assert((await jobs()).some(j => j.op === "bam/unalign" && j.params.threads === 4));
  await reset("dna-test.html");
  await page.set('[aria-label="MyHeritage chip version"]', "myheritage_gsa_2025");
  await page.set('[aria-label="Minimum genotype quality"]', "15");
  await page.click('[aria-label="Explain blanks"]');
  await page.click('[data-edge="dna"]');
  assert((await jobs()).some(j => j.op === "microarray" && j.ui.person === "reginald" && j.params.formats.includes("myheritage_gsa_2025") && j.params.min_gq === 15 && j.params.call_reasons));
  await page.goto("result.html");
  await page.click('[data-edge="rerun-result"]');
  assert((await jobs()).filter(j => j.op === "microarray" && j.ui.person === "helen").length >= 2);
  await page.click('[data-edge="folder"]');
  await page.waitFor("document.querySelector('#v2-edge-dialog[open]')");
  assert.match(await page.text("#v2-edge-dialog"), /helen-mcdonald/);
  await page.click("#v2-edge-dialog [data-close]");
  await page.goto("settings.html");
  await page.click('[data-edge="workspace"]');
  await page.waitFor("document.querySelector('#v2-edge-dialog [aria-label=Volume]')");
  await page.set('#v2-edge-dialog [aria-label="Volume"]', "genomes");
  await page.click("[data-edge-choose]");
  await page.waitFor("document.querySelector('[data-edge=workspace]').parentElement.textContent.includes('/Volumes/Genomes/wgs-extract')");
  await page.click('[data-edge="allow-folder"]');
  await page.set('#v2-edge-dialog [aria-label="Folder path"]', "~/Desktop");
  await page.click("#v2-edge-dialog button", "Allow folder");
  await page.waitFor("document.querySelector('[data-edge-folders]').textContent.includes('~/Desktop')");
  await page.click("[data-edge-folders] .v2-row:last-child button", "Remove");
  await page.waitFor("!document.querySelector('[data-edge-folders]').textContent.includes('~/Desktop')");
  await page.click('[data-edge="check"]');
  await page.waitFor("document.querySelector('#v2-edge-dialog[open]')");
  assert.match(await page.text("#v2-edge-dialog"), /does not install/);
  assert((await jobs()).some(j => j.op === "deps/check"));
  await page.click("#v2-edge-dialog [data-close]");
  await page.click('[data-edge="report"]');
  await page.waitFor("document.querySelector('#v2-edge-dialog[open]')");
  assert.match(await page.text("#v2-edge-dialog"), /No names, genome data or file paths/);
  assert(!/McDonald|\/Volumes/.test(await page.text("#v2-edge-dialog pre")));
  await shot("report");
  await page.click("#v2-edge-dialog [data-close]");
  await page.goto("setup.html");
  await page.click('[data-edge="example"]');
  assert((await jobs()).some(j => j.op === "ref/test-genome"));
  await page.goto("states.html");
  await page.click('[data-edge="ask"]');
  await page.click('[data-edge="decline"]');
  assert.deepEqual((await api("GET", "/v1/mock/settings")).body.requests.map(r => r.action), ["ask", "decline"]);
  await page.goto("phone.html");
  await page.click('[data-v2-set="device-iphone-removed"]');
  await page.waitFor("document.querySelector('[data-annot-id=device-iphone]').hidden");
  assert((await api("GET", "/v1/mock/flags")).body.flags.includes("device-iphone-removed"));
  await page.click('[aria-label="Phone access"]');
  await page.waitFor("document.querySelector('[data-annot-id=pairing]').hidden");
  await page.goto("phone.html");
  await page.waitFor("document.querySelector('[data-annot-id=pairing]').hidden");
  await page.click('[data-open="#access-log"]');
  assert.match(await page.text("#access-log"), /decline.*clinvar/);
  await shot("access-log");
  assert.equal(page.errors.length, 0, page.errors.join("\n"));
  if (emulation) emulation.close();
  console.log(`edge cases and formerly dead controls: ${size} passed`);
};
