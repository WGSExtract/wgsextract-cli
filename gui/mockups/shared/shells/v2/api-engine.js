/* Mock server core, part 4: the job engine. One analysis job and up to
 * three downloads run beside quick queries. Jobs progress on the simulated
 * clock (analysis) or at the simulated network speed (downloads), finish,
 * fail when asked, cancel, and rerun with step resume. Downloads keep a
 * .partial bound to their URL and resume from its last byte. All state is
 * one JSON document in the injected storage. Environment-agnostic. */
(function (root) {
  "use strict";
  var M = root.WGSMMock;
  var U = M.util;
  var KEY = "v2-api";
  var VERSION = 4;
  var MAX_JOBS = 1;
  var MAX_DOWNLOADS = 3;
  var FINISHED = ["succeeded", "failed", "cancelled", "interrupted"];

  function clone(x) {
    return x === undefined ? undefined : JSON.parse(JSON.stringify(x));
  }

  /* opts: {storage, realNow, machineKey (browser: the top bar's choice),
   * idleMs, speed} */
  M.createMock = function (config, opts) {
    var storage = opts.storage;
    var clock = M.createClock({ storage: storage, realNow: opts.realNow, start: M.seedStart, speed: opts.speed, idleMs: opts.idleMs });
    var doc = null;
    var mock = { config: config, clock: clock, router: M.createRouter(), finished: FINISHED };

    function save() {
      doc.rev += 1;
      storage.set(KEY, JSON.stringify(doc));
    }

    /* The machine, its files and the people as they are now */
    function ctx() {
      var machine = doc.machine ? M.machineFrom(config, doc.machine, doc.machineCustom) : M.machineFrom(config, opts.machineKey, opts.machineCustom);
      if (doc.settings && doc.settings.workspace) machine.workspace = doc.settings.workspace;
      var people = {};
      Object.keys(config.people).concat(Object.keys(doc.people)).forEach(function (id) {
        people[id] = Object.assign({ name: id, note: "" }, config.people[id], doc.people[id]);
      });
      /* Seed files, then files added since (api-files.js) */
      var files = { files: Object.assign({}, doc.emptyLibrary ? {} : config.files, doc.added) };
      return { machine: machine, files: M.resolveFiles(files, machine), people: people };
    }

    function rate(job) {
      if (job.plan.mode === "cpu") return 1;
      return doc.net.bps ? doc.net.bps / 1000 / Math.max(1, downloads().length) : Infinity;
    }

    function downloads() {
      return doc.jobs.filter(function (j) {
        return j.status === "running" && j.plan.mode === "net";
      });
    }

    /* Freeze work at the old shared rate before changing the active set. */
    function rebase(t) {
      downloads().forEach(function (j) {
        j.segWork = doneWork(j, t) - j.skipWork;
        j.segAt = t;
        j.segments.push({ at: t, work: j.segWork });
      });
    }

    function release(job, t) {
      if (job.kind !== "job") return;
      var key = job.plan.mode === "net" ? "netFreeAt" : "freeAt";
      doc[key] = Math.max(doc[key], t);
    }

    /* Work done (CPU: simulated ms; downloads: bytes), resumed part included */
    function doneWork(job, t) {
      if (job.status !== "running") return job.stopWork !== null ? job.stopWork : job.status === "succeeded" ? job.plan.work : 0;
      var r = rate(job);
      var left = job.plan.work - job.skipWork;
      var d = r === Infinity ? left : job.segWork + Math.max(0, t - job.segAt) * r;
      return job.skipWork + Math.min(left, d);
    }

    function endAt(job) {
      var left = job.plan.work - job.skipWork;
      var target = job.plan.fail ? Math.max(0, Math.floor(left * job.plan.fail.at)) : left;
      var r = rate(job);
      if (r === Infinity) return job.segAt;
      return job.segAt + Math.max(0, target - job.segWork) / r;
    }

    function find(id) {
      var job = doc.jobs.filter(function (j) {
        return j.id === id;
      })[0];
      if (!job) M.fail(404, "unknown_job", "No job " + JSON.stringify(id) + ".");
      return job;
    }

    function make(op, params, fields) {
      var p = clone(params) || {};
      if (fields.mock) p._mock = fields.mock;
      var c = ctx();
      var desc = M.ops.describe(op);
      if (!desc) M.fail(404, "unknown_op", "No operation " + JSON.stringify(op) + ".");
      var job = {
        id: U.jobId(fields.created), op: op, kind: desc.kind, params: p, plan: M.plan(op, p, c, c.machine),
        created: fields.created, started: null, finished: null, status: "queued", exit_code: null, error: null,
        skipWork: 0, segWork: 0, segAt: null, segments: [], stopWork: null, rerunOf: null, pre: false, note: null, resumed: 0,
      };
      Object.keys(fields).forEach(function (k) {
        if (k !== "mock") job[k] = fields[k];
      });
      if (job.started !== null) job.segAt = job.started;
      if (p.dry_run) {
        job.kind = "query";
        job.plan = M.dryRun(job.plan, op, p, doc.jobs, c.machine);
      }
      if (job.status === "succeeded") job.exit_code = 0;
      if (job.status === "failed") job.exit_code = 1;
      if (job.status === "interrupted" || job.status === "cancelled") keepPartial(job);
      return job;
    }

    function keepPartial(job) {
      if (job.plan.mode !== "net" || !job.stopWork) return;
      var out = job.plan.outputs[0];
      doc.partials[out.path] = { url: job.plan.url, bytes: job.stopWork, disk: out.disk, job: job.id };
    }

    function start(job, t) {
      if (job.plan.mode === "net") rebase(t);
      job.status = "running";
      job.started = t;
      job.segAt = t;
      job.segWork = 0;
      job.segments = [{ at: t, work: 0 }];
      if (job.plan.mode !== "net") return;
      var path = job.plan.outputs[0].path;
      var part = doc.partials[path];
      if (!part) return;
      delete doc.partials[path];
      if (part.url === job.plan.url) {
        job.skipWork = Math.min(part.bytes, job.plan.work);
        job.resumed = job.skipWork;
      } else {
        job.note = "Started over: " + path + ".partial was for another URL.";
      }
    }

    function finish(job, t) {
      if (job.plan.mode === "net") rebase(t);
      job.finished = t;
      release(job, t);
      if (job.plan.fail) {
        job.stopWork = doneWork(job, t);
        job.status = "failed";
        job.exit_code = 1;
        job.error = { kind: "failure", message: job.plan.fail.message };
        keepPartial(job);
        return;
      }
      job.status = "succeeded";
      job.exit_code = 0;
      job.stopWork = job.plan.work;
      config.references.forEach(function (ref) {
        var flag = ref.flag || "ref-" + ref.id;
        if (doc.removedRefs && doc.removedRefs[ref.id] && job.plan.flags.indexOf(flag) >= 0) {
          delete doc.flags[flag];
          delete doc.removedRefs[ref.id];
        }
      });
      if (job.plan.thenRerun) {
        try {
          rerun(job.plan.thenRerun, t);
        } catch (err) {
          if (err.code !== "insufficient_space") throw err;
          find(job.plan.thenRerun).error = { kind: "failure", message: "The fix finished, but the job could not restart. " + err.message };
          job.plan.thenRerun = null;
        }
      }
    }

    function stop(job, t, status, message) {
      var was = job.status;
      if (was === "running" && job.plan.mode === "net") rebase(t);
      job.stopWork = was === "running" ? doneWork(job, t) : 0;
      job.status = status;
      job.finished = t;
      job.exit_code = status === "cancelled" ? 130 : null;
      job.error = { kind: "cancelled", message: message };
      if (was === "running") release(job, t);
      keepPartial(job);
    }

    /* Catch up to now: finish jobs whose time has come, start the next */
    function advance(now) {
      var changed = false;
      for (;;) {
        var running = doc.jobs.filter(function (j) {
          return j.status === "running";
        });
        var next = null;
        var nextEnd = Infinity;
        running.forEach(function (j) {
          var e = endAt(j);
          if (e <= now && e < nextEnd) {
            next = j;
            nextEnd = e;
          }
        });
        var busy = running.filter(function (j) {
          return j.kind === "job" && j.plan.mode === "cpu";
        }).length;
        var queued = null;
        var startAt = Infinity;
        doc.jobs.forEach(function (j) {
          if (j.status !== "queued") return;
          var net = j.plan.mode === "net";
          var max = doc.settings && doc.settings.max_concurrent_jobs || MAX_JOBS;
          if (j.kind === "job" && (net ? downloads().length >= MAX_DOWNLOADS : busy >= max)) return;
          var t = j.kind === "query" ? j.created : Math.max(j.created, net ? doc.netFreeAt : doc.freeAt);
          if (t <= now && t < startAt) {
            queued = j;
            startAt = t;
          }
        });
        if (next && nextEnd <= startAt) {
          finish(next, Math.max(nextEnd, next.started));
          changed = true;
          continue;
        }
        if (!queued) break;
        start(queued, startAt);
        changed = true;
      }
      if (changed) save();
      return changed;
    }

    function submit(op, params, now) {
      var job = make(op, params, { created: now });
      if (mock.preflight) mock.preflight(job);
      doc.jobs.push(job);
      advance(now);
      save();
      return job;
    }

    /* A new job with the same parameters. A stopped CPU job resumes after
     * its last finished step; a download resumes from its .partial. */
    function rerun(id, t) {
      var src = find(id);
      if (!mock.isDone(src)) M.fail(409, "active_job", "Cancel this job before running it again.");
      var params = clone(src.params);
      if (params._mock) delete params._mock.fail;
      var job = make(src.op, params, { created: t });
      if (mock.preflight) mock.preflight(job);
      job.rerunOf = src.id;
      if (job.plan.mode === "cpu" && src.stopWork) {
        var sum = 0;
        var open = true;
        job.plan.steps.forEach(function (s) {
          if (open && sum + s.work <= src.stopWork) sum += s.work;
          else open = false;
        });
        job.skipWork = sum;
      }
      doc.jobs.push(job);
      advance(t);
      save();
      return job;
    }

    /* Disk use in GB by disk id: finished outputs, running jobs' temporary
     * space, growing downloads and kept .partial files */
    function used(t) {
      var u = { system: 0, genomes: 0 };
      doc.jobs.forEach(function (j) {
        if (j.status === "succeeded" && !j.pre) j.plan.outputs.forEach(function (o) {
          u[o.disk] = (u[o.disk] || 0) + o.gb;
        });
        if (j.status !== "running") return;
        if (j.plan.mode === "net") u[j.plan.outputs[0].disk] = (u[j.plan.outputs[0].disk] || 0) + doneWork(j, t) / 1e9;
        else u[j.plan.tempDisk] = (u[j.plan.tempDisk] || 0) + j.plan.tempGB;
      });
      Object.keys(doc.partials).forEach(function (k) {
        u[doc.partials[k].disk] = (u[doc.partials[k].disk] || 0) + doc.partials[k].bytes / 1e9;
      });
      Object.keys(doc.added || {}).forEach(function (k) {
        var d = doc.added[k].disk;
        u[d] = (u[d] || 0) + doc.added[k].size;
      });
      return u;
    }

    function seed() {
      clock.reset();
      var now = clock.now();
      doc = { v: VERSION, rev: 0, machine: null, people: {}, assign: {}, flags: {}, net: { bps: 1e9 }, partials: {}, jobs: [], freeAt: 0, netFreeAt: 0 };
      doc.jobs = M.seedJobs(now, ctx().files, make);
      doc.seedUsed = used(now);
      save();
    }

    function load() {
      doc = U.readJson(storage, KEY);
      if (!doc || doc.v !== VERSION) seed();
    }

    Object.assign(mock, {
      doc: function () {
        return doc;
      },
      save: save,
      ctx: ctx,
      find: find,
      submit: submit,
      rerun: rerun,
      doneWork: doneWork,
      endAt: endAt,
      rate: rate,
      maxDownloads: MAX_DOWNLOADS,
      used: used,
      isDone: function (job) {
        return FINISHED.indexOf(job.status) >= 0;
      },
      /* Every request and timer tick: the time now, jobs caught up */
      tick: function () {
        var now = clock.now();
        advance(now);
        return now;
      },
      cancel: function (id, now) {
        var job = find(id);
        if (mock.isDone(job)) M.fail(409, "finished", "Job " + id + " already finished (" + job.status + ").");
        stop(job, now, "cancelled", "Cancelled.");
        advance(now);
        save();
        return job;
      },
      /* Mock-only: as if the server stopped while jobs were running */
      interrupt: function (now) {
        doc.jobs.forEach(function (j) {
          if (!mock.isDone(j)) stop(j, now, "interrupted", "The server stopped before the job finished.");
        });
        save();
      },
      setNetwork: function (bps, now) {
        rebase(now);
        doc.net.bps = Math.max(0, Number(bps) || 0);
        advance(now);
        save();
      },
      /* Capabilities: from finished jobs, then explicit overrides */
      flags: function () {
        var on = {};
        doc.jobs.forEach(function (j) {
          var list = j.status === "succeeded" ? j.plan.flags : j.status === "cancelled" ? j.plan.cancelFlags : [];
          list.forEach(function (f) {
            on[f] = true;
          });
        });
        Object.keys(doc.flags).forEach(function (f) {
          if (doc.flags[f]) on[f] = true;
          else delete on[f];
        });
        return on;
      },
      reset: seed,
      /* Another tab or process changed the stored state */
      reload: function () {
        clock.reload();
        load();
      },
    });
    load();
    M.routes.forEach(function (register) {
      register(mock);
    });
    /* Every request first catches the jobs up to the simulated now */
    mock.handle = function (method, url, body) {
      mock.tick();
      return mock.router.handle(method, url, clone(body));
    };
    return mock;
  };

  /* Route modules push register(mock) functions here */
  M.routes = M.routes || [];
})(typeof globalThis !== "undefined" ? globalThis : this);
