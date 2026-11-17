/* Mock server core, part 5: how a job looks from outside. The real job
 * view (docs/server.md: id, op, kind, status, created, started, finished,
 * exit_code, error, progress {stage, message, fraction}, result) plus a
 * PROPOSED "ui" object with what the app shows: label, person, steps, ETA,
 * why it waits. Also the job log, its result manifest and the command
 * line. Environment-agnostic. */
(function (root) {
  "use strict";
  var M = root.WGSMMock;
  var U = M.util;
  var F = M.fmt;

  function num(n) {
    return Math.round(n).toLocaleString("en");
  }

  function command(job) {
    var parts = ["wgsextract"].concat(job.op.split("/"));
    Object.keys(job.params).forEach(function (k) {
      var v = job.params[k];
      if (k === "_mock" || v === false || v === null || v === undefined) return;
      parts.push("--" + k.replace(/_/g, "-"));
      if (v !== true) parts.push(Array.isArray(v) ? v.join(",") : String(v));
    });
    return parts.map(function (p) {
      return /[\s'"]/.test(p) ? JSON.stringify(p) : p;
    }).join(" ");
  }

  /* Simulated time at which `work` was (or will be) reached */
  function timeAt(mock, job, work) {
    var w = work - job.skipWork;
    if (w <= 0) return job.started;
    if (job.plan.mode === "net" && job.segments.length) {
      var points = job.finished === null ? job.segments : job.segments.concat([{ at: job.finished, work: job.stopWork - job.skipWork }]);
      for (var i = 1; i < points.length; i += 1) {
        if (w > points[i].work || points[i].work <= points[i - 1].work) continue;
        return points[i - 1].at + (points[i].at - points[i - 1].at) * (w - points[i - 1].work) / (points[i].work - points[i - 1].work);
      }
    }
    if (job.finished !== null && job.stopWork > job.skipWork) return job.started + (job.finished - job.started) * Math.min(1, w / (job.stopWork - job.skipWork));
    var rate = mock.rate(job);
    if (w <= job.segWork) return job.started + (job.segAt - job.started) * (job.segWork ? w / job.segWork : 1);
    return rate === Infinity ? job.segAt : job.segAt + (w - job.segWork) / rate;
  }

  /* Each step with its state and fraction, and the current one */
  function steps(mock, job, now) {
    var done = mock.doneWork(job, now);
    var stopped = ["failed", "cancelled", "interrupted"].indexOf(job.status) >= 0;
    var cum = 0;
    var current = null;
    var list = job.plan.steps.map(function (s) {
      var from = cum;
      cum += s.work;
      var f = s.work ? Math.max(0, Math.min(1, (done - from) / s.work)) : 1;
      var state = "waiting";
      if (cum <= job.skipWork && job.skipWork) state = "skipped";
      else if (f >= 1 && (job.status !== "running" || done >= cum)) state = "done";
      else if (job.status === "running" && done >= from) state = "running";
      else if (stopped && done >= from && f < 1) state = "stopped";
      if (job.status === "queued") state = "waiting";
      var out = { name: s.name, weight: s.work, state: state, fraction: +f.toFixed(4) };
      if (s.unit === "reads" && f > 0 && f < 1) out.detail = num(f * s.total) + " of " + num(s.total) + " reads";
      if (state === "running" || state === "stopped") current = out;
      return out;
    });
    return { list: list, current: current, done: done };
  }

  /* Forecast each lane independently, including future bandwidth sharing. */
  function schedule(mock, now, extra) {
    var doc = mock.doc();
    var rate = doc.net.bps ? doc.net.bps / 1000 : Infinity;
    var t = now;
    var head = null;
    var jobs = doc.jobs.concat(extra ? [extra] : []);
    var active = [];
    var queue = [];
    var out = {};
    function remaining(j) {
      var part = doc.partials[j.plan.outputs[0].path];
      var skip = j.status === "running" ? j.skipWork : part && part.url === j.plan.url ? part.bytes : 0;
      var left = j.plan.work - skip;
      var target = j.plan.fail ? Math.floor(left * j.plan.fail.at) : left;
      return Math.max(0, target - (j.status === "running" ? mock.doneWork(j, now) - skip : 0));
    }
    jobs.forEach(function (j) {
      if (j.kind !== "job") return;
      if (j.plan.mode === "net") {
        if (j.status === "running") active.push({ id: j.id, left: remaining(j) });
        if (j.status === "queued") queue.push(j);
      } else if (j.status === "running") {
        t = Math.max(t, mock.endAt(j));
        head = j.id;
      }
    });
    jobs.forEach(function (j) {
      if (j.status !== "queued" || j.kind !== "job" || j.plan.mode !== "cpu") return;
      out[j.id] = { starts_at: t, behind: head };
      t += j.plan.work - (j.skipWork || 0);
      head = j.id;
    });
    t = now;
    head = null;
    while (queue.length) {
      while (queue.length && active.length < mock.maxDownloads) {
        var j = queue.shift();
        out[j.id] = { starts_at: t, behind: head };
        active.push({ id: j.id, left: remaining(j) });
      }
      if (!queue.length) break;
      active.sort(function (a, b) { return a.left - b.left; });
      var work = active[0].left;
      t += rate === Infinity ? 0 : work * active.length / rate;
      active.forEach(function (a) { a.left = Math.max(0, a.left - work); });
      head = active.shift().id;
    }
    return out;
  }
  M.schedule = schedule;

  function progress(mock, job, now, st) {
    var p = job.plan;
    var fraction = +(st.done / p.work || 0).toFixed(4);
    var cur = st.current || st.list[st.list.length - 1];
    var out = { stage: cur ? cur.name : null, message: cur && cur.detail ? cur.detail : null, fraction: fraction };
    if (p.mode === "net") {
      var bps = mock.rate(job) * 1000;
      out.bytes_done = Math.round(st.done);
      out.bytes_total = p.work;
      out.rate_bps = job.status === "running" ? (isFinite(bps) ? bps : null) : 0;
      out.message = F.gb(st.done / 1e9) + " of " + F.gb(p.work / 1e9) + (job.status === "running" ? " at " + F.rate(isFinite(bps) ? bps : 0) : "");
    }
    if (job.status === "running") out.eta_s = Math.max(0, Math.round((mock.endAt(job) - now) / 1000));
    return out;
  }

  function manifest(mock, job, full) {
    if (job.status === "queued" || job.status === "running" || job.status === "interrupted") return null;
    var p = job.plan;
    var ok = job.status === "succeeded";
    var first = p.outputs[0] ? p.outputs[0].path.replace(/\.(gz|bgz|zst|bz2|xz)$/, "") + ".manifest.json" : null;
    var doc = {
      schema: "wgsextract.result/1",
      command: job.op.split("/").join(" "),
      status: ok ? "ok" : job.status === "cancelled" ? "cancelled" : "failed",
      exit_code: job.exit_code,
      error: job.error,
      inputs: p.inputs,
      outputs: p.outputs.map(function (o) {
        return { path: o.path, kind: o.kind, role: o.role, exists: ok, size_bytes: ok ? Math.round(o.gb * 1e9) : null, mtime: ok ? U.iso(job.finished) : null, fingerprint: null, index: null, metrics: {} };
      }),
      metrics: p.metrics,
      warnings: [],
      details: null,
      manifest: ok ? first : null,
    };
    if (full) {
      var params = Object.assign({}, job.params);
      delete params._mock;
      doc.provenance = { version: "mock", argv: command(job).split(" ").slice(1), params: params, tools: {}, started: U.iso(job.started), finished: U.iso(job.finished), elapsed_s: Math.round((job.finished - job.started) / 1000) };
    }
    return doc;
  }

  M.view = function (mock, job, now, opts) {
    var full = opts && opts.full;
    var c = mock.ctx();
    var v = { id: job.id, op: job.op, kind: job.kind, status: job.status, created: U.iso(job.created) };
    if (full) v.params = job.params;
    if (job.started !== null) v.started = U.iso(job.started);
    if (job.finished !== null) v.finished = U.iso(job.finished);
    if (job.exit_code !== null) v.exit_code = job.exit_code;
    if (job.error) v.error = job.error;
    var st = steps(mock, job, now);
    if (job.started !== null) v.progress = progress(mock, job, now, st);
    var result = manifest(mock, job, full);
    if (result) v.result = result;
    var person = job.plan.person && c.people[job.plan.person];
    var ui = {
      label: job.plan.label,
      person: job.plan.person,
      person_name: person ? person.name : null,
      mode: job.plan.mode,
      steps: st.list,
      outputs: job.plan.outputs.map(function (o) {
        return { path: o.path, gb: o.gb, kind: o.kind, disk: M.diskFor(c.machine, o.disk).name, library: !!o.library };
      }),
      temp_gb: job.plan.tempGB,
      mem_gb: job.plan.memGB,
      command: command(job),
      rerun_of: job.rerunOf,
      then_rerun: job.plan.thenRerun,
      resumed_bytes: job.resumed || 0,
      note: job.note,
    };
    if (job.plan.dryRun) ui.dry_run = job.plan.dryRun;
    if (job.status === "running") {
      ui.eta_s = v.progress.eta_s;
      ui.finishes_at = U.iso(mock.endAt(job));
    }
    if (job.finished !== null) ui.took_s = Math.round((job.finished - job.started) / 1000);
    if (job.status === "queued") {
      var s = schedule(mock, now)[job.id];
      ui.waiting = s ? { reason: job.plan.mode === "net" ? "download_slots" : "one_at_a_time", behind: s.behind, starts_at: U.iso(s.starts_at), starts_in_s: Math.round((s.starts_at - now) / 1000) } : { reason: "starting" };
      ui.est_s = Math.round((job.plan.mode === "cpu" ? job.plan.work : job.plan.work / ((mock.doc().net.bps || Infinity) / 1000)) / 1000);
    }
    if (job.status === "interrupted" || job.status === "cancelled" || job.status === "failed") {
      var resumeAt = st.list.filter(function (x) {
        return x.state === "stopped";
      })[0];
      ui.resume = job.plan.mode === "net"
        ? { from_bytes: job.stopWork || 0, partial: job.plan.outputs[0].path + ".partial" }
        : { from_step: resumeAt ? resumeAt.name : st.list[0].name };
    }
    v.ui = ui;
    return v;
  };

  /* The job's log up to now, with simulated timestamps */
  M.jobLog = function (mock, job, now) {
    var c = mock.ctx();
    var lines = [];
    var until = job.finished !== null ? job.finished : now;
    function line(t, text) {
      if (t <= until) lines.push(F.hms(t) + " " + text);
    }
    line(job.created, "Submitted " + job.op + (job.rerunOf ? " (rerun of " + job.rerunOf + ")" : ""));
    if (job.started === null) return lines;
    line(job.started, "Started: " + command(job));
    var p = job.plan;
    if (p.mode === "cpu") {
      line(job.started, "Preflight memory OK  " + p.memGB.toFixed(2) + " / " + c.machine.ramGB.toFixed(2) + " GiB");
      if (p.tempGB) line(job.started, "Preflight disk OK  " + p.tempGB.toFixed(2) + " GiB temporary at " + M.diskFor(c.machine, p.tempDisk).path);
    } else {
      line(job.started, "Downloading " + p.url + " to " + p.outputs[0].path + ".partial");
      if (job.resumed) line(job.started, "Resuming from byte " + num(job.resumed) + " (the .partial is bound to this URL)");
    }
    if (job.note) line(job.started, job.note);
    if (p.dryRun) {
      p.dryRun.steps.forEach(function (s) {
        line(job.started, "Dry run: step " + s.name + " would " + s.action + " (" + s.why + ")");
      });
      p.dryRun.notes.forEach(function (n) {
        line(job.started, "Dry run: " + n);
      });
    }
    var cum = 0;
    var done = mock.doneWork(job, now);
    p.steps.forEach(function (s) {
      var from = cum;
      cum += s.work;
      if (cum <= job.skipWork && job.skipWork) {
        line(job.started, "Step " + s.name + ": skipped, already done");
        return;
      }
      if (from > done || (from === done && job.status !== "running" && from > 0)) return;
      line(timeAt(mock, job, Math.max(from, job.skipWork)), "Step " + s.name + ": started");
      [0.25, 0.5, 0.75].forEach(function (q) {
        var w = from + s.work * q;
        if (w <= job.skipWork || w > done) return;
        var what = s.unit === "reads" ? num(q * s.total) + " of " + num(s.total) + " reads" : p.mode === "net" ? F.gb(w / 1e9) + " of " + F.gb(p.work / 1e9) : Math.round(q * 100) + "%";
        line(timeAt(mock, job, w), s.name + ": " + what);
      });
      if (done < cum) return;
      line(timeAt(mock, job, cum), "Step " + s.name + ": done");
    });
    if (job.finished === null) return lines;
    var took = F.dur(job.finished - job.started);
    var end = { succeeded: "Finished: succeeded in " + took, failed: "Failed after " + took + ": " + (job.error && job.error.message),
      cancelled: "Cancelled after " + took, interrupted: "Interrupted: " + (job.note || "the server stopped") }[job.status];
    lines.push(F.hms(job.finished) + " " + end);
    return lines;
  };

  M.command = command;
})(typeof globalThis !== "undefined" ? globalThis : this);
