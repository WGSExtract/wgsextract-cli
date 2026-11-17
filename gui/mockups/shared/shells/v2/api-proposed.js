/* Mock server core, part 7: PROPOSED routes. The UI needs these and the
 * real server (docs/server.md) doesn't have them yet; each is "PROPOSED —
 * mock only" until it is built there. Shapes are in MOCK_API.md.
 * Environment-agnostic. */
(function (root) {
  "use strict";

  /* Own keys only: ids like "constructor" mustn't match Object's members */
  function hasOwn(table, key) {
    return Object.prototype.hasOwnProperty.call(table, key);
  }
  var M = root.WGSMMock;
  var U = M.util;

  function round(n, d) {
    var f = Math.pow(10, d === undefined ? 1 : d);
    return Math.round(n * f) / f;
  }

  M.routes.push(function (mock) {
    var r = mock.router;
    function now() {
      return mock.clock.now();
    }

    function disks() {
      var m = mock.ctx().machine;
      var t = now();
      var used = mock.used(t);
      var seed = mock.doc().seedUsed;
      var temp = { system: 0, genomes: 0 };
      mock.doc().jobs.forEach(function (j) {
        if (j.status !== "running") return;
        if (j.plan.mode === "net") temp[j.plan.outputs[0].disk] += mock.doneWork(j, t) / 1e9;
        else temp[j.plan.tempDisk] += j.plan.tempGB;
      });
      return m.disks.map(function (d) {
        var free = d.base + seed[d.id] - used[d.id];
        return { id: d.id, name: d.name, path: d.path, total_gb: d.total, free_gb: round(free), held_gb: round(temp[d.id]), system: d.id === "system" };
      });
    }

    /* PROPOSED — mock only. The computer: OS, CPU, memory, GPU */
    r.add("GET", "/v1/system", "proposed", function () {
      var m = mock.ctx().machine;
      return { os: m.os, arch: m.arch, label: m.label, spec: m.spec, cores: m.cores, performance_cores: m.bigCores, ram_gb: m.ramGB, gpu: m.gpu, workspace: m.workspace, profile: m.key };
    });

    /* PROPOSED — mock only. Disks with live free space: finished outputs
     * take space, running jobs hold temporary space, downloads grow */
    r.add("GET", "/v1/disks", "proposed", function () {
      return { disks: disks() };
    });

    function fileView(f, status, extra) {
      return Object.assign({ id: f.id, name: f.name, path: f.path, tag: f.tag, kind: f.kind, build: f.build || null, ref: f.ref || null, depth: f.depth || null,
        size_gb: f.size, disk: M.diskFor(mock.ctx().machine, f.disk).name, status: status }, extra);
    }

    function owner(f) {
      return mock.doc().assign[f.id] || f.person;
    }

    function library() {
      var c = mock.ctx();
      var flags = mock.flags();
      var t = now();
      var people = Object.keys(c.people).map(function (id) {
        return { id: id, name: c.people[id].name, note: c.people[id].note, removed: !!c.people[id].removed, files: [] };
      });
      var byId = {};
      people.forEach(function (p) {
        byId[p.id] = p;
      });
      var unassigned = [];
      Object.keys(c.files).forEach(function (k) {
        var f = Object.assign({ id: k }, c.files[k]);
        var v = fileView(f, f.needs && !flags[f.needs] ? "needs_fix" : "ready", f.needs ? { needs: f.needs } : {});
        var who = owner(f);
        (who && hasOwn(byId, who) ? byId[who].files : unassigned).push(v);
      });
      /* Files jobs are making or have made */
      mock.doc().jobs.forEach(function (j) {
        var p = hasOwn(byId, j.plan.person) ? byId[j.plan.person] : null;
        if (!p || ["queued", "running", "succeeded"].indexOf(j.status) < 0) return;
        j.plan.outputs.filter(function (o) {
          return o.library;
        }).forEach(function (o) {
          var name = o.path.split(/[\\/]/).pop();
          p.files.push({ id: j.id + ":" + name, name: name, path: o.path, tag: o.kind.toUpperCase(), kind: "reads", size_gb: o.gb,
            disk: M.diskFor(c.machine, o.disk).name, status: j.status === "succeeded" ? "ready" : "being_made", job: j.id,
            fraction: round(mock.doneWork(j, t) / j.plan.work, 4) });
        });
      });
      return { profile: c.machine.key, people: people, unassigned: unassigned };
    }

    /* PROPOSED — mock only. People with their files (DESIGN.md §11:
     * "the library index groups files by person") */
    r.add("GET", "/v1/library", "proposed", library);

    /* PROPOSED — mock only. Rename a person or change their note */
    r.add("PATCH", "/v1/library/people/{id}", "proposed", function (req) {
      var c = mock.ctx();
      var id = req.params.id;
      if (!hasOwn(c.people, id)) M.fail(404, "unknown_person", "No person " + JSON.stringify(id) + ".");
      var b = req.body || {};
      if (b.name !== undefined && !String(b.name).trim()) M.fail(400, "usage", "A person needs a name.");
      var own = (mock.doc().people[id] = Object.assign({}, mock.doc().people[id]));
      ["name", "note", "removed"].forEach(function (k) {
        if (b[k] !== undefined) own[k] = k === "removed" ? !!b[k] : String(b[k]).trim();
      });
      mock.save();
      return library().people.filter(function (p) {
        return p.id === id;
      })[0];
    });

    /* PROPOSED — mock only. Remove a person from the library; their files
     * stay on disk */
    r.add("DELETE", "/v1/library/people/{id}", "proposed", function (req) {
      if (!hasOwn(mock.ctx().people, req.params.id)) M.fail(404, "unknown_person", "No person " + JSON.stringify(req.params.id) + ".");
      mock.doc().people[req.params.id] = Object.assign({}, mock.doc().people[req.params.id], { removed: true });
      mock.save();
      return { id: req.params.id, removed: true, files_kept: true };
    });

    /* PROPOSED — mock only. Whose a file is: {"person": id} or
     * {"name": "New person"} */
    r.add("POST", "/v1/library/files/{id}/assign", "proposed", function (req) {
      var c = mock.ctx();
      if (!hasOwn(c.files, req.params.id)) M.fail(404, "unknown_file", "No file " + JSON.stringify(req.params.id) + ".");
      var b = req.body || {};
      var id = b.person;
      if (!id && b.name) {
        id = String(b.name).trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "person";
        if (!hasOwn(c.people, id)) mock.doc().people[id] = { name: String(b.name).trim(), note: "", added: true };
      }
      if (!id || !hasOwn(mock.ctx().people, id)) M.fail(400, "usage", "Give an existing person id or a new name.");
      mock.doc().assign[req.params.id] = id;
      mock.save();
      return library();
    });

    /* PROPOSED — mock only. A preflight estimate for op + params: time or
     * download size, memory, disk, and when it would start. Takes the UI's
     * own numbers from "_mock" when given. */
    r.add("POST", "/v1/estimate", "proposed", function (req) {
      var b = req.body || {};
      var c = mock.ctx();
      var plan = M.plan(b.op, b.params || {}, c, c.machine);
      var bps = mock.doc().net.bps;
      var sharing = Math.min(mock.maxDownloads, 1 + mock.doc().jobs.filter(function (j) {
        return j.status === "running" && j.plan.mode === "net";
      }).length);
      var ms = plan.mode === "cpu" ? plan.work : bps ? plan.work * sharing / (bps / 1000) : 0;
      var t = now();
      var forecast = M.schedule(mock, t, { id: "estimate", kind: M.ops.describe(b.op).kind, status: "queued", plan: plan });
      var startAt = forecast.estimate ? forecast.estimate.starts_at : t;
      var free = {};
      disks().forEach(function (d) {
        free[d.id] = d;
      });
      var need = {};
      plan.outputs.forEach(function (o) {
        need[o.disk] = (need[o.disk] || 0) + o.gb;
      });
      if (plan.tempGB) need[plan.tempDisk] = (need[plan.tempDisk] || 0) + plan.tempGB;
      return {
        op: b.op, label: plan.label, mode: plan.mode, duration_s: Math.round(ms / 1000), download_bytes: plan.mode === "net" ? plan.work : 0,
        memory_gb: plan.memGB, ram_gb: c.machine.ramGB, fits_memory: plan.memGB <= c.machine.ramGB,
        steps: plan.steps.map(function (s) {
          return { name: s.name, share: round(s.work / plan.work, 3) };
        }),
        disk: Object.keys(need).map(function (id) {
          return { disk: free[id].name, path: free[id].path, need_gb: round(need[id]), temp_gb: id === plan.tempDisk ? plan.tempGB : 0, free_gb: free[id].free_gb, fits: need[id] <= free[id].free_gb };
        }),
        starts_at: U.iso(startAt), starts_in_s: Math.round((startAt - t) / 1000), finishes_at: U.iso(startAt + ms),
      };
    });

    /* PROPOSED — mock only. Installed reference genomes and data, with
     * downloads in progress and kept partial files */
    r.add("GET", "/v1/references", "proposed", function () {
      var flags = mock.flags();
      var t = now();
      var doc = mock.doc();
      return { references: mock.config.references.map(function (ref) {
        var flag = ref.flag || "ref-" + ref.id;
        var out = { id: ref.id, name: ref.name, build: ref.build || null, kind: ref.kind, gb: ref.gb, use: ref.use, installed: !!(ref.installed || flags[flag]), flag: flag };
        if (ref.kind === "genome") out.indexes = { fasta: out.installed, bwa: !!ref.installed, "bwa-mem2": !!flags["mem2-" + ref.id] };
        var job = doc.jobs.filter(function (j) {
          return j.status === "running" && (j.plan.flags.indexOf(flag) >= 0 || j.plan.flags.indexOf("mem2-" + ref.id) >= 0);
        })[0];
        if (job) out.downloading = { job: job.id, fraction: round(mock.doneWork(job, t) / job.plan.work, 4) };
        Object.keys(doc.partials).forEach(function (path) {
          if (path.indexOf("/" + ref.id + "/") >= 0) out.partial = { path: path + ".partial", bytes: doc.partials[path].bytes };
        });
        return out;
      }) };
    });
  });
})(typeof globalThis !== "undefined" ? globalThis : this);
