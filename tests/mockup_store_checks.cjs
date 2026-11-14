const assert = require("node:assert/strict");
const { readFileSync, mkdirSync, writeFileSync, rmSync } = require("node:fs");
const { spawnSync } = require("node:child_process");
const { test } = require("node:test");
const vm = require("node:vm");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const source = readFileSync(path.join(__dirname, "../gui/mockups/shared/annotate/store.js"), "utf8");
const allNotesSource = readFileSync(path.join(__dirname, "../gui/mockups/shared/annotate/all-notes.js"), "utf8");

function browser({ hosted = true, protocol = "http:", version = {}, pathname = "/",
  storage = new Map(), committed = [], shared = true } = {}) {
  const timers = new Map(), events = [];
  let online = false, sequence = 0, requests = 0;
  const doc = { page: "index", title: "Main", notes: {} };
  const WGSM = {
    page: { id: "index", title: "Main" }, params: new URLSearchParams(),
    link: p => (pathname.match(/^\/(?:preview|local)\/[^/]+\//) || ["/"])[0] + p,
    emit: (name, value) => events.push({ name, value }),
    pref: () => null,
  };
  const location = { protocol, pathname, hostname: "review.invalid", href: protocol + "//review.invalid" + pathname };
  const context = {
    window: { WGSM, WGSM_VERSION: version }, location, URL, URLSearchParams, AbortController,
    document: { querySelector: () => hosted ? { getAttribute: () => pathname.startsWith("/local/") ? "local/draft" : pathname.startsWith("/preview/") ? "mockups/draft" : "main" } : null },
    localStorage: {
      getItem(key) { assert.ok(!hosted, "hosted notes must not read browser storage"); return storage.get(key) || null; },
      setItem(key, value) { assert.ok(!hosted, "hosted notes must not write browser storage"); storage.set(key, value); },
      removeItem(key) { assert.ok(!hosted, "hosted notes must not migrate browser storage"); storage.delete(key); },
      get length() { assert.ok(!hosted, "hosted notes must not enumerate browser storage"); return storage.size; },
      key: i => [...storage.keys()][i],
    },
    setTimeout(fn, delay) { const id = ++sequence; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    async fetch(url, opts = {}) {
      requests++;
      const endpoint = new URL(url, location.href).pathname;
      if (endpoint.endsWith("/feedback/_pages.json")) return { ok: true, json: async () => committed.map(d => d.page) };
      if (endpoint.includes("/feedback/") && !endpoint.startsWith("/api/")) return { ok: true, json: async () => committed.find(d => endpoint.endsWith("/" + d.page + ".json")) || null };
      if (!online) throw new TypeError("network unavailable");
      if (endpoint === "/api/health") return { ok: true, json: async () => ({ ok: true, shared }) };
      if (opts.method === "POST") {
        const change = JSON.parse(opts.body);
        if (change.op === "add") doc.notes[change.note.id] = change.note;
      }
      return { ok: true, json: async () => structuredClone(doc) };
    },
  };
  vm.runInNewContext(source, context);
  return {
    store: WGSM.notes, storage, doc, events,
    online(value) { online = value; }, requests: () => requests,
    async retry() {
      const [id, timer] = [...timers.entries()].sort((a, b) => a[1].delay - b[1].delay)[0];
      timers.delete(id);
      await timer.fn();
    },
  };
}

for (const options of [
  { pathname: "/" },
  { pathname: "/local/draft/" },
  { pathname: "/preview/draft/", version: { source: "preview", branch: "mockups/draft" } },
  { pathname: "/preview/draft/", hosted: false, version: { source: "preview", hosted: true, branch: "mockups/draft" } },
]) {
  test("hosted marker prevents browser-note fallback during outage: " + JSON.stringify(options), async () => {
    // Empty-version cases cover a failed version-script request too.
    const b = browser(options);
    await b.store.init();
    assert.equal(b.store.mode, "server");
    assert.equal(b.store.available, false);
    assert.match(b.store.error, /unavailable.*retrying/);
    b.store.author("Randy");
    assert.equal(b.store.author(), "Randy", "name selection must also work if browser preferences cannot persist");
    const change = { op: "add", note: { id: "noutage", author: "Randy", text: "Shared only" } };
    const before = b.requests();
    await assert.rejects(b.store.apply(change), /can't be saved/);
    await assert.rejects(b.store.api("import", {}), /can't be saved/);
    assert.throws(() => b.store.saveLocalDoc({ page: "index", notes: {} }), /cannot be saved in browser/);
    assert.equal(b.requests(), before, "writes must be blocked before recovery");
    b.online(true);
    await b.retry();
    assert.equal(b.store.available, true);
    assert.equal(b.store.shared, true);
    await b.store.apply(change);
    assert.equal(b.doc.notes.noutage.text, "Shared only");
    b.online(false);
    await assert.rejects(b.store.apply(change), /network unavailable/);
    assert.equal(b.store.available, false);
    assert.equal(b.store.doc.notes.noutage.text, "Shared only");
    assert.equal(b.storage.size, 0);
    b.online(true);
    await b.retry();
    assert.equal(b.store.available, true);
  });
}

const chrome = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
  .find(command => spawnSync(command, ["--version"], {
    encoding: "utf8", timeout: 2000, killSignal: "SIGKILL",
  }).status === 0);

test("note and exception markup stays inert in real panel, All notes and toast renderers", {
  skip: chrome ? false : "Chromium/Chrome is required for the real-DOM regression",
  timeout: 60000,
}, () => {
  const out = path.join(__dirname, "../out/776/xss-renderer");
  const profile = path.join(out, "profile-" + process.pid);
  mkdirSync(profile, { recursive: true });
  try {
    const result = spawnSync(chrome, [
      "--headless", "--no-sandbox", "--disable-gpu", "--no-first-run",
      "--user-data-dir=" + profile, "--virtual-time-budget=1000", "--dump-dom",
      pathToFileURL(path.join(__dirname, "mockup_xss_fixture.html")).href,
    ], { encoding: "utf8", timeout: 45000, killSignal: "SIGKILL" });
    writeFileSync(path.join(out, "dom.html"), result.stdout || "");
    writeFileSync(path.join(out, "browser.log"), result.stderr || "");
    assert.equal(result.status, 0, String(result.error || result.stderr));
    assert.match(result.stdout, /data-xss-result="passed"/);
    assert.match(result.stdout, /PASS: panel, All notes and error toast render markup inert/);
  } finally {
    rmSync(profile, { recursive: true, force: true });
  }
});

for (const [idea, url, linked, mode = "server", site] of [
  ["local/gone", undefined, false],
  ["mockups/gone", undefined, false],
  ["local/bad", "//index.html", false],
  ["local/live", "/local/live/index.html", true],
  ["mockups/draft", undefined, true, "local", "/preview/draft/"],
  ["mockups/unavailable", undefined, false, "local", undefined],
]) {
  test("All notes links only published same-origin ideas: " + idea, async () => {
    const controls = new Map();
    const list = {
      children: [],
      replaceChildren() { this.children = []; },
      appendChild(node) { this.children.push(node); },
    };
    const h = (spec, attrs, ...children) => {
      if (!attrs || attrs.nodeType || typeof attrs !== "object") {
        children.unshift(attrs); attrs = {};
      }
      return { nodeType: 1, spec, attrs, children, appendChild(node) { this.children.push(node); } };
    };
    const W = {
      h, on() {}, link: value => "/" + value,
      qs(selector) {
        if (selector === "#all-notes") return list;
        if (!controls.has(selector)) controls.set(selector, { value: "", addEventListener() {} });
        return controls.get(selector);
      },
      notes: {
        ready: true, mode, idea: "main",
        allDocs: async () => [{
          idea, page: "index", url, site,
          notes: { nremoved: { author: "Randy", text: "Retained note", status: "open", replies: [] } },
        }],
      },
    };
    vm.runInNewContext(allNotesSource, {
      window: { WGSM: W }, URL,
      location: { href: "http://review.invalid/notes.html", origin: "http://review.invalid" },
    });
    await new Promise(setImmediate);
    assert.equal(list.children.length, 1);
    const label = list.children[0].children[0];
    assert.equal(label.spec, linked ? "a" : "span");
    if (linked) {
      assert.equal(new URL(label.attrs.href).origin, "http://review.invalid");
      assert.match(label.attrs.href, /note=nremoved/);
      if (site) assert.equal(new URL(label.attrs.href).pathname, site + "index.html");
    } else {
      assert.equal(label.attrs.href, undefined);
      assert.match(label.children.join(""), mode === "server" ? /\(idea removed\)/ : /open this idea/);
    }
  });
}

for (const options of [
  { hosted: false, protocol: "file:" },
  { hosted: false, protocol: "https:", version: { source: "pages" } },
]) {
  test("static review still saves browser notes: " + options.protocol, async () => {
    const b = browser(options);
    await b.store.init();
    await b.store.apply({ op: "add", note: { id: "nstatic", author: "Randy", text: "Browser note" } });
    const docs = await b.store.allDocs();
    assert.equal(docs[0].notes.nstatic.text, "Browser note");
    assert.equal(b.store.mode, "local");
  });
}

test("Pages ideas isolate notes and preserve legacy documents on migration", async () => {
  const storage = new Map([["wgsm.notes.index", JSON.stringify({
    page: "index", notes: { nlegacy: { id: "nlegacy", text: "Old main review" } },
  })]]);
  const draft = browser({ hosted: false, protocol: "https:", pathname: "/preview/draft/",
    version: { source: "preview", branch: "mockups/draft" }, storage });
  await draft.store.init();
  await draft.store.apply({ op: "add", note: { id: "nsame", author: "Randy", text: "Draft review" } });
  const main = browser({ hosted: false, protocol: "https:", version: { source: "pages" }, storage });
  await main.store.init();
  await main.store.apply({ op: "add", note: { id: "nsame", author: "Randy", text: "Main review" } });
  const docs = await main.store.allDocs();
  assert.equal(docs.length, 2);
  assert.equal(docs.find(d => d.idea === "main").notes.nlegacy.text, "Old main review");
  assert.equal(docs.find(d => d.idea === "main").notes.nsame.text, "Main review");
  assert.equal(docs.find(d => d.idea === "mockups/draft").notes.nsame.text, "Draft review");
  assert.equal(docs.find(d => d.idea === "mockups/draft").site, "/preview/draft/");
  assert.equal((await main.store.summary()).pages.length, 1);
  assert.equal(storage.has("wgsm.notes.index"), false);
  main.store.saveLocalDoc({ idea: "mockups/imported", page: "index", notes: {
    nimport: { id: "nimport", text: "Imported review" },
  } });
  assert.equal((await main.store.allDocs()).find(d => d.idea === "mockups/imported").notes.nimport.text, "Imported review");
});

test("unstamped committed preview feedback exports with the preview identity", async () => {
  const b = browser({ hosted: false, protocol: "https:", pathname: "/preview/draft/",
    version: { source: "preview", branch: "mockups/draft" },
    committed: [{ page: "index", notes: { ncommitted: { id: "ncommitted", text: "Preview review" } } }] });
  await b.store.init();
  const exported = await b.store.allDocs();
  assert.equal(exported.length, 1);
  assert.equal(exported[0].idea, "mockups/draft");
  assert.equal(exported[0].notes.ncommitted.text, "Preview review");
});

test("file-backed server storage keeps the health response's non-shared identity", async () => {
  const b = browser({ shared: false });
  b.online(true);
  await b.store.init();
  assert.equal(b.store.mode, "server");
  assert.equal(b.store.shared, false);
});
