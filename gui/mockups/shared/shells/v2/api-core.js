/* Mock server core, part 1: the namespace, the simulated clock, the router
 * and pure helpers. Environment-agnostic: no DOM, window or localStorage.
 * Storage ({get(key), set(key, value)}, strings) and the real-time source
 * are injected, so the same file runs as a classic <script> in the mockup
 * and under Node (gui/mockups/mock-server/server.mjs). See MOCK_API.md. */
(function (root) {
  "use strict";
  var M = (root.WGSMMock = root.WGSMMock || {});

  function pad(n, w) {
    return String(n).padStart(w || 2, "0");
  }

  /* ISO 8601 UTC to the second, like the real server's timestamps */
  function iso(ms) {
    if (ms === null || ms === undefined) return null;
    return new Date(Math.round(ms / 1000) * 1000).toISOString().replace(".000Z", "+00:00");
  }

  function parseIso(text) {
    return text ? Date.parse(text) : null;
  }

  /* 20260105-100425-986af7: UTC creation time and 3 random bytes */
  function jobId(ms) {
    var d = new Date(ms);
    var hex = Math.floor(Math.random() * 0xffffff).toString(16).padStart(6, "0");
    return d.getUTCFullYear() + pad(d.getUTCMonth() + 1) + pad(d.getUTCDate()) + "-" +
      pad(d.getUTCHours()) + pad(d.getUTCMinutes()) + pad(d.getUTCSeconds()) + "-" + hex;
  }

  /* Formatting, in the local time zone of whoever runs it */
  var fmt = {
    hm: function (ms) {
      var d = new Date(ms);
      return pad(d.getHours()) + ":" + pad(d.getMinutes());
    },
    hms: function (ms) {
      var d = new Date(ms);
      return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
    },
    dayStart: function (ms) {
      var d = new Date(ms);
      d.setHours(0, 0, 0, 0);
      return d.getTime();
    },
    /* "Today 06:14", "Yesterday 18:02", "3 days ago", "Nov 2" */
    when: function (ms, now) {
      var days = Math.round((fmt.dayStart(now) - fmt.dayStart(ms)) / 86400000);
      if (days <= 0) return "Today " + fmt.hm(ms);
      if (days === 1) return "Yesterday " + fmt.hm(ms);
      if (days < 14) return days + " days ago";
      return new Date(ms).toLocaleDateString("en", { month: "short", day: "numeric" });
    },
    ago: function (ms, now) {
      var s = Math.round((now - ms) / 1000);
      if (s < 60) return "just now";
      if (s < 3600) return Math.round(s / 60) + " min ago";
      if (s < 86400) return Math.round(s / 3600) + " h ago";
      return Math.round(s / 86400) + " days ago";
    },
    /* "12 s", "3 min 41 s", "42 min", "1 h 52 min" */
    dur: function (ms) {
      var s = Math.max(0, Math.round(ms / 1000));
      if (s < 60) return s + " s";
      if (s < 600) return Math.floor(s / 60) + " min" + (s % 60 ? " " + (s % 60) + " s" : "");
      var m = Math.round(s / 60);
      if (m < 60) return m + " min";
      return Math.floor(m / 60) + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
    },
    gb: function (gb) {
      if (gb >= 1000) return (gb / 1000).toFixed(1) + " TB";
      if (gb >= 10) return Math.round(gb) + " GB";
      if (gb >= 1) return gb.toFixed(1) + " GB";
      return Math.max(1, Math.round(gb * 1000)) + " MB";
    },
    /* Bytes per second; 0 is unlimited */
    rate: function (bps) {
      if (!bps) return "unlimited";
      if (bps >= 1e9) return +(bps / 1e9).toFixed(1) + " GB/s";
      return Math.round(bps / 1e6) + " MB/s";
    },
  };

  function readJson(storage, key) {
    try {
      return JSON.parse(storage.get(key) || "null");
    } catch (err) {
      return null;
    }
  }

  /* The simulated clock: sim = anchor + (real - realAnchor) × speed.
   * Time passes only while someone is looking: a now() more than idleMs
   * (real) after the previous one re-anchors, so closing every page (or a
   * server getting no requests) pauses the simulation instead of jumping. */
  M.createClock = function (opts) {
    var storage = opts.storage;
    var key = opts.key || "v2-clock";
    var realNow = opts.realNow || Date.now;
    var idleMs = opts.idleMs || 90000;
    var defaultSpeed = opts.speed === undefined ? 60 : opts.speed;
    var s = readJson(storage, key);
    var savedAt = 0;

    function fresh() {
      var real = realNow();
      return { real: real, sim: opts.start ? opts.start(real) : real, speed: defaultSpeed, seen: real };
    }

    function save(force) {
      if (!force && s.seen - savedAt < 5000) return;
      savedAt = s.seen;
      storage.set(key, JSON.stringify(s));
    }

    function anchor(sim, real) {
      s.sim = sim;
      s.real = real;
      s.seen = real;
      save(true);
    }

    if (!s || typeof s.sim !== "number") {
      s = fresh();
      save(true);
    }

    var clock = {
      now: function () {
        var r = realNow();
        if (r - s.seen > idleMs) anchor(s.sim + (s.seen - s.real) * s.speed, r);
        s.seen = Math.max(s.seen, r);
        save(false);
        return Math.round(s.sim + (r - s.real) * s.speed);
      },
      speed: function () {
        return s.speed;
      },
      setSpeed: function (x) {
        var now = clock.now();
        s.speed = Math.max(0, Number(x) || 0);
        anchor(now, realNow());
        return s.speed;
      },
      set: function (ms) {
        anchor(ms, realNow());
      },
      advance: function (ms) {
        anchor(clock.now() + ms, realNow());
      },
      reset: function () {
        s = fresh();
        save(true);
      },
      /* Another tab or process changed the stored clock */
      reload: function () {
        s = readJson(storage, key) || fresh();
      },
    };
    return clock;
  };

  function ApiError(status, code, message) {
    this.status = status;
    this.code = code;
    this.message = message;
  }
  M.ApiError = ApiError;
  M.fail = function (status, code, message) {
    throw new ApiError(status, code, message);
  };

  /* Routes: "/v1/ops/{op*}" ({name*} spans slashes), "/v1/jobs/{id}".
   * meta: {kind: "real" | "proposed" | "mock"} for the index and the docs. */
  M.createRouter = function () {
    var routes = [];
    function compile(pattern) {
      var keys = [];
      var re = pattern.replace(/\{(\w+)(\*?)\}/g, function (m, name, star) {
        keys.push(name);
        return star ? "([a-z0-9-]+(?:/[a-z0-9-]+)*)" : "([^/]+)";
      });
      return { re: new RegExp("^" + re + "/?$"), keys: keys };
    }
    return {
      routes: routes,
      add: function (method, pattern, kind, handler) {
        var c = compile(pattern);
        routes.push({ method: method, pattern: pattern, kind: kind, re: c.re, keys: c.keys, handler: handler });
      },
      /* -> {status, body, type?}; never throws */
      handle: function (method, url, body) {
        var u = new URL(url, "http://mock");
        var query = {};
        u.searchParams.forEach(function (v, k) {
          query[k] = v;
        });
        var path = u.pathname;
        var known = false;
        try {
          for (var i = 0; i < routes.length; i++) {
            var r = routes[i];
            var m = r.re.exec(path);
            if (!m) continue;
            known = true;
            if (r.method !== method) continue;
            var params = {};
            r.keys.forEach(function (k, n) {
              params[k] = decodeURIComponent(m[n + 1]);
            });
            var out = r.handler({ method: method, path: path, params: params, query: query, body: body });
            return out && typeof out.status === "number" && "body" in out ? out : { status: 200, body: out };
          }
          if (known) M.fail(405, "method_not_allowed", "No route " + method + " " + path + ".");
          M.fail(404, "not_found", "No route " + method + " " + path + ".");
        } catch (err) {
          if (err instanceof ApiError) return { status: err.status, body: { error: { code: err.code, message: err.message } } };
          return { status: 500, body: { error: { code: "internal", message: String((err && err.stack) || err) } } };
        }
      },
    };
  };

  M.util = { pad: pad, iso: iso, parseIso: parseIso, jobId: jobId, readJson: readJson };
  M.fmt = fmt;
})(typeof globalThis !== "undefined" ? globalThis : this);
