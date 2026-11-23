/* WGSM.v2api: the mockup's client of the WGS Extract server, and the seam
 * where the mock is swapped for the real thing. Two modes share one mock
 * core (api-core.js … api-admin.js):
 *
 *   "browser"  the mock runs in this page; state in this browser
 *              (localStorage). The default, e.g. on GitHub Pages.
 *   "shared"   requests go over HTTP to a server: the shared mock server
 *              (mock-server/server.mjs), so reviewers see the same jobs,
 *              or later the real `wgsextract serve` (add its token).
 *
 * Shared mode is chosen by ?api=<base URL> (remembered; ?api=browser goes
 * back), by setMode(), or found on its own when the page is served on port
 * 8765 and a mock server answers on 8767 of the same host.
 *
 *   request(method, path, body) -> Promise<{status, body}>; get/post/patch/del
 *   on(fn)    fn({mode, now}) about once a second (real), while open
 *   mode(), baseUrl(), setMode(mode, url, token), ready (Promise)
 *   reset()   back to the seed: this browser's, or the shared server's
 *
 * The transport is the only thing that differs: browser mode calls the
 * in-page router after a short simulated latency; shared mode is fetch()
 * with JSON and an optional Bearer token. See MOCK_API.md. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var M = window.WGSMMock;
  var TICK_MS = 1000;
  var listeners = [];
  var local = null;
  var state = { mode: "browser", base: "", token: "" };
  var mirror = null;
  var cache = {};
  var flagsSeen = null;
  var ticks = 0;

  function config() {
    var mm = WGSM.v2machine;
    return M.buildConfig({ profiles: mm.profiles, machineKey: mm.machine.key, files: mm.baseFiles, people: WGSM.v2state.people(),
      toolEnvs: mm.toolEnvs, toolLimits: mm.toolLimits });
  }

  function localMock() {
    if (!local) {
      local = M.createMock(config(), {
        storage: {
          get: function (k) {
            return WGSM.pref(k);
          },
          set: function (k, v) {
            WGSM.pref(k, v);
          },
        },
        realNow: Date.now,
        machineKey: WGSM.v2machine.machine.key,
        machineCustom: WGSM.v2machine.machine.key === "custom" ? WGSM.v2machine.profile() : null,
      });
    }
    return local;
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  var transports = {
    browser: function (method, path, body) {
      return sleep(20 + Math.random() * 60).then(function () {
        return M.request(localMock(), method, path, body === undefined ? null : JSON.parse(JSON.stringify(body)));
      });
    },
    /* The real server: the same paths under <base>/v1, a Bearer token */
    shared: function (method, path, body) {
      var headers = { Accept: "application/json" };
      var hasBody = body !== undefined && body !== null;
      if (hasBody) headers["Content-Type"] = "application/json";
      if (state.token) headers.Authorization = "Bearer " + state.token;
      return fetch(state.base + path, { method: method, headers: headers, cache: "no-store", body: hasBody ? JSON.stringify(body) : undefined })
        .then(function (res) {
          var type = res.headers.get("Content-Type") || "";
          return (type.indexOf("json") >= 0 ? res.json() : res.text()).then(function (b) {
            return { status: res.status, body: b, type: type };
          });
        })
        .catch(function (err) {
          return { status: 0, body: { error: { code: "unreachable", message: "No answer from " + state.base + ": " + err.message } } };
        });
    },
  };

  var ready;

  function request(method, path, body) {
    return ready.then(function () {
      return transports[state.mode](method, path, body);
    });
  }

  /* GETs within the same moment share one answer */
  function get(path) {
    var hit = cache[path];
    if (hit && Date.now() - hit.at < 400) return hit.promise;
    var promise = request("GET", path);
    cache[path] = { at: Date.now(), promise: promise };
    return promise;
  }

  function normalize(url) {
    return String(url || "").trim().replace(/\/+$/, "").replace(/\/v1$/, "");
  }

  function useMode(mode, url, token) {
    state.mode = mode === "shared" && url ? "shared" : "browser";
    state.base = state.mode === "shared" ? normalize(url) : "";
    state.token = token || "";
    mirror = null;
    cache = {};
    flagsSeen = null;
  }

  function probe(url) {
    var ctl = new AbortController();
    var timer = setTimeout(function () {
      ctl.abort();
    }, 800);
    return fetch(url + "/v1/health", { signal: ctl.signal, cache: "no-store" })
      .then(function (res) {
        return res.json();
      })
      .then(function (b) {
        return !!(b && b.mock);
      })
      .catch(function () {
        return false;
      })
      .finally(function () {
        clearTimeout(timer);
      });
  }

  /* ?api=URL or ?api=browser (both remembered), a saved choice, or the
   * mock server beside serve.py */
  function choose() {
    var q = WGSM.params.get("api");
    if (q) {
      WGSM.pref("v2-api-mode", q === "browser" ? "browser" : "shared");
      WGSM.pref("v2-api-url", q === "browser" ? "" : normalize(q));
    }
    var saved = WGSM.pref("v2-api-mode");
    if (saved === "shared" && WGSM.pref("v2-api-url")) {
      useMode("shared", WGSM.pref("v2-api-url"), WGSM.pref("v2-api-token"));
      return Promise.resolve();
    }
    if (saved !== "browser" && location.port === "8765" && /^https?:$/.test(location.protocol)) {
      var guess = location.protocol + "//" + location.hostname + ":8767";
      return probe(guess).then(function (ok) {
        useMode(ok ? "shared" : "browser", guess);
      });
    }
    useMode("browser");
    return Promise.resolve();
  }

  /* The simulated clock as this page sees it */
  function clock() {
    if (state.mode === "browser") return localMock().clock;
    return {
      now: function () {
        return mirror ? Math.round(mirror.now_ms + (Date.now() - mirror.at) * mirror.speed) : Date.now();
      },
      speed: function () {
        return mirror ? mirror.speed : 0;
      },
    };
  }

  function syncClock(body) {
    if (body && typeof body.now_ms === "number") mirror = { now_ms: body.now_ms, speed: body.speed, network_bps: body.network_bps, at: Date.now() };
    return body;
  }

  /* Capabilities the server derives from jobs (mem2-hs38, panel-1kg, …)
   * reach the page's data-v2-if hooks. Only changes are copied, so a flag
   * set by a click on a static page stays until the server changes it. */
  function syncFlags() {
    return get("/v1/mock/flags").then(function (res) {
      if (res.status !== 200 || !WGSM.v2state) return;
      var now = {};
      res.body.flags.forEach(function (f) {
        now[f] = true;
      });
      var before = flagsSeen || JSON.parse(WGSM.pref("v2-api-flags-" + state.mode) || "{}");
      var on = Object.keys(now).filter(function (f) {
        return !before[f];
      });
      var off = Object.keys(before).filter(function (f) {
        return !now[f];
      });
      if (on.length) WGSM.v2state.setFlags(on.join(" "), true);
      if (off.length) WGSM.v2state.setFlags(off.join(" "), false);
      flagsSeen = now;
      WGSM.pref("v2-api-flags-" + state.mode, JSON.stringify(now));
    });
  }

  /* Files generated on the Add files page (api-files.js), mirrored into
   * this browser for machine.js's file pickers; "v2-generated" fires when
   * they change */
  var generatedSeen = null;
  function syncGenerated() {
    return request("GET", "/v1/mock/files").then(function (res) {
      if (res.status !== 200) return;
      var text = JSON.stringify({ files: res.body.files, people: res.body.people });
      if (generatedSeen === null) generatedSeen = WGSM.pref("v2-generated") || JSON.stringify({ files: {}, people: {} });
      if (text === generatedSeen) return;
      generatedSeen = text;
      WGSM.pref("v2-generated", text);
      WGSM.emit("v2-generated", res.body);
    });
  }

  function notify() {
    var detail = { mode: state.mode, now: clock().now() };
    listeners.slice().forEach(function (fn) {
      try {
        fn(detail);
      } catch (err) {
        console.error(err);
      }
    });
    WGSM.emit("v2-api", detail);
  }

  function tick() {
    var step = state.mode === "browser"
      ? Promise.resolve(localMock().tick())
      : request("GET", "/v1/clock").then(function (res) {
        if (res.status === 200) syncClock(res.body);
      });
    return step
      .then(function () {
        var n = ticks++;
        if (n % 10 === 0) syncGenerated();
        if (n % 3 === 0) return syncFlags();
      })
      .then(notify);
  }

  ready = choose();
  ready.then(function () {
    tick();
    setInterval(tick, TICK_MS);
  });

  /* Another tab changed this browser's mock */
  window.addEventListener("storage", function (event) {
    if (state.mode === "browser" && local && /^wgsm\.v2-(api|clock)$/.test(event.key || "")) {
      local.reload();
      notify();
    }
  });

  WGSM.v2api = {
    ready: ready,
    request: request,
    get: get,
    post: function (path, body) {
      return request("POST", path, body || {});
    },
    patch: function (path, body) {
      return request("PATCH", path, body || {});
    },
    del: function (path) {
      return request("DELETE", path);
    },
    on: function (fn) {
      listeners.push(fn);
      return function () {
        listeners = listeners.filter(function (x) {
          return x !== fn;
        });
      };
    },
    refresh: tick,
    mode: function () {
      return state.mode;
    },
    baseUrl: function () {
      return state.base;
    },
    /* Remembered in this browser; resolves {ok, mock, message} once a
     * shared server has answered GET /v1/health */
    setMode: function (mode, url, token) {
      WGSM.pref("v2-api-mode", mode === "shared" ? "shared" : "browser");
      WGSM.pref("v2-api-url", mode === "shared" ? normalize(url) : "");
      WGSM.pref("v2-api-token", token || "");
      useMode(mode, url, token);
      if (state.mode === "browser") {
        return tick().then(function () {
          return { ok: true };
        });
      }
      return transports.shared("GET", "/v1/health").then(function (res) {
        tick();
        if (res.status === 200) return { ok: true, mock: !!res.body.mock };
        return { ok: false, message: res.body && res.body.error ? res.body.error.message : "HTTP " + res.status };
      });
    },
    reset: function () {
      if (state.mode === "browser") {
        localMock().reset();
        return tick().then(syncGenerated);
      }
      return request("POST", "/v1/mock/reset").then(tick).then(syncGenerated);
    },
    /* Refresh the generated files mirror now (after adding or removing) */
    syncGenerated: syncGenerated,
    /* For WGSM.v2clock */
    clock: clock,
    syncClock: syncClock,
    network: function () {
      if (state.mode === "browser") return localMock().doc().net.bps;
      return mirror ? mirror.network_bps : null;
    },
  };
})();
