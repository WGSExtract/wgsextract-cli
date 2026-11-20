/* Mock-only review controls and preflight shared by the browser and Node.
 * No filesystem or network access: paths and permissions are simulated. */
(function (root) {
  "use strict";
  var M = root.WGSMMock;
  M.routes.push(function (mock) {
    var r = mock.router;
    function settings() {
      var m = mock.ctx().machine;
      return Object.assign({ workspace: m.workspace, allowed_paths: [m.genomesPath, m.downloads], phone_access: true, max_concurrent_jobs: 1, requests: [] }, mock.doc().settings);
    }

    mock.requireIdle = function (person, path) {
      var os = mock.ctx().machine.os;
      var windows = os === "windows";
      var home = windows ? "C:/Users/you" : os === "macos" ? "/Users/you" : "/home/you";
      function normalize(p) {
        p = p.replace(/\\/g, "/").replace(/^\{\{usersWorkspace\}\}/, home + "/wgs-extract").replace(/^~(?=\/)/, home);
        return windows ? p.toLowerCase() : p;
      }
      var paths = path ? [path] : Object.keys(mock.ctx().files).filter(function (id) {
        return (mock.doc().assign[id] || mock.ctx().files[id].person) === person;
      }).map(function (id) { return mock.ctx().files[id].path; });
      paths = paths.map(normalize);
      function uses(value) {
        if (typeof value === "string") return paths.indexOf(normalize(value)) >= 0;
        return value && typeof value === "object" && Object.keys(value).some(function (key) { return uses(value[key]); });
      }
      var jobs = mock.doc().jobs.filter(function (j) {
        return !mock.isDone(j) && ((person && j.plan.person === person) || uses(j.params));
      });
      if (jobs.length) M.fail(409, "in_use", "Used by " + jobs.map(function (j) {
        return "“" + j.plan.label + "”";
      }).join(", ") + ". Cancel " + (jobs.length === 1 ? "that job" : "those jobs") + " in Jobs before removing it; existing files are untouched.");
    };

    function removeReference(id) {
      var ref = mock.config.references.filter(function (x) { return x.id === id; })[0];
      if (!ref) M.fail(404, "unknown_reference", "Unknown reference.");
      var files = mock.ctx().files;
      var active = mock.doc().jobs.some(function (j) {
        var cram = j.plan.inputs.some(function (input) {
          return Object.keys(files).some(function (key) {
            return files[key].path === input.path && files[key].tag === "CRAM" && files[key].ref === ref.id;
          });
        });
        return !mock.isDone(j) && (cram || JSON.stringify(j.params).indexOf(ref.id) >= 0 || j.plan.flags.indexOf(ref.flag || "ref-" + ref.id) >= 0);
      });
      if (active) M.fail(409, "in_use", "This reference is used by an active job. Cancel it in Jobs first.");
      mock.doc().flags[ref.flag || "ref-" + ref.id] = false;
      mock.doc().removedRefs = Object.assign({}, mock.doc().removedRefs);
      mock.doc().removedRefs[ref.id] = true;
      return { id: ref.id, removed: true };
    }

    r.add("POST", "/v1/library/people", "proposed", function (req) {
      var name = String((req.body || {}).name || "").trim();
      if (!name) M.fail(400, "usage", "Enter a name for this person.");
      var base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-") || "person";
      var id = base;
      for (var n = 2; Object.prototype.hasOwnProperty.call(mock.ctx().people, id); n++) id = base + "-" + n;
      mock.doc().people[id] = { name: name, note: "", generated: true };
      mock.save();
      return { id: id, name: name, files: [] };
    });

    /* Reserve the remaining outputs of accepted jobs too: a burst of
     * downloads must not each spend the same free space. */
    mock.preflight = function (job) {
      if (job.kind === "query") return;
      var t = mock.clock.now();
      var need = {};
      function add(plan, temp, fraction) {
        plan.outputs.forEach(function (o) {
          need[o.disk] = (need[o.disk] || 0) + o.gb * fraction;
        });
        if (temp) need[plan.tempDisk] = (need[plan.tempDisk] || 0) + plan.tempGB;
      }
      add(job.plan, true, 1);
      mock.doc().jobs.forEach(function (j) {
        if (mock.isDone(j)) return;
        add(j.plan, j.status === "queued", j.plan.mode === "net" ? 1 - mock.doneWork(j, t) / j.plan.work : 1);
      });
      if (job.plan.mode === "net") {
        var part = mock.doc().partials[job.plan.outputs[0].path];
        if (part && part.url === job.plan.url) need[part.disk] -= part.bytes / 1e9;
      }
      mock.disks().forEach(function (d) {
        if ((need[d.id] || 0) > d.free_gb) M.fail(409, "insufficient_space",
          "Not enough space on " + d.name + ": needs " + M.fmt.gb(need[d.id]) + " including queued work, with " + M.fmt.gb(Math.max(0, d.free_gb)) +
          " free. Free up disk space or choose another volume.");
      });
    };

    r.add("GET", "/v1/mock/settings", "mock", settings);
    r.add("PATCH", "/v1/mock/settings", "mock", function (req) {
      var b = req.body || {};
      if (b.workspace !== undefined && !String(b.workspace).trim()) M.fail(400, "usage", "Choose a workspace folder.");
      if (b.allowed_paths !== undefined && (!Array.isArray(b.allowed_paths) || b.allowed_paths.some(function (p) {
        return typeof p !== "string" || !p.trim();
      }))) M.fail(400, "usage", "allowed_paths must be a list of folder paths.");
      var s = settings();
      if (b.max_concurrent_jobs !== undefined && [1, 2].indexOf(b.max_concurrent_jobs) < 0) M.fail(400, "usage", "Allow one or two concurrent analysis jobs.");
      if (b.max_concurrent_jobs !== undefined && b.max_concurrent_jobs !== s.max_concurrent_jobs) mock.doc().freeAt = Math.max(mock.doc().freeAt, mock.clock.now());
      ["workspace", "output_directory", "allowed_paths", "phone_access", "max_concurrent_jobs", "prevent_sleep", "notifications", "menu_bar"].forEach(function (key) {
        if (b[key] !== undefined) s[key] = b[key];
      });
      mock.doc().settings = s;
      mock.save();
      return settings();
    });

    r.add("POST", "/v1/mock/approval", "mock", function (req) {
      var b = req.body || {};
      if (["ask", "decline", "allow"].indexOf(b.action) < 0) M.fail(400, "usage", "Use ask, decline or allow.");
      if (b.action === "allow" && b.resource === "clinvar") removeReference("clinvar");
      var s = settings();
      s.requests.push({ action: b.action, at: M.util.iso(mock.clock.now()), resource: b.resource || "start_jobs" });
      mock.doc().settings = s;
      mock.save();
      return { requests: s.requests };
    });

    r.add("DELETE", "/v1/references/{id}", "proposed", function (req) {
      var result = removeReference(req.params.id);
      mock.save();
      return result;
    });

    /* Reproducible empty/space cases, without editing browser internals. */
    r.add("POST", "/v1/mock/scenario", "mock", function (req) {
      var name = (req.body || {}).name;
      if (["empty-jobs", "empty-library", "disk-full", "restore-space"].indexOf(name) < 0) M.fail(400, "usage", "Unknown review scenario.");
      if (name === "empty-jobs" || name === "empty-library") {
        mock.doc().jobs = [];
        mock.doc().partials = {};
        mock.doc().freeAt = mock.doc().netFreeAt = 0;
      }
      if (name === "empty-library") {
        mock.doc().added = {};
        mock.doc().emptyLibrary = true;
        Object.keys(mock.ctx().people).forEach(function (id) {
          mock.doc().people[id] = Object.assign({}, mock.doc().people[id], { removed: true });
        });
      }
      if (name === "disk-full") {
        mock.doc().diskFill = {};
        mock.disks().forEach(function (d) {
          if (!(req.body || {}).disk || req.body.disk === d.id) mock.doc().diskFill[d.id] = Math.max(0, d.free_gb - 0.01);
        });
      }
      if (name === "restore-space") mock.doc().diskFill = {};
      mock.save();
      return { name: name };
    });
  });
})(typeof globalThis !== "undefined" ? globalThis : this);
