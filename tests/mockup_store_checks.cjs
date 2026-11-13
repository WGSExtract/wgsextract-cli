const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");
const path = require("node:path");

const source = readFileSync(path.join(__dirname, "../gui/mockups/shared/annotate/store.js"), "utf8");

function browser({ hosted = true, protocol = "http:", version = {}, pathname = "/" } = {}) {
  const storage = new Map(), timers = new Map(), events = [];
  let online = false, sequence = 0, requests = 0;
  const doc = { page: "index", title: "Main", notes: {} };
  const WGSM = {
    page: { id: "index", title: "Main" }, params: new URLSearchParams(),
    link: p => "/" + p, emit: (name, value) => events.push({ name, value }),
    pref: () => null,
  };
  const location = { protocol, pathname, hostname: "review.invalid", href: protocol + "//review.invalid" + pathname };
  const context = {
    window: { WGSM, WGSM_VERSION: version }, location, URL, URLSearchParams, AbortController,
    document: { querySelector: () => hosted ? { getAttribute: () => pathname.startsWith("/local/") ? "local/draft" : pathname.startsWith("/preview/") ? "mockups/draft" : "main" } : null },
    localStorage: {
      getItem(key) { assert.ok(!hosted, "hosted notes must not read browser storage"); return storage.get(key) || null; },
      setItem(key, value) { assert.ok(!hosted, "hosted notes must not write browser storage"); storage.set(key, value); },
      get length() { assert.ok(!hosted, "hosted notes must not enumerate browser storage"); return storage.size; },
      key: i => [...storage.keys()][i],
    },
    setTimeout(fn, delay) { const id = ++sequence; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    async fetch(url, opts = {}) {
      requests++;
      const endpoint = new URL(url, location.href).pathname;
      if (endpoint.startsWith("/feedback/")) return { ok: true, json: async () => [] };
      if (!online) throw new TypeError("network unavailable");
      if (endpoint === "/api/health") return { ok: true, json: async () => ({ ok: true }) };
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
    assert.equal(b.requests(), before, "writes must be blocked before recovery");
    b.online(true);
    await b.retry();
    assert.equal(b.store.available, true);
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

for (const options of [
  { hosted: false, protocol: "file:" },
  { hosted: false, protocol: "https:", version: { source: "pages" } },
]) {
  test("static review still saves browser notes: " + options.protocol, async () => {
    const b = browser(options);
    await b.store.init();
    await b.store.apply({ op: "add", note: { id: "nstatic", author: "Randy", text: "Browser note" } });
    assert.equal(JSON.parse(b.storage.get("wgsm.notes.index")).notes.nstatic.text, "Browser note");
    assert.equal(b.store.mode, "local");
  });
}
