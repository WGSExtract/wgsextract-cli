import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer, request as httpRequest } from "node:http";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { dataEnvironment } from "./workspace-fixture.mjs";

const { createRequestHandler } = await import("../dist/web/src/server/routes.js");
const { streamNDJSON } = await import("../dist/web/src/server/ndjson.js");
const { takeApiToken } = await import("../dist/web/src/server/request-guard.js");
const { recordWorkspaceMove } = await import("../dist/web/src/server/users-workspace.js");
const { appSettingsPath } = await import("../dist/web/src/server/workspace-location.js");

const apiToken = "test-token-0123456789abcdefghijklmnopqrstuvwxyz";

function testManifest() {
  return {
    id: "test-bundle",
    displayName: "Test Bundle",
    pages: [
      {
        id: "main",
        title: "Main",
        sections: [
          {
            id: "run",
            actions: [{ id: "run", title: "Run", command: { executable: "tool", arguments: ["{{sample}}"] } }],
            controls: [
              {
                id: "library",
                kind: "libraryList",
                label: "Library",
                dataSource: { path: "scripts/list.sh", arguments: ["{{sample}}"] },
                rowActions: [{ id: "delete", title: "Delete", command: { executable: "static-delete", arguments: ["{{row.id}}"] } }],
              },
              {
                id: "settings",
                kind: "configEditor",
                label: "Settings",
                configFile: { path: "{{bundleWorkspace}}/config.toml" },
                settings: [{ id: "threads", key: "threads", label: "Threads", value: "4" }],
              },
            ],
          },
        ],
      },
    ],
    setup: { steps: [{ id: "node", kind: "pathTool", label: "Find Node", value: "node" }] },
  };
}

async function withTestServer(t, overrides = {}, { fresh = false, onRequestSettled } = {}) {
  const tempRoot = await mkdtemp(path.join(tmpdir(), "wgsextract-gui-routes-"));
  for (const [key, value] of Object.entries(dataEnvironment(tempRoot))) {
      const original = process.env[key];
      process.env[key] = value;
      t.after(() => {
          if (original === undefined) delete process.env[key];
          else process.env[key] = original;
      });
  }
  t.after(async () => {
    await rm(tempRoot, { force: true, recursive: true });
  });
  await writeFile(path.join(tempRoot, "index.html"), '<!doctype html><head><meta name="gfc-api-token" content=""></head>\n');
  if (!fresh) {
    await mkdir(path.dirname(appSettingsPath()), { recursive: true });
    await writeFile(appSettingsPath(), JSON.stringify({ networkConsent: "deferred" }));
  }

  const calls = { loadBundle: [], localizedLoads: [], runProcess: [], shutdown: [] };
  const manifest = testManifest();
  const context = {
    addDevReloadClient() {},
    apiToken,
    appVersion: "1.2.3",
    bundleRoot: tempRoot,
    defaultLocale: "en",
    distRoot: tempRoot,
    dynamicActions: new Map(),
    enableDevReload: false,
    localizedBundleLoader: {
      async load(locale, preferredLocales) {
        calls.localizedLoads.push({ locale, preferredLocales });
        return { manifest, labels: {} };
      },
    },
    async loadBundle(requestedSource) {
      calls.loadBundle.push(requestedSource);
      return { bundleRootPath: path.join(tempRoot, "workspace"), sourceRootPath: requestedSource };
    },
    manifest,
    maxBodyBytes: 1_048_576,
    repoRoot: path.resolve(".."),
    async runProcess(executable, args, options = {}) {
      calls.runProcess.push({ executable, args, options });
      await options.onStdout?.("hello from process\n");
      return { exitCode: 0, stdout: "ok\n", stderr: "" };
    },
    shutdown(reason) {
      calls.shutdown.push(reason);
    },
    sourceBundleRoot: tempRoot,
    webRoot: tempRoot,
    ...overrides,
  };

  const handle = createRequestHandler(context);
  const server = createServer(onRequestSettled ? async (request, response) => {
    try {
      await handle(request, response);
    } finally {
      onRequestSettled(request.url);
    }
  } : handle);
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return { baseURL: `http://127.0.0.1:${port}`, port, calls, context, tempRoot };
}

function apiFetch(baseURL, route, { body, headers = {}, method } = {}) {
  return fetch(`${baseURL}${route}`, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers: {
      "x-gfc-token": apiToken,
      ...(body === undefined ? {} : { "content-type": "application/json" }),
      ...headers,
    },
    ...(body === undefined ? {} : { body: typeof body === "string" ? body : JSON.stringify(body) }),
  });
}

test("#1113: gene.iobio hand-off needs fresh consent and passes only gene and build to the browser", async (t) => {
  const launched = [];
  const { baseURL } = await withTestServer(t, { openBrowser: async (url) => { launched.push(url); } });
  const route = "/api/gene/iobio/open";
  const choice = { gene: "BRCA1", build: "hg38" };
  const consent = { ...choice, consentVersion: 2 };
  assert.equal((await apiFetch(baseURL, route, { body: consent, headers: { "x-gfc-token": "" } })).status, 401);
  assert.equal((await apiFetch(baseURL, route, { body: choice })).status, 403);
  assert.equal((await apiFetch(baseURL, route, { body: { ...choice, consentVersion: 1 } })).status, 403, "The old notice does not authorize a new launch.");
  assert.equal((await apiFetch(baseURL, route, { body: { ...consent, build: "t2t" } })).status, 400);
  assert.equal((await apiFetch(baseURL, route, { body: { ...consent, gene: "BRCA1&sample0=test" } })).status, 400);
  assert.deepEqual(launched, []);

  // Capture the system browser launch, not a live request to the service.
  const result = await apiFetch(baseURL, route, { body: {
    ...consent, vcf: "/private/local.vcf.gz", sample: "private-sample", url: "https://example.org",
  } });
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), { ok: true });
  assert.equal(launched.length, 1);
  const [url] = launched;
  assert.equal(`${url.origin}${url.pathname}`, "https://gene.iobio.io/");
  assert.deepEqual(Object.fromEntries(url.searchParams), { gene: "BRCA1", build: "GRCh38" });

  const { geneIobioConsent: receipt } = JSON.parse(await readFile(appSettingsPath(), "utf8"));
  assert.ok(Number.isFinite(Date.parse(receipt.acceptedAt)));
  assert.deepEqual(Object.keys(receipt).sort(), ["acceptedAt", "version"]);
  assert.equal((await apiFetch(baseURL, route, { body: choice })).status, 403, "A stored receipt cannot open another gene.");
  assert.equal(launched.length, 1);
});

test("#1596: desktop evidence opens only a known command on the published certification page", async (t) => {
  const launched = [];
  const { baseURL } = await withTestServer(t, { openBrowser: async (url) => { launched.push(url.href); } });
  const route = "/api/certification/open";
  const body = { command: "qc fastqc" };
  assert.equal((await apiFetch(baseURL, route, { body, headers: { "x-gfc-token": "" } })).status, 401);
  assert.equal((await apiFetch(baseURL, route, { body: { command: "https://example.org/" } })).status, 404);
  assert.deepEqual(launched, []);

  const result = await apiFetch(baseURL, route, { body: { ...body, url: "https://example.org/" } });
  assert.equal(result.status, 200);
  assert.deepEqual(launched, ["https://wgse.bio/wgsextract-cli/certification.html#command-qc-fastqc"]);
});

test("#1090: opening viewer files uses API authentication and rejects traversal or non-local paths", async (t) => {
  const manifest = testManifest();
  manifest.id = "wgs-extract";
  manifest.pages[0].sections[0].controls[1].id = "wgs_settings";
  const { baseURL, tempRoot } = await withTestServer(t, { manifest });
  const outside = path.join(tempRoot, "outside.bam");
  await writeFile(outside, "not indexed yet");
  for (const route of ["/api/viewer/open", "/api/viewer/close"]) {
    const response = await apiFetch(baseURL, route, { body: { path: outside, id: "unknown" }, headers: { "x-gfc-token": "" } });
    assert.equal(response.status, 401);
  }
  const response = await apiFetch(baseURL, "/api/viewer/open", { body: { path: outside } });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { problem: "indexMissing", fileName: "outside.bam" });
  for (const badPath of [`${tempRoot}/sub/../outside.bam`, "relative.bam", "https://example.com/outside.bam"]) {
    assert.equal((await apiFetch(baseURL, "/api/viewer/open", { body: { path: badPath } })).status, 403);
  }
});

/** fetch() cannot spoof Host, so attacks that rely on it go through http.request. */
function rawRequest(port, route, { method = "POST", headers = {}, body = "" } = {}) {
  return new Promise((resolve, reject) => {
    const request = httpRequest({ host: "127.0.0.1", port, path: route, method, headers }, (response) => {
      let text = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        text += chunk;
      });
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, text }));
    });
    request.on("error", reject);
    request.end(body);
  });
}

function within(promise, ms, what) {
  let timer;
  const timeout = new Promise((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} did not settle within ${ms} ms`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

async function readNDJSON(response) {
  const text = await response.text();
  return text.trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
}

const runContext = { fieldValues: { sample: "HG001" }, checkedOptions: {}, configValues: {}, rowValues: {} };

test("#1090: viewer permits the bundled CRAM WASM but not outside connections or inline scripts", async (t) => {
  const { baseURL, tempRoot } = await withTestServer(t);
  await mkdir(path.join(tempRoot, "web"), { recursive: true });
  await writeFile(path.join(tempRoot, "web", "viewer.html"), '<meta name="gfc-api-token" content="">');
  const response = await fetch(`${baseURL}/viewer`);
  assert.equal(response.status, 200);
  assert.match(await response.text(), new RegExp(apiToken));
  const policy = response.headers.get("content-security-policy");
  assert.match(policy, /script-src 'self' 'wasm-unsafe-eval'/);
  assert.match(policy, /connect-src 'self';/);
  assert.doesNotMatch(policy, /'unsafe-eval'|'unsafe-inline'.*script-src|https:|localhost:\*/);
  const main = await fetch(baseURL);
  assert.doesNotMatch(main.headers.get("content-security-policy"), /wasm-unsafe-eval/);
});

test("#1090: vendored viewer assets are served locally without exposing neighbouring files", async (t) => {
  const { baseURL, tempRoot } = await withTestServer(t);
  await mkdir(path.join(tempRoot, "vendor", "igv"), { recursive: true });
  await writeFile(path.join(tempRoot, "vendor", "igv", "igv.min.js"), "/* bundled igv */");
  await writeFile(path.join(tempRoot, "private.txt"), "not public");
  const response = await fetch(`${baseURL}/vendor/igv/igv.min.js`);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "/* bundled igv */");
  const escape = await fetch(`${baseURL}/vendor/igv/%2e%2e/%2e%2e/private.txt`);
  assert.equal(escape.status, 404);
});

test("#1090: viewer documents and library ship in the existing desktop resource mappings", async (t) => {
  const { baseURL, tempRoot, context } = await withTestServer(t);
  const shell = path.resolve("web/packagers/tauri");
  const config = JSON.parse(await readFile(path.join(shell, "tauri.conf.json"), "utf8"));
  for (const [source, target] of Object.entries(config.bundle.resources)) {
    if (target.startsWith("app/")) {
      await cp(path.resolve(shell, source), path.join(tempRoot, target), { recursive: true });
    }
  }
  context.distRoot = path.join(tempRoot, "app/dist");
  context.webRoot = path.join(tempRoot, "app/web");
  for (const route of ["/viewer", "/viewer.css", "/client/genome-viewer.js", "/vendor/igv/igv.min.js", "/vendor/igv/LICENSE"]) {
    const response = await fetch(`${baseURL}${route}`);
    assert.equal(response.status, 200, route);
    assert.ok((await response.text()).length > 0, route);
  }
});

test("audit regression: cross-origin simple requests and DNS rebinding cannot run commands", async (t) => {
  const { port, calls } = await withTestServer(t);
  const attack = JSON.stringify({
    action: { title: "pwn", command: { executable: "/bin/sh", arguments: ["-c", "touch /tmp/pwned"] } },
    context: runContext,
  });
  const attempts = [
    // A text/plain "simple" CORS request from an evil page: no preflight, no token.
    { "content-type": "text/plain", host: `127.0.0.1:${port}`, origin: "http://evil.example" },
    // DNS rebinding: same-origin from the browser's view, but the Host names the attacker's domain.
    { "content-type": "application/json", host: `evil.example:${port}`, origin: `http://evil.example:${port}` },
    // Spoofed Origin/Host that look local, still without the per-launch token.
    { "content-type": "application/json", host: `127.0.0.1:${port}`, origin: `http://127.0.0.1:${port}` },
  ];
  for (const headers of attempts) {
    for (const route of ["/api/run", "/api/run/stream", "/api/config/save", "/api/bundle/load", "/api/setup/stream"]) {
      const response = await rawRequest(port, route, { headers, body: attack });
      assert.ok([401, 403].includes(response.status), `${route} with ${JSON.stringify(headers)} returned ${response.status}`);
    }
  }
  assert.deepEqual(calls.runProcess, []);
});

test("API requests need the token, a local Host, a matching Origin, and JSON bodies", async (t) => {
  const { port, calls } = await withTestServer(t);
  const host = `127.0.0.1:${port}`;
  const body = JSON.stringify({ action: { pageID: "main", sectionID: "run", actionID: "run" }, context: runContext });
  const json = "application/json";

  const statuses = {
    missingToken: (await rawRequest(port, "/api/manifest", { method: "GET", headers: { host } })).status,
    wrongToken: (await rawRequest(port, "/api/manifest", { method: "GET", headers: { host, "x-gfc-token": `${apiToken.slice(0, -1)}X` } })).status,
    foreignHost: (await rawRequest(port, "/api/manifest", { method: "GET", headers: { host: `attacker.test:${port}`, "x-gfc-token": apiToken } })).status,
    wrongPortHost: (await rawRequest(port, "/api/manifest", { method: "GET", headers: { host: "127.0.0.1:1", "x-gfc-token": apiToken } })).status,
    foreignOrigin: (await rawRequest(port, "/api/run/stream", { headers: { host, origin: "http://evil.example", "x-gfc-token": apiToken, "content-type": json }, body })).status,
    textPlain: (await rawRequest(port, "/api/run/stream", { headers: { host, "x-gfc-token": apiToken, "content-type": "text/plain" }, body })).status,
    foreignHostIndex: (await rawRequest(port, "/", { method: "GET", headers: { host: `attacker.test:${port}` } })).status,
    localhostAlias: (await rawRequest(port, "/api/manifest", { method: "GET", headers: { host: `localhost:${port}`, "x-gfc-token": apiToken } })).status,
    sameOrigin: (await rawRequest(port, "/api/run/stream", { headers: { host, origin: `http://${host}`, "x-gfc-token": apiToken, "content-type": "application/json; charset=utf-8" }, body })).status,
  };

  assert.deepEqual(statuses, {
    missingToken: 401,
    wrongToken: 401,
    foreignHost: 403,
    wrongPortHost: 403,
    foreignOrigin: 403,
    textPlain: 415,
    foreignHostIndex: 403,
    localhostAlias: 200,
    sameOrigin: 200,
  });
  assert.equal(calls.runProcess.length, 1);
});

test("index.html bootstraps the token for the page and every response carries a strict CSP", async (t) => {
  const { baseURL } = await withTestServer(t);

  const response = await fetch(`${baseURL}/`);
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.match(html, new RegExp(`<meta name="gfc-api-token" content="${apiToken}">`));
  assert.equal(response.headers.get("cache-control"), "no-store");
  const csp = response.headers.get("content-security-policy");
  assert.match(csp, /default-src 'none'/);
  assert.match(csp, /script-src 'self'(;|$)/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("cross-origin-resource-policy"), "same-origin");
});

test("index.html leaves the token out when the launcher injects it", async (t) => {
  const { baseURL } = await withTestServer(t, { apiTokenInjectedByLauncher: true });

  const html = await (await fetch(`${baseURL}/`)).text();

  assert.match(html, /<meta name="gfc-api-token" content="">/);
  assert.doesNotMatch(html, new RegExp(apiToken));
});

test("launcher tokens are taken out of the environment and validated", () => {
  const environment = { GFC_API_TOKEN: apiToken, PATH: "/bin" };
  assert.equal(takeApiToken(environment), apiToken);
  assert.deepEqual(environment, { PATH: "/bin" });
  assert.match(takeApiToken({}), /^[A-Za-z0-9_-]{43}$/);
  assert.throws(() => takeApiToken({ GFC_API_TOKEN: "short" }), /at least 32/);
});

test("GET /api/manifest localizes with app version and preferred locales", async (t) => {
  const { baseURL, calls } = await withTestServer(t);

  const response = await apiFetch(baseURL, "/api/manifest?locale=fr", { headers: { "accept-language": "es;q=0.5,de;q=0.9,*;q=1" } });
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.manifest.id, "test-bundle");
  assert.equal(body.appVersion, "1.2.3");
  assert.deepEqual(calls.localizedLoads, [{ locale: "fr", preferredLocales: ["de", "es"] }]);
});

test("GET /api/manifest omits a blank app version", async (t) => {
  const { baseURL } = await withTestServer(t, { appVersion: " " });

  const body = await (await apiFetch(baseURL, "/api/manifest")).json();

  assert.equal(Object.hasOwn(body, "appVersion"), false);
});

test("POST /api/run/stream renders the referenced manifest action server-side", async (t) => {
  const { baseURL, calls } = await withTestServer(t);

  const response = await apiFetch(baseURL, "/api/run/stream", {
    body: {
      action: { pageID: "main", sectionID: "run", actionID: "run", command: { executable: "/bin/sh", arguments: ["-c", "evil"] } },
      context: { ...runContext, bundleRootPath: "/elsewhere", fileStateValues: { "sample.exists": "true" } },
    },
  });
  const events = await readNDJSON(response);

  assert.equal(response.status, 200);
  assert.deepEqual(events.map((event) => event.type), ["start", "output", "complete"]);
  assert.equal(events[1].stream, "stdout");
  assert.equal(events[2].result.exitCode, 0);
  assert.match(events[2].result.command, /tool HG001$/);
  assert.deepEqual(calls.runProcess.map((call) => [call.executable, call.args]), [["tool", ["HG001"]]]);
});

test("a declared result document is read from stdout, and only its files can be opened", async (t) => {
  const { baseURL, calls, context, tempRoot } = await withTestServer(t);
  context.manifest.resultDocument = { format: "wgsextract.result/1", executables: ["tool"], arguments: ["--json"] };
  const output = path.join(tempRoot, "sample.cram");
  await writeFile(output, "cram");
  const document = { schema: "wgsextract.result/1", status: "ok", outputs: [{ path: output, role: "alignment", size_bytes: 4 }] };
  context.runProcess = async (executable, args, options = {}) => {
    calls.runProcess.push({ executable, args });
    if (executable !== "tool") {
      return { exitCode: 0, stdout: "", stderr: "" };
    }
    await options.onStderr?.("log line\n");
    return { exitCode: 0, stdout: JSON.stringify(document), stderr: "log line\n" };
  };

  const events = await readNDJSON(await apiFetch(baseURL, "/api/run/stream", {
    body: { action: { pageID: "main", sectionID: "run", actionID: "run" }, context: runContext },
  }));
  const unlisted = await apiFetch(baseURL, "/api/result/open", { body: { path: path.join(tempRoot, "index.html"), reveal: false } });
  const listed = await apiFetch(baseURL, "/api/result/open", { body: { path: output, reveal: false } });

  assert.deepEqual(events.map((event) => [event.type, event.stream]), [["start", undefined], ["output", "stderr"], ["complete", undefined]]);
  assert.match(events[0].command, /tool HG001 --json$/);
  assert.equal(events[2].result.resultDocument.outputs[0].role, "alignment");
  assert.equal(unlisted.status, 403);
  assert.equal(listed.status, 200);
  assert.deepEqual(calls.runProcess.slice(1).map((call) => call.args), [[output]]);
});

test("only result cards listed in a result document can be shown, as text for a sandboxed frame", async (t) => {
  const { baseURL, context, tempRoot } = await withTestServer(t);
  context.manifest.resultDocument = { format: "wgsextract.result/1", executables: ["tool"], arguments: ["--json"], cardArguments: ["--card"] };
  context.manifest.pages[0].sections[0].actions[0].resultCard = true;
  const card = path.join(tempRoot, "sample_info.card.html");
  const table = path.join(tempRoot, "sample.tsv");
  const stray = path.join(tempRoot, "other.card.html");
  await writeFile(card, "<!doctype html><title>info</title><pre>Avg Read Length</pre>");
  await writeFile(table, "a\tb");
  await writeFile(stray, "<p>not from a run</p>");
  const document = {
    schema: "wgsextract.result/1",
    status: "ok",
    outputs: [{ path: table, role: "table" }, { path: card, role: "result_card", kind: "html" }],
  };
  context.runProcess = async (executable) => ({ exitCode: 0, stdout: executable === "tool" ? JSON.stringify(document) : "", stderr: "" });

  const events = await readNDJSON(await apiFetch(baseURL, "/api/run/stream", {
    body: { action: { pageID: "main", sectionID: "run", actionID: "run" }, context: runContext },
  }));
  const shown = await apiFetch(baseURL, "/api/result/card", { body: { path: card } });
  const notCard = await apiFetch(baseURL, "/api/result/card", { body: { path: table } });
  const unlisted = await apiFetch(baseURL, "/api/result/card", { body: { path: stray } });

  assert.match(events[0].command, /tool HG001 --json --card$/);
  assert.equal(shown.status, 200);
  assert.deepEqual(await shown.json(), { path: card, name: "sample_info.card.html", html: "<!doctype html><title>info</title><pre>Avg Read Length</pre>" });
  assert.equal(notCard.status, 403);
  assert.equal(unlisted.status, 403);
});

test("PR #530 review: a bug report reads the last action run in the app, not the CLI's last run", async (t) => {
  const { baseURL, context } = await withTestServer(t);
  context.manifest.resultDocument = { format: "wgsextract.result/1", executables: ["tool"], arguments: ["--json"] };
  context.manifest.pages[0].sections[0].actions.push({
    id: "report",
    title: "Report",
    command: { executable: "tool", arguments: ["report-issue"], optionalArguments: [["--run", "{{lastResultDocumentPath}}"]] },
  });
  const commands = [];
  context.runProcess = async (executable, args) => {
    commands.push(args.join(" "));
    const failed = args[0] === "FAIL";
    const document = { schema: "wgsextract.result/1", command: args[0], status: failed ? "failed" : "ok", exit_code: failed ? 1 : 0 };
    return { exitCode: failed ? 1 : 0, stdout: JSON.stringify(document), stderr: "" };
  };
  const run = async (actionID, sample, extraContext = {}) => {
    const events = await readNDJSON(await apiFetch(baseURL, "/api/run/stream", {
      body: { action: { pageID: "main", sectionID: "run", actionID }, context: { ...runContext, fieldValues: { sample }, ...extraContext } },
    }));
    return events.at(-1).result.lastResultDocumentPaths;
  };
  const savedCommand = async (filePath) => JSON.parse(await readFile(filePath, "utf8")).command;

  // A page cannot point the report at a file of its choosing.
  await run("report", "", { lastResultDocumentPaths: { last: "/etc/passwd" } });
  assert.equal(commands.at(-1), "report-issue --json");

  const afterFailure = await run("run", "FAIL");
  const afterSuccess = await run("run", "HG001");
  const afterReport = await run("report", "");

  assert.equal(await savedCommand(afterFailure.lastFailed), "FAIL");
  assert.equal(await savedCommand(afterSuccess.last), "HG001");
  assert.equal(await savedCommand(afterSuccess.lastFailed), "FAIL");
  assert.equal(commands.at(-1), `report-issue --run ${afterSuccess.last} --json`);
  // The report itself does not become the last run.
  assert.deepEqual(afterReport, afterSuccess);
  assert.equal(await savedCommand(afterReport.last), "HG001");
});

test("review regression: a listed relative output stays tied to its run's workspace, and a failed opener is reported", async (t) => {
  const { baseURL, context, tempRoot } = await withTestServer(t);
  context.manifest.resultDocument = { format: "wgsextract.result/1", executables: ["tool"], arguments: ["--json"] };
  const document = { schema: "wgsextract.result/1", status: "ok", outputs: [{ path: "report.html", role: "report" }] };
  const otherRoot = path.join(tempRoot, "other-bundle");
  await mkdir(otherRoot);
  await writeFile(path.join(tempRoot, "report.html"), "first");
  await writeFile(path.join(otherRoot, "report.html"), "second");
  context.runProcess = async (executable) => executable === "tool"
    ? { exitCode: 0, stdout: JSON.stringify(document), stderr: "" }
    : { exitCode: 4, stdout: "", stderr: "no application can open report.html\n" };

  await readNDJSON(await apiFetch(baseURL, "/api/run/stream", {
    body: { action: { pageID: "main", sectionID: "run", actionID: "run" }, context: runContext },
  }));
  const failedOpen = await apiFetch(baseURL, "/api/result/open", { body: { path: "report.html", reveal: false } });
  context.bundleRoot = otherRoot;
  const otherBundle = await apiFetch(baseURL, "/api/result/open", { body: { path: "report.html", reveal: false } });

  if (process.platform !== "win32") {
    assert.equal(failedOpen.status, 500);
    assert.match((await failedOpen.json()).error, /no application can open/);
  }
  assert.equal(otherBundle.status, 403);
});

test("POST /api/run/stream rejects command bodies and unknown actions without running anything", async (t) => {
  const { baseURL, calls } = await withTestServer(t);

  const legacy = await apiFetch(baseURL, "/api/run/stream", {
    body: { action: { title: "Run", command: { executable: "tool", arguments: [] } }, context: runContext },
  });
  const unknown = await apiFetch(baseURL, "/api/run/stream", {
    body: { action: { pageID: "main", sectionID: "run", actionID: "nope" }, context: runContext },
  });
  const badContext = await apiFetch(baseURL, "/api/run/stream", {
    body: { action: { pageID: "main", sectionID: "run", actionID: "run" }, context: { fieldValues: { sample: { nested: true } } } },
  });
  const removedEndpoint = await apiFetch(baseURL, "/api/run", {
    body: { action: { pageID: "main", sectionID: "run", actionID: "run" }, context: runContext },
  });

  assert.equal(legacy.status, 400);
  assert.match((await legacy.json()).error, /Missing required field: pageID/);
  assert.equal(unknown.status, 404);
  assert.equal(badContext.status, 400);
  assert.equal(removedEndpoint.status, 404);
  assert.deepEqual(calls.runProcess, []);
});

test("data source row actions replace the declared ones for that control", async (t) => {
  const processCalls = [];
  const { baseURL } = await withTestServer(t, {
    async runProcess(executable, args) {
      processCalls.push({ executable, args });
      if (executable.endsWith("list.sh")) {
        return {
          exitCode: 0,
          stderr: "",
          stdout: JSON.stringify({ rows: [{ id: "hg38" }], rowActions: [{ id: "delete", title: "Delete", command: { executable: "dynamic-delete", arguments: ["{{row.id}}"] } }] }),
        };
      }
      return { exitCode: 0, stdout: "", stderr: "" };
    },
  });
  const reference = { pageID: "main", sectionID: "run", controlID: "library", actionID: "delete" };
  const rowContext = { ...runContext, rowValues: { id: "hg38" } };

  await readNDJSON(await apiFetch(baseURL, "/api/run/stream", { body: { action: reference, context: rowContext } }));
  const payload = await (await apiFetch(baseURL, "/api/datasource", { body: { source: { pageID: "main", sectionID: "run", controlID: "library" }, context: runContext } })).json();
  await readNDJSON(await apiFetch(baseURL, "/api/run/stream", { body: { action: reference, context: rowContext } }));
  const missingSource = await apiFetch(baseURL, "/api/datasource", { body: { source: { pageID: "main", sectionID: "run" }, context: runContext } });

  assert.deepEqual(payload.rows, [{ id: "hg38" }]);
  assert.deepEqual(processCalls.map((call) => [path.basename(call.executable), call.args]), [
    ["static-delete", ["hg38"]],
    ["list.sh", ["HG001"]],
    ["dynamic-delete", ["hg38"]],
  ]);
  assert.equal(missingSource.status, 404);
});

test("POST /api/precheck resolves the precheck from the manifest", async (t) => {
  const { baseURL } = await withTestServer(t);

  const setup = await apiFetch(baseURL, "/api/precheck", { body: { target: { kind: "setup" }, labels: {} } });
  const action = await apiFetch(baseURL, "/api/precheck", { body: { target: { kind: "action", pageID: "main", sectionID: "run", actionID: "run" }, context: runContext, labels: {} } });
  const raw = await apiFetch(baseURL, "/api/precheck", { body: { precheck: { diskSpaceGB: "1" }, labels: {} } });

  assert.equal(setup.status, 200);
  assert.equal(await setup.json(), null);
  assert.equal(action.status, 200);
  assert.equal(raw.status, 400);
});

test("POST /api/setup/stream emits setup completion", async (t) => {
  const { baseURL, calls } = await withTestServer(t);

  const response = await apiFetch(baseURL, "/api/setup/stream", { body: { locale: "en" } });
  const events = await readNDJSON(response);

  assert.equal(response.status, 200);
  assert.equal(events.at(-1).type, "complete");
  assert.equal(events.at(-1).result.status, "ok");
  assert.equal(calls.runProcess.length, 1);
});

test("PR #531 review: nothing starts or writes while the workspace moves, so no change is lost with the old copy", async (t) => {
  let finishMove;
  const moving = new Promise((resolve) => {
    finishMove = resolve;
  });
  let started;
  const moveStarted = new Promise((resolve) => {
    started = resolve;
  });
  const { baseURL, calls } = await withTestServer(t, {
    async moveWorkspace() {
      started();
      await moving;
    },
  });

  const move = apiFetch(baseURL, "/api/workspace-location/move", { body: { directory: "/elsewhere" } });
  await moveStarted;
  const blocked = await apiFetch(baseURL, "/api/setup/stream", { body: { locale: "en" } });
  assert.equal(blocked.status, 409);
  assert.equal((await blocked.json()).error, "workspaceMoveInProgress");
  const save = () => apiFetch(baseURL, "/api/config/save", { body: { controlID: "settings", values: { threads: "8" } } });
  assert.equal((await save()).status, 409);
  assert.equal(calls.runProcess.length, 0);

  finishMove();
  assert.equal((await readNDJSON(await move)).at(-1).type, "complete");
  assert.equal((await save()).status, 200);
  const after = await apiFetch(baseURL, "/api/setup/stream", { body: { locale: "en" } });
  assert.equal((await readNDJSON(after)).at(-1).type, "complete");
  assert.equal(calls.runProcess.length, 1);
});

test("#1322: another tab's older settings cannot undo a completed folder move", async (t) => {
  const { baseURL, context, tempRoot } = await withTestServer(t, {
    workspaceMoveRevision: "0",
    async moveWorkspace() {
      await writeFile(path.join(tempRoot, "config.toml"), 'threads = "9"\n');
      await recordWorkspaceMove(context.manifest);
    },
  });
  const manifest = async () => {
    const response = await apiFetch(baseURL, "/api/manifest");
    assert.equal(response.status, 200);
    return response.json();
  };
  const before = (await manifest()).workspaceMoveRevision;
  const move = await readNDJSON(await apiFetch(baseURL, "/api/workspace-location/move", { body: { folder: "/elsewhere" } }));
  assert.equal(move.at(-1).type, "complete");

  const save = (revision) => apiFetch(baseURL, "/api/config/save", {
    headers: { "x-wgse-workspace-revision": revision },
    body: { controlID: "settings", values: { threads: "5" } },
  });
  const blocked = await save(before);
  assert.equal(blocked.status, 409);
  assert.equal((await blocked.json()).error, "workspaceMoveStale");
  assert.match(await readFile(path.join(tempRoot, "config.toml"), "utf8"), /"9"/);
  assert.equal((await save((await manifest()).workspaceMoveRevision)).status, 200);
  assert.match(await readFile(path.join(tempRoot, "config.toml"), "utf8"), /"5"/);
});

test("disconnecting a setup stream cancels the running step and frees the workspace", async (t) => {
  const runs = [];
  const { baseURL } = await withTestServer(t, {
    runProcess(_executable, _args, options = {}) {
      return new Promise((resolve, reject) => {
        runs.push(options.signal);
        options.signal?.addEventListener("abort", () => reject(new Error("Process cancelled.")), { once: true });
      });
    },
  });
  const waitForRuns = async (count) => {
    while (runs.length < count) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };

  const first = await apiFetch(baseURL, "/api/setup/stream", { body: {} });
  await waitForRuns(1);
  const concurrent = await readNDJSON(await apiFetch(baseURL, "/api/setup/stream", { body: {} }));
  await first.body.cancel();
  while (!runs[0].aborted) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  // Once the cancelled run has settled the workspace lock is free again.
  const retry = await apiFetch(baseURL, "/api/setup/stream", { body: {} });
  await waitForRuns(2);
  await retry.body.cancel();

  assert.equal(concurrent.at(-1).type, "complete");
  assert.match(concurrent.at(-1).result.error, /already running/);
  assert.equal(runs.length, 2);
});

test("#1234: a client that stops reading and disconnects mid-move releases the workspace lock", async (t) => {
  let blocked;
  const moveBlocked = new Promise((resolve) => {
    blocked = resolve;
  });
  const moveSettled = Promise.withResolvers();
  const progress = { type: "progress", text: "x".repeat(256 * 1024) };
  const { baseURL, port } = await withTestServer(t, {
    async moveWorkspace(_target, emit, signal) {
      while (!signal.aborted) {
        const written = emit(progress);
        const waiting = await Promise.race([written.then(() => false), new Promise((resolve) => setTimeout(resolve, 50, true))]);
        if (waiting) blocked();
        await written;
      }
    },
  }, {
    onRequestSettled(url) {
      // Callback completion alone does not mean the outer route has released the move lease.
      if (url === "/api/workspace-location/move") moveSettled.resolve();
    },
  });

  const body = JSON.stringify({ folder: "/elsewhere" });
  const client = connect(port, "127.0.0.1", () => {
    client.write(`POST /api/workspace-location/move HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\nx-gfc-token: ${apiToken}\r\n`
      + `content-type: application/json\r\ncontent-length: ${Buffer.byteLength(body)}\r\n\r\n${body}`);
    client.pause();
  });
  client.on("error", () => {});
  try {
    await within(moveBlocked, 5000, "backpressure");
  }
  finally {
    client.destroy();
  }

  await within(moveSettled.promise, 5000, "the move");
  const after = await apiFetch(baseURL, "/api/setup/stream", { body: { locale: "en" } });
  assert.equal(after.status, 200);
  assert.equal((await readNDJSON(after)).at(-1).type, "complete");
});

test("bundles load only from folders picked in the native dialog", async (t) => {
  const picker = createServer((request, response) => {
    assert.equal(request.headers["x-gfc-token"], apiToken);
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ path: "/picked/bundle", kind: "directory", cancelled: false }));
  });
  await new Promise((resolve) => picker.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => picker.close(resolve)));
  const { baseURL, calls } = await withTestServer(t, { nativePicker: { port: picker.address().port, token: apiToken } });

  const unpicked = await apiFetch(baseURL, "/api/bundle/load", { body: { path: "/tmp/source-bundle" } });
  const pick = await (await apiFetch(baseURL, "/api/path/pick", { body: { kind: "directory", title: "Bundle" } })).json();
  const picked = await apiFetch(baseURL, "/api/bundle/load", { body: { path: pick.path } });
  const reused = await apiFetch(baseURL, "/api/bundle/load", { body: { path: pick.path } });

  assert.equal(unpicked.status, 403);
  assert.equal(picked.status, 200);
  assert.equal((await picked.json()).sourceRootPath, "/picked/bundle");
  assert.equal(reused.status, 403);
  assert.deepEqual(calls.loadBundle, ["/picked/bundle"]);
});

test("settings are only written to the declared file type of a declared settings editor", async (t) => {
  const { baseURL, tempRoot } = await withTestServer(t);
  const save = (body) => apiFetch(baseURL, "/api/config/save", { body });

  const declared = await save({ controlID: "settings", values: { threads: "8" } });
  const other = await save({ controlID: "settings", path: path.join(tempRoot, "other.toml"), values: { threads: "2", injected: "x" } });
  const shellProfile = await save({ controlID: "settings", path: path.join(tempRoot, ".zshrc"), values: { threads: "1" } });
  const unknownControl = await save({ controlID: "library", values: {} });
  const badState = await apiFetch(baseURL, "/api/state/save", { body: { state: { configFilePaths: { settings: path.join(tempRoot, "evil.sh") } } } });

  assert.equal(declared.status, 200);
  assert.equal(await readFile(path.join(tempRoot, "config.toml"), "utf8"), 'threads = "8"\n');
  assert.equal(other.status, 200);
  assert.equal(await readFile(path.join(tempRoot, "other.toml"), "utf8"), 'threads = "2"\n');
  assert.equal(shellProfile.status, 400);
  assert.equal(unknownControl.status, 404);
  assert.equal(badState.status, 400);
});

test("bundle files are served from the workspace only", async (t) => {
  const { baseURL, tempRoot } = await withTestServer(t);
  await writeFile(path.join(tempRoot, "icon.png"), "png");

  const icon = await fetch(`${baseURL}/bundle-file?path=icon.png`);
  const escape = await fetch(`${baseURL}/bundle-file?path=..%2F..%2Fetc%2Fpasswd`);

  assert.equal(icon.status, 200);
  assert.equal(icon.headers.get("content-type"), "image/png");
  assert.equal(escape.status, 404);
});

test("NDJSON streams abort the run and drop the connection when the client goes away", async () => {
  const response = new FailingStreamResponse(2);
  let runSignal;

  await streamNDJSON(response, async (emit, signal) => {
    runSignal = signal;
    await emit({ type: "start" });
    await emit({ type: "output", text: "chunk\n" });
    await new Promise((resolve) => setImmediate(resolve));
    await emit({ type: "output", text: "after failure\n" });
    await emit({ type: "complete" });
  }, (error) => ({ type: "error", error }));

  assert.equal(runSignal.aborted, true);
  assert.equal(response.destroyed, true);
  assert.match(response.destroyError.message, /client disconnected/);
  assert.doesNotMatch(response.body, /after failure/);
});

class FailingStreamResponse extends EventEmitter {
  headersSent = false;
  writableEnded = false;
  destroyed = false;
  destroyError = undefined;
  body = "";
  #writeCount = 0;
  #failOnWrite;

  constructor(failOnWrite) {
    super();
    this.#failOnWrite = failOnWrite;
  }

  writeHead() {
    this.headersSent = true;
  }

  write(chunk) {
    this.#writeCount += 1;
    this.body += String(chunk);
    if (this.#writeCount === this.#failOnWrite) {
      setImmediate(() => this.emit("error", new Error("client disconnected")));
      return false;
    }
    return true;
  }

  end() {
    this.writableEnded = true;
  }

  destroy(error) {
    this.destroyed = true;
    this.destroyError = error;
    this.emit("close");
  }
}

test("the prerequisite routes check the referenced action and install only what the check reports", async (t) => {
  const manifest = testManifest();
  manifest.prerequisites = { command: { executable: "tool", arguments: ["needs"] }, executables: { tool: [] } };
  const hs37d5 = { id: "hs37d5", kind: "genome", name: "hs37d5", installArguments: ["install", "hs37d5"] };
  const { baseURL, calls } = await withTestServer(t, {
    manifest,
    async runProcess(executable, args) {
      calls.runProcess.push({ executable, args });
      return { exitCode: 0, stdout: args[0] === "needs" ? JSON.stringify({ missing: [hs37d5] }) : "", stderr: "" };
    },
  });
  const action = { pageID: "main", sectionID: "run", actionID: "run" };

  const check = await apiFetch(baseURL, "/api/prerequisites", { body: { action, context: runContext } });
  assert.deepEqual(await check.json(), { missing: [hs37d5] });
  const install = await apiFetch(baseURL, "/api/prerequisites/install/stream", { body: { action, context: runContext, id: "hs37d5" } });
  const events = await readNDJSON(install);

  assert.equal(events.at(-1).type, "complete");
  assert.deepEqual(calls.runProcess.map((call) => [call.executable, call.args]), [
    ["tool", ["needs", "HG001"]],
    ["tool", ["needs", "HG001"]],
    ["tool", ["install", "hs37d5"]],
  ]);
});

test("#1591 a fresh desktop blocks downloads until a choice, persists it, and preserves other settings", async (t) => {
  const { baseURL } = await withTestServer(t, {}, { fresh: true });
  const settingsPath = appSettingsPath();
  await mkdir(path.dirname(settingsPath), { recursive: true });
  await writeFile(settingsPath, JSON.stringify({ usersWorkspaces: { "test-bundle": "/kept" } }));

  const settings = "/api/network-settings";
  assert.equal((await apiFetch(baseURL, settings, { body: { privacyMode: "yes" } })).status, 400);
  assert.equal((await apiFetch(baseURL, settings, { body: { checkForUpdatesAtLaunch: true } })).status, 409);
  const setup = "/api/setup/stream";
  assert.equal((await apiFetch(baseURL, setup, { body: {} })).status, 403);
  assert.equal((await apiFetch(baseURL, "/api/gene/iobio/open", { body: {} })).status, 403);

  const declined = await apiFetch(baseURL, settings, { body: { consent: "deferred" } });
  assert.equal((await declined.json()).recordedChoice, true);
  assert.equal((await apiFetch(baseURL, setup, { body: { automatic: true } })).status, 403);
  const stale = await apiFetch(baseURL, settings, { body: { consent: "accepted" } });
  assert.equal((await stale.json()).recordedChoice, false);
  await apiFetch(baseURL, settings, { body: { privacyMode: true } });
  const manifest = await (await apiFetch(baseURL, "/api/manifest")).json();
  assert.deepEqual(manifest.networkSettings, { consent: "deferred", privacyMode: true, checkForUpdatesAtLaunch: false });
  assert.deepEqual(JSON.parse(await readFile(settingsPath, "utf8")).usersWorkspaces, { "test-bundle": "/kept" });
});
