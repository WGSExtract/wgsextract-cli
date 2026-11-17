/* WGSM.v2bind: static markup that follows live jobs, with no page script.
 *
 *   data-v2-job="realign:gordon"   the newest job for op + person ("id:…"
 *                                  for one job; "label:…" by its label;
 *                                  "running" prefers analysis over downloads)
 *     data-v2-job-text[="full|eta|percent|status|label"]
 *                                  "47% · 1 h 52 min left", "Queued · starts
 *                                  in 1 h 52 min", "Done", "Interrupted at
 *                                  62%" (full); eta: "1 h 52 min left"
 *     data-v2-job-bar              a .v2-bar (or anything using --v):
 *                                  --v, aria-valuenow, data-state="stopped"
 *     data-v2-job-when="running queued"   shown only in those states; also
 *                                  done (succeeded), stopped (failed,
 *                                  cancelled, interrupted), active (running,
 *                                  queued), none (no such job)
 *   The data-v2-job element gets data-v2-job-status="running" etc.
 *
 * Starts by itself; start() refreshes now. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var api = WGSM.v2api;
  var F = window.WGSMMock.fmt;
  var GROUPS = { done: ["succeeded"], stopped: ["failed", "cancelled", "interrupted"], active: ["running", "queued"] };

  function pick(jobs, match) {
    var hits = jobs.filter(function (j) {
      if (match === "running") return j.status === "running" && j.kind === "job";
      if (match.indexOf("id:") === 0) return j.id === match.slice(3);
      if (match.indexOf("label:") === 0) return j.ui.label === match.slice(6);
      var at = match.lastIndexOf(":");
      return j.op === (at < 0 ? match : match.slice(0, at)) && (at < 0 || j.ui.person === match.slice(at + 1));
    });
    if (match === "running") {
      var cpu = hits.filter(function (j) { return j.ui.mode === "cpu"; });
      if (cpu.length) return cpu[0];
    }
    return hits[hits.length - 1] || null;
  }

  function percent(job) {
    return Math.floor(((job.progress && job.progress.fraction) || 0) * 100);
  }

  function eta(job) {
    return job.status === "running" ? F.dur(job.progress.eta_s * 1000) + " left" : "";
  }

  function text(job, how) {
    if (!job) return "";
    if (how === "eta") return eta(job);
    if (how === "percent") return percent(job) + "%";
    if (how === "label") return job.ui.label;
    var s = job.status;
    if (how === "status") return { running: "Running", queued: "Queued", succeeded: "Done", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted" }[s];
    if (s === "running") return percent(job) + "% · " + eta(job);
    if (s === "queued") return job.ui.waiting && job.ui.waiting.starts_in_s > 0 ? "Queued · starts in " + F.dur(job.ui.waiting.starts_in_s * 1000) : "Queued";
    if (s === "succeeded") return "Done";
    if (s === "failed") return "Failed after " + F.dur(job.ui.took_s * 1000);
    if (s === "interrupted") return "Interrupted at " + percent(job) + "%";
    return "Cancelled";
  }

  function shows(job, when) {
    return when.split(/\s+/).some(function (w) {
      if (w === "none") return !job;
      return !!job && (w === job.status || (GROUPS[w] || []).indexOf(job.status) >= 0);
    });
  }

  function bar(el, job) {
    var v = job ? (job.status === "succeeded" ? 100 : percent(job)) : 0;
    var fill = el.matches(".v2-bar") ? el.firstElementChild || el : el;
    fill.style.setProperty("--v", v + "%");
    var aria = el.getAttribute("role") === "progressbar" ? el : el.closest('[role="progressbar"]');
    if (aria) aria.setAttribute("aria-valuenow", String(v));
    if (job && GROUPS.stopped.indexOf(job.status) >= 0) el.dataset.state = "stopped";
    else if (el.dataset.state === "stopped") delete el.dataset.state;
  }

  function apply(scope, job) {
    scope.dataset.v2JobStatus = job ? job.status : "none";
    WGSM.qsa("[data-v2-job-text]", scope).forEach(function (el) {
      var t = text(job, el.dataset.v2JobText || "full");
      if (el.textContent !== t) el.textContent = t;
    });
    WGSM.qsa("[data-v2-job-bar]", scope).forEach(function (el) {
      bar(el, job);
    });
    /* The scope itself may say when it shows */
    WGSM.qsa("[data-v2-job-when]", scope).concat(scope.matches("[data-v2-job-when]") ? [scope] : []).forEach(function (el) {
      el.hidden = !shows(job, el.dataset.v2JobWhen);
    });
  }

  function start() {
    var scopes = WGSM.qsa("[data-v2-job]");
    if (!scopes.length) return Promise.resolve();
    return api.get("/v1/jobs?detail=1&full=1").then(function (res) {
      if (res.status !== 200) return;
      scopes.forEach(function (scope) {
        apply(scope, pick(res.body.jobs, scope.dataset.v2Job));
      });
    });
  }

  api.on(start);
  WGSM.v2bind = { start: start, pick: pick, text: text };
})();
