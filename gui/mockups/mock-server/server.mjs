#!/usr/bin/env node
/* The shared mock server: the same core as the in-browser mock
 * (shared/shells/v2/api-*.js), over HTTP, with its state in a JSON file so
 * several reviewers see the same jobs. Node 18+, built-in modules only.
 *
 *   node gui/mockups/mock-server/server.mjs --host 0.0.0.0 --port 8767
 *
 * Open a mockup page with ?api=http://HOST:8767 (or from serve.py on port
 * 8765, which finds it on its own). See gui/mockups/MOCK_API.md. */
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const MOCKUPS = path.resolve(HERE, "..");
const SHELL = path.join(MOCKUPS, "shared/shells/v2");
const CORE = ["../../data/api-schema", "machine-spec", "api-core", "api-validate", "api-ops", "api-seed", "api-engine", "api-views", "api-routes", "api-proposed", "api-admin", "api-files", "api-edge"];
const USAGE = `Usage: server.mjs [--host 127.0.0.1] [--port 8767] [--state FILE] [--speed N]

  --host    address to listen on (0.0.0.0 for other computers)
  --port    port (default 8767; pages served on 8765 look for it there)
  --state   JSON file for the shared state (default gui/mockups/out/mock-server/state.json)
  --speed   simulated seconds per real second (default: as saved, else 60)

Clear the shared state: POST /v1/mock/reset, or stop the server and delete the file.`;

function parseArgs(argv) {
  const opts = { host: "127.0.0.1", port: 8767, state: path.join(MOCKUPS, "out/mock-server/state.json"), speed: null };
  for (let i = 0; i < argv.length; i++) {
    const [flag, inline] = argv[i].split("=", 2);
    const value = () => (inline !== undefined ? inline : argv[++i]);
    if (flag === "--host") opts.host = value();
    else if (flag === "--port") opts.port = Number(value());
    else if (flag === "--state") opts.state = path.resolve(value());
    else if (flag === "--speed") opts.speed = Number(value());
    else if (flag === "-h" || flag === "--help") {
      console.log(USAGE);
      process.exit(0);
    } else {
      console.error(`Unknown option ${argv[i]}\n\n${USAGE}`);
      process.exit(2);
    }
  }
  if (!Number.isInteger(opts.port) || opts.port <= 0) throw new Error("--port must be a port number");
  if (opts.speed !== null && !(opts.speed >= 0)) throw new Error("--speed must be 0 or more");
  return opts;
}

/* The core files, as the browser loads them: classic scripts on the global */
export function loadCore() {
  for (const name of CORE) {
    const file = path.join(SHELL, name + ".js");
    vm.runInThisContext(fs.readFileSync(file, "utf8"), { filename: file });
  }
  return globalThis.WGSMMock;
}

/* The demo's people, files and computers come from the mockup's own
 * state.js and machine.js, run against a stub WGSM with no stored state */
export function loadDemoData(M) {
  const WGSM = { pref: () => null, page: { id: "mock-server" }, emit() {}, on() {} };
  const context = vm.createContext({ window: { WGSM }, console });
  for (const name of ["state", "machine-spec", "machine"]) {
    const file = path.join(SHELL, name + ".js");
    vm.runInContext(fs.readFileSync(file, "utf8"), context, { filename: file });
  }
  const plain = (x) => JSON.parse(JSON.stringify(x));
  return M.buildConfig({
    profiles: plain(WGSM.v2machine.profiles),
    machineKey: WGSM.v2machine.machine.key,
    files: plain(WGSM.v2machine.baseFiles),
    people: plain(WGSM.v2state.people()),
    toolEnvs: plain(WGSM.v2machine.toolEnvs),
    toolLimits: plain(WGSM.v2machine.toolLimits),
  });
}

/* {get, set} over one JSON file, written atomically at most twice a second */
export function fileStorage(file) {
  let data = {};
  try {
    data = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    if (err.code !== "ENOENT") console.warn(`Ignoring unreadable state ${file}: ${err.message}`);
  }
  let timer = null;
  const flush = () => {
    timer = null;
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file + ".tmp", JSON.stringify(data, null, 1));
    fs.renameSync(file + ".tmp", file);
  };
  return {
    get: (key) => (key in data ? data[key] : null),
    set(key, value) {
      data[key] = value;
      timer ??= setTimeout(flush, 500);
    },
    flush() {
      if (timer) clearTimeout(timer);
      flush();
    },
  };
}

function cors(req) {
  const headers = {
    "Access-Control-Allow-Origin": req.headers.origin || "*",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Last-Event-ID",
    "Access-Control-Max-Age": "600",
    Vary: "Origin",
  };
  /* Chrome's Private Network Access: a public page calling this computer */
  if (req.headers["access-control-request-private-network"]) headers["Access-Control-Allow-Private-Network"] = "true";
  return headers;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > 1 << 20) reject(Object.assign(new Error("Body too large."), { code: "bad_length" }));
      else chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

/* GET /v1/jobs/{id}/events: status and progress events until the job ends */
function streamEvents(mock, id, req, res, headers) {
  const first = mock.handle("GET", `/v1/jobs/${id}`, null);
  if (first.status !== 200) {
    res.writeHead(first.status, { ...headers, "Content-Type": "application/json" });
    return res.end(JSON.stringify(first.body));
  }
  let seq = 0;
  let beat = 0;
  let last = {};
  res.writeHead(200, { ...headers, "Content-Type": "text/event-stream", "Cache-Control": "no-store", Connection: "close" });
  const send = (type, data) => res.write(`id: ${++seq}\nevent: ${type}\ndata: ${JSON.stringify({ seq, type, ...data })}\n\n`);
  let timer = null;
  const step = () => {
    const job = mock.handle("GET", `/v1/jobs/${id}`, null).body;
    if (job.status !== last.status) send("status", { status: job.status });
    const p = job.progress;
    if (p && JSON.stringify(p) !== JSON.stringify(last.progress)) send("progress", p);
    last = job;
    if (mock.finished.includes(job.status)) {
      clearInterval(timer);
      return res.end();
    }
    if (++beat % 15 === 0) res.write(": keepalive\n\n");
  };
  timer = setInterval(step, 1000);
  req.on("close", () => clearInterval(timer));
  step();
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const M = loadCore();
  const config = loadDemoData(M);
  const storage = fileStorage(opts.state);
  const mock = M.createMock(config, { storage, realNow: Date.now });
  if (opts.speed !== null) mock.clock.setSpeed(opts.speed);

  const server = http.createServer(async (req, res) => {
    const headers = cors(req);
    const send = (status, body, type = "application/json") => {
      const text = typeof body === "string" ? body : JSON.stringify(body);
      res.writeHead(status, { ...headers, "Content-Type": type, "Cache-Control": "no-store" });
      res.end(text);
    };
    if (req.method === "OPTIONS") {
      res.writeHead(204, headers);
      return res.end();
    }
    const url = new URL(req.url, "http://mock");
    if (!url.pathname.startsWith("/v1/") && url.pathname !== "/v1" && url.pathname !== "/api/result/card") {
      return send(404, { error: { code: "not_found", message: "Unknown mock API route." } });
    }
    const events = /^\/v1\/jobs\/([A-Za-z0-9-]+)\/events$/.exec(url.pathname);
    if (events && req.method === "GET") return streamEvents(mock, events[1], req, res, headers);
    let body = null;
    try {
      const text = await readBody(req);
      if (text.trim()) body = JSON.parse(text);
    } catch (err) {
      return send(400, { error: { code: err.code || "bad_json", message: err.message } });
    }
    const out = await M.request(mock, req.method, url.pathname + url.search, body);
    if (req.method !== "GET") console.log(`${new Date().toISOString()} ${req.method} ${url.pathname} ${out.status}`);
    send(out.status, out.body, out.type || "application/json");
  });

  const stop = () => {
    storage.flush();
    server.close();
    process.exit(0);
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
  server.on("error", (err) => {
    console.error(err.code === "EADDRINUSE" ? `Port ${opts.port} on ${opts.host} is in use; pick another with --port.` : err.message);
    process.exit(1);
  });
  server.listen(opts.port, opts.host, () => {
    console.log(`WGS Extract mock server on http://${opts.host}:${opts.port}/v1 (state: ${opts.state})`);
    console.log(`Open a mockup with ?api=http://${opts.host === "0.0.0.0" ? "<this computer>" : opts.host}:${opts.port}`);
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
