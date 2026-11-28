import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { once } from "node:events";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

const { createProcessManager } = await import("../dist/web/src/server/process-runner.js");
const { createJobServer } = await import("../dist/web/src/server/job-server.js");
const { runActionPreferringServer } = await import("../dist/web/src/server/job-server-action.js");
const { startJob } = await import("../dist/web/src/server/job-server-client.js");

// A job server speaking the protocol in devdocs/job-server.md. `slow` runs until cancelled;
// `quick` finishes at once and logs the parameters it received, `noisy` the same with a 2 MiB log;
// anything else is refused. With --parent-pid PID it writes its own PID to server.pid and
// exits when PID does; with --refuse it prints an error line instead of starting.
const fakeServer = String.raw`
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { appendFileSync, writeFileSync } from "node:fs";
const parentPid = process.argv.includes("--parent-pid") ? Number(process.argv[process.argv.indexOf("--parent-pid") + 1]) : undefined;
if (process.argv.includes("--refuse")) {
  console.log(JSON.stringify({ error: { message: "Another server is using the state folder" } }));
  process.exit(1);
}
if (parentPid !== undefined) {
  const watch = () => { try { process.kill(parentPid, 0); } catch { process.exit(1); } };
  watch();
  writeFileSync("server.pid", String(process.pid));
  setInterval(watch, 100);
}
const token = randomUUID();
const jobs = new Map();
const watchers = new Map();
function send(job, type, data) {
  job.events.push({ type, data });
  for (const response of watchers.get(job.id) ?? []) {
    response.write("event: " + type + "\ndata: " + JSON.stringify(data) + "\n\n");
    if (type === "status" && ["succeeded", "failed", "cancelled"].includes(data.status)) response.end();
  }
}
function finish(job, status, exitCode) {
  Object.assign(job, { status, exit_code: exitCode });
  // Like wgsextract serve, the record carries the run's result document without its schema, empty fields left out.
  if (status === "succeeded") job.result = { status: "ok", outputs: [{ path: "/data/sample.bam.cov.csv", role: "coverage", size_bytes: 12 }] };
  send(job, "status", { status });
}
const server = createServer(async (request, response) => {
  if (request.headers.authorization !== "Bearer " + token) {
    response.writeHead(401).end();
    return;
  }
  let body = "";
  for await (const chunk of request) body += chunk;
  const [, kind, ...rest] = new URL(request.url, "http://x").pathname.replace(/^\/v1/, "").split("/");
  if (request.method === "POST" && kind === "ops") {
    const operation = rest.join("/");
    if (!["slow", "quick", "noisy", "malformed", "missing", "lost", "gateway-error", "unconfirmed", "cancel-recovered"].includes(operation)) {
      response.writeHead(403, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: { code: "path_not_allowed", message: "not allowed" } }));
      return;
    }
    const query = new URL(request.url, "http://x").search;
    const id = request.headers["x-wgse-job-id"] ?? "job_" + (jobs.size + 1);
    const job = { id, status: "running", events: [], log: "params " + body + (query ? " query " + query : "") + "\n" };
    jobs.set(job.id, job);
    appendFileSync("executions", "server\n");
    if (operation === "lost" || operation === "unconfirmed") request.socket.destroy();
    else if (operation === "gateway-error") response.writeHead(502).end();
    else {
      const replies = { malformed: "{", missing: "{}", "cancel-recovered": "{" };
      response.writeHead(202, { "content-type": "application/json" }).end(replies[operation] ?? JSON.stringify(job));
    }
    if (operation === "unconfirmed") job.hideFromLookup = true;
    send(job, "status", { status: "running" });
    send(job, "progress", { stage: "reading", message: "chr1", fraction: 0.5 });
    if (operation === "noisy") job.log = "x".repeat(2 * 1024 * 1024) + "\nlast line\n";
    if (operation !== "slow" && operation !== "cancel-recovered") setTimeout(() => finish(job, "succeeded", 0), 50);
    return;
  }
  if (kind === "jobs" && rest.length === 0) {
    response.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ jobs: [...jobs.values()] }));
    return;
  }
  const job = jobs.get(rest[0]);
  if (kind !== "jobs" || !job || job.hideFromLookup) {
    response.writeHead(404).end();
  } else if (request.method === "DELETE") {
    response.writeHead(202).end();
    setTimeout(() => finish(job, "cancelled", null), 50);
  } else if (rest[1] === "events") {
    response.writeHead(200, { "content-type": "text/event-stream" });
    response.write(": keepalive\n\n");
    watchers.set(job.id, [...(watchers.get(job.id) ?? []), response]);
    for (const event of job.events) response.write("event: " + event.type + "\ndata: " + JSON.stringify(event.data) + "\n\n");
    if (job.status !== "running") response.end();
  } else if (rest[1] === "log") {
    response.writeHead(200).end(job.log);
  } else {
    const { events, ...record } = job;
    response.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(record));
  }
});
server.listen(0, "127.0.0.1", () => {
  console.log("starting up");
  console.log(JSON.stringify({ url: "http://127.0.0.1:" + server.address().port + "/v1/", token, client_job_ids: !process.argv.includes("--server-ids") }));
});
`;

async function withBundle(t) {
  const bundleRoot = await mkdtemp(path.join(tmpdir(), "wgsextract-gui-job-server-"));
  const manager = createProcessManager({ maxOutputBytes: 1_048_576, maxErrorBytes: 65_536 });
  // Windows can't remove the folder while the server still runs in it.
  t.after(async () => {
    await manager.terminateAllProcesses();
    await rm(bundleRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  });
  await writeFile(path.join(bundleRoot, "server.mjs"), fakeServer);
  const startServer = (args = ["{{bundleRoot}}/server.mjs"]) =>
    createJobServer({ command: { executable: process.execPath, arguments: args }, startupTimeoutSeconds: 10 }, bundleRoot, manager.runProcess);
  return { bundleRoot, manager, startServer };
}

test("#1173: a locally launched server cannot publish an external job destination", async (t) => {
  const bundle = await withBundle(t);
  await writeFile(path.join(bundle.bundleRoot, "server.mjs"),
    'console.log(JSON.stringify({url:"https://genomes.example/v1",token:"synthetic-token"}));');
  assert.equal(await bundle.startServer().address(), undefined);
});

test("#1173: job credentials and sample parameters never reach a non-loopback URL", async (t) => {
  t.mock.method(globalThis, "fetch", () => { throw new Error("unexpected network request"); });
  for (const url of ["https://genomes.example/v1", "http://localhost:8080/v1", "http://0.0.0.0:8080/v1"]) {
    const result = await startJob({ url, token: "synthetic-token" }, randomUUID(), "quick", { input: "/private/sample.bam" });
    assert.equal(result.kind, "rejected");
    assert.match(result.reason, /loopback/);
  }
});

test("#1173: redirects cannot forward a local job's sample parameters to another service", async (t) => {
  let unwantedRequests = 0;
  const sink = createServer((_request, response) => {
    unwantedRequests++;
    response.end();
  });
  await new Promise((resolve) => sink.listen(0, "127.0.0.1", resolve));
  const redirect = createServer((_request, response) => response.writeHead(307, {
    location: `http://127.0.0.1:${sink.address().port}/unapproved`,
  }).end());
  await new Promise((resolve) => redirect.listen(0, "127.0.0.1", resolve));
  t.after(async () => {
    for (const server of [redirect, sink]) {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
  });
  const result = await startJob(
    { url: `http://127.0.0.1:${redirect.address().port}/v1`, token: "synthetic-token" },
    randomUUID(), "quick", { input: "/private/sample.bam" },
  );
  assert.equal(result.kind, "indeterminate");
  assert.equal(unwantedRequests, 0);
});

function action(operation, parameters = {}) {
  return {
    id: "coverage",
    title: "Coverage",
    command: { executable: process.execPath, arguments: ["-e", "require('node:fs').appendFileSync('executions', 'direct\\n'); console.log('ran the command')"] },
    serverOperation: { operation, parameters },
  };
}

async function run(bundle, spec, jobServer, signal, resultSpec, onEvent) {
  const events = [];
  const context = { fieldValues: { bam: "/data/sample.bam", ref: "" } };
  const result = await runActionPreferringServer(spec, context, signal, bundle.bundleRoot, bundle.manager.runProcess, jobServer, (event) => {
    events.push(event);
    onEvent?.(event);
  }, resultSpec);
  return { result, events, output: events.filter((event) => event.type === "output").map((event) => event.text).join("") };
}

test("an action with a serverOperation runs as a job, with its progress in the output", async (t) => {
  const bundle = await withBundle(t);
  const { result, events, output } = await run(bundle, action("quick", { input: "{{bam}}", ref: "{{ref}}", bins: true }), bundle.startServer());

  assert.equal(result.exitCode, 0);
  assert.ok(events.some((event) => event.type === "start"));
  assert.equal(events.at(-1).type, "complete");
  assert.match(output, /\[progress\] reading: chr1 \(50%\)/);
  assert.match(output, /\[job\] succeeded/);
  // An empty placeholder leaves its parameter out; non-string values are sent as written.
  assert.match(output, /params \{"input":"\/data\/sample\.bam","bins":true\}/);
  assert.doesNotMatch(output, /ran the command/);
});

test("#1591 a running CLI job server applies a changed network mode to the next job", async (t) => {
  const bundle = await withBundle(t);
  const script = path.join(bundle.bundleRoot, "run-wgsextract.sh");
  await writeFile(script, 'exec "$@"\n');
  await writeFile(path.join(bundle.bundleRoot, "run-wgsextract.ps1"), "$node, $rest = $args\n& $node @rest\nexit $LASTEXITCODE\n");
  let mode = "full";
  const jobServer = createJobServer({
    command: { executable: script, arguments: [process.execPath, "{{bundleRoot}}/server.mjs"] },
    startupTimeoutSeconds: 10,
  }, bundle.bundleRoot, bundle.manager.runProcess, async () => mode);
  const first = await run(bundle, action("quick", {}), jobServer);
  const address = await jobServer.address();
  mode = "low";
  const second = await run(bundle, action("quick", {}), jobServer);

  assert.match(first.output, /"network":"full"/);
  assert.match(second.output, /"network":"low"/);
  assert.equal(await jobServer.address(), address);
});

test("an action with resultCard asks the job server for the job's card; others don't", async (t) => {
  const bundle = await withBundle(t);
  const jobServer = bundle.startServer();
  const plain = await run(bundle, action("quick", { input: "{{bam}}" }), jobServer);
  const carded = await run(bundle, { ...action("quick", { input: "{{bam}}" }), resultCard: true }, jobServer);

  assert.doesNotMatch(plain.output, /query/);
  assert.match(carded.output, /params \{"input":"\/data\/sample\.bam"\} query \?card=1/);
});

test("a job's result document is read from its record, which wgsextract serve sends without its schema", async (t) => {
  const bundle = await withBundle(t);
  const resultSpec = { format: "wgsextract.result/1", executables: [process.execPath] };
  const jobServer = bundle.startServer();

  const declared = await run(bundle, action("quick"), jobServer, undefined, resultSpec);
  const undeclared = await run(bundle, action("quick"), jobServer);

  assert.equal(declared.result.resultDocument.status, "ok");
  assert.deepEqual(declared.result.resultDocument.outputs.map((output) => [output.role, output.path, output.sizeBytes]), [["coverage", "/data/sample.bam.cov.csv", 12]]);
  assert.deepEqual(declared.result.resultDocument.warnings, []);
  assert.equal(declared.events.at(-1).result.resultDocument.status, "ok");
  assert.equal(undeclared.result.resultDocument, undefined);
});

test("a job's result keeps the end of a large log, and streams all of it", async (t) => {
  const bundle = await withBundle(t);
  const { result, output } = await run(bundle, action("noisy"), bundle.startServer());

  assert.equal(result.exitCode, 0);
  assert.ok(output.length > 2 * 1024 * 1024);
  assert.ok(result.stdout.length <= 1_048_576);
  assert.equal(result.stdoutTruncated, true);
  assert.match(result.stdout, /last line\n$/);
});

for (const fault of ["malformed", "missing", "lost", "gateway-error", "unconfirmed"]) {
  test(`#1235: an accepted job runs once when the submission reply is ${fault}`, async (t) => {
    const bundle = await withBundle(t);
    const running = run(bundle, action(fault), bundle.startServer());
    if (fault === "unconfirmed") {
      await assert.rejects(running, (error) => typeof error.jobID === "string" && error.message.includes(error.jobID));
    } else {
      const { result, output } = await running;
      assert.equal(result.exitCode, 0);
      assert.match(output, /\[job\] succeeded/);
    }
    assert.equal(await readFile(path.join(bundle.bundleRoot, "executions"), "utf8"), "server\n");
  });
}

test("#1235: a successful submission with a server-assigned ID still follows the job", async (t) => {
  const bundle = await withBundle(t);
  const { result, output } = await run(bundle, action("quick"), bundle.startServer(["{{bundleRoot}}/server.mjs", "--server-ids"]));
  assert.equal(result.exitCode, 0);
  assert.match(output, /\[job\] succeeded/);
  assert.equal(await readFile(path.join(bundle.bundleRoot, "executions"), "utf8"), "server\n");
});

test("#1235: an older server's lost reply does not report a fictitious recovery ID or rerun the command", async (t) => {
  const bundle = await withBundle(t);
  const jobServer = bundle.startServer(["{{bundleRoot}}/server.mjs", "--server-ids"]);
  await assert.rejects(run(bundle, action("lost"), jobServer), (error) => error.jobID === null);
  assert.equal(await readFile(path.join(bundle.bundleRoot, "executions"), "utf8"), "server\n");
});

test("cancelling a server action cancels its job", async (t) => {
  const bundle = await withBundle(t);
  const jobServer = bundle.startServer();
  const controller = new AbortController();
  const running = run(bundle, action("slow"), jobServer, controller.signal, undefined, (event) => {
    if (event.type === "start") controller.abort();
  });

  await assert.rejects(running, /Process cancelled\./);
  const address = await jobServer.address();
  const { jobs } = await (await fetch(`${address.url}/jobs`, { headers: { authorization: `Bearer ${address.token}` } })).json();
  assert.deepEqual(jobs.map((job) => job.status), ["cancelled"]);
});

test("#1235: a recovered job is cancelled even when the action stream has already closed", async (t) => {
  const bundle = await withBundle(t);
  const jobServer = bundle.startServer();
  const controller = new AbortController();
  const fetchJob = globalThis.fetch;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    const response = await fetchJob(url, options);
    if (options?.method === "POST") controller.abort();
    return response;
  });
  await assert.rejects(run(bundle, action("cancel-recovered"), jobServer, controller.signal, undefined, (event) => {
    assert.notEqual(event.type, "start");
  }), /Process cancelled/);
  const address = await jobServer.address();
  const { jobs } = await (await fetchJob(`${address.url}/jobs`, { headers: { authorization: `Bearer ${address.token}` } })).json();
  assert.deepEqual(jobs.map((job) => job.status), ["cancelled"]);
});

test("an action runs its command when the job server refuses the job", async (t) => {
  const bundle = await withBundle(t);
  const { result, output } = await run(bundle, action("refused"), bundle.startServer());

  assert.equal(result.exitCode, 0);
  assert.match(output, /ran the command/);
});

test("an action runs its command when the job server can't start", async (t) => {
  const bundle = await withBundle(t);
  const { result, output } = await run(bundle, action("quick"), bundle.startServer(["-e", "process.exit(3)"]));

  assert.equal(result.exitCode, 0);
  assert.match(output, /ran the command/);
});

test("a job server refusing to start is reported as such, and the action runs its command", async (t) => {
  const bundle = await withBundle(t);
  const warn = t.mock.method(console, "warn", () => { });
  const { result, output } = await run(bundle, action("quick"), bundle.startServer(["{{bundleRoot}}/server.mjs", "--refuse"]));

  assert.equal(result.exitCode, 0);
  assert.match(output, /ran the command/);
  const warnings = warn.mock.calls.map((call) => String(call.arguments[0]));
  assert.ok(warnings.some((text) => /refused to start: Another server is using the state folder/.test(text)), warnings.join("\n"));
});

// #523 review: a server outliving a hard-killed app kept its state locked, so later ones couldn't start.
test("a job server given {{appPid}} exits when the app is killed", async (t) => {
  const bundle = await withBundle(t);
  const app = String.raw`
    const { createProcessManager } = await import(${JSON.stringify(new URL("../dist/web/src/server/process-runner.js", import.meta.url).href)});
    const { createJobServer } = await import(${JSON.stringify(new URL("../dist/web/src/server/job-server.js", import.meta.url).href)});
    const { runProcess } = createProcessManager({ maxOutputBytes: 65536, maxErrorBytes: 65536 });
    const server = createJobServer({ command: { executable: process.execPath, arguments: ["{{bundleRoot}}/server.mjs", "--parent-pid", "{{appPid}}"] } }, process.cwd(), runProcess);
    console.log((await server.address()) ? "ready" : "unavailable");
  `;
  const child = spawn(process.execPath, ["--input-type=module", "-e", app], { cwd: bundle.bundleRoot, stdio: ["ignore", "pipe", "inherit"] });
  t.after(() => child.kill("SIGKILL"));
  const [line] = await once(child.stdout, "data");
  assert.equal(String(line).trim(), "ready");
  const serverPid = Number(await readFile(path.join(bundle.bundleRoot, "server.pid"), "utf8"));
  assert.ok(isAlive(serverPid));

  child.kill("SIGKILL");
  const deadline = Date.now() + 10_000;
  while (isAlive(serverPid) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(isAlive(serverPid), false);
});

function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  }
  catch {
    return false;
  }
}

test("#1459: the next job succeeds on a new server immediately after terminate-all", async (t) => {
  const bundle = await withBundle(t);
  const jobServer = bundle.startServer();
  const first = await jobServer.address();
  assert.ok(first);
  await bundle.manager.terminateAllProcesses();

  const { result, events, output } = await run(bundle, action("quick"), jobServer);
  assert.equal(result.exitCode, 0);
  assert.match(output, /\[job\] succeeded/);
  assert.equal(events.filter((event) => event.type === "submission-pending").length, 1, "no retry against a stale address");
  const replacement = await jobServer.address();
  assert.ok(replacement);
  assert.notEqual(replacement.token, first.token);
});
