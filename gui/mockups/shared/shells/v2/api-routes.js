/* Mock server core, part 6: the routes the real server has
 * (docs/server.md, "HTTP API"), with the same shapes. Additions the real
 * server lacks are marked PROPOSED here and in MOCK_API.md: GET /v1/jobs
 * ?detail=1 and the job view's "ui" object. Environment-agnostic. */
(function (root) {
  "use strict";
  var M = root.WGSMMock;
  var U = M.util;

  function flag(q, name) {
    return /^(1|true|yes)$/i.test(q[name] || "");
  }

  function summary(job) {
    var s = { id: job.id, op: job.op, status: job.status, created: U.iso(job.created) };
    if (job.finished !== null) s.finished = U.iso(job.finished);
    return s;
  }

  function schema(op) {
    return {
      type: "object",
      properties: {
        input: { type: "string", description: "BAM, CRAM, FASTQ or VCF" },
        ref: { type: "string" },
        outdir: { type: "string" },
        threads: { type: "integer" },
        _mock: { type: "object", description: "Mock-only hint; the real server has no such field (MOCK_API.md)" },
      },
      additionalProperties: true,
      "x-mock": op,
    };
  }

  M.routes.push(function (mock) {
    var r = mock.router;
    function now() {
      return mock.clock.now();
    }
    function view(job, q) {
      return M.view(mock, job, now(), { full: flag(q || {}, "full") });
    }

    r.add("GET", "/v1/health", "real", function () {
      return { ok: true, version: "mock", mock: true };
    });

    r.add("GET", "/v1", "real", function () {
      var m = mock.ctx().machine;
      return {
        name: "wgsextract", version: "mock", mock: true, ops: "/v1/ops", jobs: "/v1/jobs", openapi: "/v1/openapi.json",
        lookups: ["/v1/manifest", "/v1/variants", "/v1/report"],
        allowed_paths: [m.workspace, m.genomesPath, m.downloads],
        routes: r.routes.map(function (x) {
          return { method: x.method, path: x.pattern, kind: x.kind };
        }),
      };
    });

    r.add("GET", "/v1/ops", "real", function () {
      return { ops: M.ops.names().map(M.ops.describe) };
    });

    r.add("GET", "/v1/ops/{op*}", "real", function (req) {
      var d = M.ops.describe(req.params.op);
      if (!d) M.fail(404, "unknown_op", "No operation " + JSON.stringify(req.params.op) + ".");
      return Object.assign({}, d, { command: "wgsextract " + d.op.split("/").join(" "), params: schema(d.op) });
    });

    /* 202 with the job; 200 when it has already finished. ?wait=S is
     * honoured by mock.request (real seconds), not here. */
    r.add("POST", "/v1/ops/{op*}", "real", function (req) {
      var body = req.body === undefined || req.body === null ? {} : req.body;
      if (typeof body !== "object" || Array.isArray(body)) M.fail(400, "bad_json", "The body must be a JSON object of parameters.");
      M.validate(req.params.op, body);
      var job = mock.submit(req.params.op, body, now());
      return { status: mock.isDone(job) ? 200 : 202, body: view(job, req.query) };
    });

    /* Real: summaries, newest last. PROPOSED: ?detail=1 gives full views
     * (one request for the Jobs page instead of one per job). */
    r.add("GET", "/v1/jobs", "real", function (req) {
      var jobs = mock.doc().jobs.filter(function (j) {
        return !req.query.status || j.status === req.query.status;
      });
      return { jobs: jobs.map(flag(req.query, "detail") ? function (j) {
        return view(j, req.query);
      } : summary) };
    });

    r.add("GET", "/v1/jobs/{id}", "real", function (req) {
      return view(mock.find(req.params.id), req.query);
    });

    r.add("GET", "/v1/jobs/{id}/log", "real", function (req) {
      var lines = M.jobLog(mock, mock.find(req.params.id), now());
      var tail = req.query.tail === undefined ? 200 : parseInt(req.query.tail, 10);
      if (isNaN(tail)) M.fail(400, "usage", "tail must be an integer.");
      return { status: 200, type: "text/plain; charset=utf-8", body: (tail > 0 ? lines.slice(-tail) : lines).join("\n") + "\n" };
    });

    /* Server-sent events. Here: the events so far as one snapshot; the
     * Node server streams them (mock-server/server.mjs), and the browser
     * mock tells WGSM.v2api.on() listeners instead. */
    r.add("GET", "/v1/jobs/{id}/events", "real", function (req) {
      var v = view(mock.find(req.params.id), {});
      var events = [{ seq: 1, type: "status", status: v.status }];
      if (v.progress) events.push({ seq: 2, type: "progress", stage: v.progress.stage, message: v.progress.message, fraction: v.progress.fraction });
      return { status: 200, type: "text/event-stream", body: M.sse(events) };
    });

    r.add("DELETE", "/v1/jobs/{id}", "real", function (req) {
      var job = mock.cancel(req.params.id, now());
      return { status: mock.isDone(job) ? 200 : 202, body: view(job, req.query) };
    });

    r.add("POST", "/v1/jobs/{id}/rerun", "real", function (req) {
      return { status: 202, body: view(mock.rerun(req.params.id, now()), req.query) };
    });

    /* Lookups: the manifest of a finished job's output; variants and the
     * report answer empty (the mockup has static pages for those) */
    r.add("GET", "/v1/manifest", "real", function (req) {
      var path = req.query.path;
      var job = mock.doc().jobs.filter(function (j) {
        var m = M.view(mock, j, now(), {}).result;
        return m && (m.manifest === path || m.outputs.some(function (o) {
          return o.path === path;
        }));
      }).pop();
      if (!job) M.fail(404, "not_found", "No manifest at " + JSON.stringify(path) + ".");
      return view(job, req.query).result;
    });

    r.add("GET", "/v1/variants", "real", function (req) {
      return { path: req.query.path || null, rows: [], next_cursor: null };
    });

    r.add("GET", "/v1/report", "real", function (req) {
      return { path: req.query.path || null, sections: [] };
    });
  });

  M.sse = function (events) {
    return events.map(function (e) {
      return "id: " + e.seq + "\nevent: " + e.type + "\ndata: " + JSON.stringify(e) + "\n\n";
    }).join("");
  };

  /* request(): handle() plus ?wait=S, which polls in real time like the
   * real server's blocking wait (S ≤ 600). Returns a Promise. */
  M.request = function (mock, method, url, body) {
    var out = mock.handle(method, url, body);
    var u = new URL(url, "http://mock");
    var wait = Math.min(600, parseFloat(u.searchParams.get("wait")) || 0);
    var id = out.body && out.body.id;
    var done = function (b) {
      return mock.finished.indexOf(b.status) >= 0;
    };
    if (!wait || !id || out.status >= 300 || done(out.body)) return Promise.resolve(out);
    var until = Date.now() + wait * 1000;
    var again = "/v1/jobs/" + id + (u.searchParams.get("full") ? "?full=1" : "");
    return new Promise(function (resolve) {
      (function poll() {
        var job = mock.handle("GET", again, null);
        if (done(job.body) || Date.now() >= until) {
          resolve({ status: method === "POST" && !done(job.body) ? 202 : 200, body: job.body });
          return;
        }
        setTimeout(poll, 250);
      })();
    });
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
