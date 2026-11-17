/* The Jobs page's cards, built from job views (GET /v1/jobs?detail=1):
 * a running job with its steps, log and Cancel; a queued job and why it
 * waits; a job that needs you, with its fix or Resume; a finished job.
 * Each card is {el, update(job, jobs)} so a tick changes text and bars in
 * place and an open log stays open. Used by api-jobs.js. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var api = WGSM.v2api;
  var clock = WGSM.v2clock;
  var PERSON_PAGE = { reginald: "person.html", gordon: "person-gordon.html", helen: "person-helen.html", alex: "person-alex.html" };

  function ms(iso) {
    return iso ? Date.parse(iso) : null;
  }

  function first(job) {
    return job.ui.person_name ? WGSM.v2state.first(job.ui.person_name) : "";
  }

  function badge(job) {
    if (job.ui.mode === "net") return WGSM.icon("reference");
    if (job.ui.person_name) return h("span.v2-avatar", WGSM.v2state.initials(job.ui.person_name));
    return WGSM.icon("jobs");
  }

  /* "412 M of 876 M reads" from "411,632,097 of 876,231,560 reads" */
  function compact(text) {
    return String(text || "").replace(/\d{1,3}(,\d{3}){2,}/g, function (n) {
      return Math.round(Number(n.replace(/,/g, "")) / 1e6) + " M";
    });
  }

  function percent(job) {
    return Math.floor(((job.progress && job.progress.fraction) || 0) * 100);
  }

  function status(state, text) {
    return h("span.v2-status.v2-small", h("span.v2-pip", { dataset: { state: state } }), h("span", text));
  }

  function act(promise, ok) {
    return promise.then(function (res) {
      if (res.status >= 300) WGSM.toast(res.body.error ? res.body.error.message : "HTTP " + res.status, "circle-alert");
      else if (ok) WGSM.toast(ok);
      return api.refresh();
    });
  }

  /* The cancel dialog, one for the page */
  var dialog = null;
  function confirmCancel(job) {
    if (!dialog) {
      dialog = h("dialog.dialog", { id: "v2-job-cancel", "aria-labelledby": "v2-job-cancel-title" },
        h("div.dialog__header", h("h2.dialog__title", { id: "v2-job-cancel-title" })),
        h("div.dialog__body", h("p.v2-muted")),
        h("div.dialog__footer",
          h("button.v2-btn", { type: "button", "data-close": "" }, "Keep running"),
          h("button.v2-btn.v2-btn--danger", { type: "button", dataset: { v2Confirm: "" } })));
    }
    var step = job.ui.steps.filter(function (s) {
      return s.state === "running";
    })[0];
    var input = (job.params && job.params.input) || "";
    WGSM.qs(".dialog__title", dialog).textContent = "Cancel " + job.ui.label.charAt(0).toLowerCase() + job.ui.label.slice(1) + "?";
    WGSM.qs("p", dialog).textContent = job.ui.mode === "net"
      ? "The " + clock.gb(job.progress.bytes_done / 1e9) + " downloaded so far is kept as a .partial file; Resume continues from there."
      : (step ? "The work on " + step.name + " so far (" + clock.dur(step.fraction * step.weight) + ") is lost. " : "") +
        "Finished steps are kept, so running it again starts at " + (step ? step.name : "the same step") + "." +
        (input ? " " + input.split(/[\\/]/).pop() + " isn’t touched." : "");
    var yes = WGSM.qs("[data-v2-confirm]", dialog);
    yes.textContent = "Cancel the job";
    yes.onclick = function () {
      WGSM.ui.close(dialog);
      act(api.del("/v1/jobs/" + job.id), "Cancelled: " + job.ui.label);
    };
    WGSM.ui.open(dialog);
  }

  function stepLabel(s, job) {
    if (s.state === "done") return s.name + " · done";
    if (s.state === "skipped") return s.name + " · already done";
    if (s.state !== "running") return s.name;
    if (job.ui.mode === "net") return s.name + " · " + job.progress.message;
    return s.detail ? s.name + " · " + compact(s.detail) : s.name;
  }

  function running(job) {
    var text = h("span");
    var steps = h("div.v2-steps", { role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": job.ui.label },
      job.ui.steps.map(function (s) {
        return h("div.v2-step", { style: { "--w": String(s.weight) } }, h("span"), h("span.v2-bar", h("span")));
      }));
    var note = h("p.v2-small.v2-muted");
    var log = h("pre.v2-code");
    var more = h("details.v2-more", h("summary", "Log"),
      h("div.v2-more__body.v2-section", log, h("pre.v2-code.v2-cmd", job.ui.command)));
    function loadLog() {
      if (!more.open) return;
      api.get("/v1/jobs/" + job.id + "/log?tail=40").then(function (res) {
        if (typeof res.body === "string" && log.textContent !== res.body.trim()) log.textContent = res.body.trim();
      });
    }
    more.addEventListener("toggle", loadLog);
    var el = h("div.v2-sheet__pad.v2-section", { dataset: { jobId: job.id } },
      h("div.v2-row.v2-row--wrap", badge(job), h("strong.v2-grow", job.ui.label), h("span.v2-status.v2-small", h("span.v2-pip", { dataset: { state: "busy" } }), text)),
      steps, note, more,
      h("div.v2-row", h("button.v2-btn.v2-btn--sm.v2-btn--danger", { type: "button", onclick: function () {
        confirmCancel(current);
      } }, "Cancel")));
    var current = job;
    function update(j) {
      current = j;
      text.textContent = percent(j) + "% · " + clock.dur(j.progress.eta_s * 1000) + " left";
      steps.setAttribute("aria-valuenow", String(percent(j)));
      j.ui.steps.forEach(function (s, i) {
        var step = steps.children[i];
        step.dataset.state = s.state === "skipped" ? "done" : s.state;
        step.firstChild.textContent = stepLabel(s, j);
        step.title = stepLabel(s, j);
        step.querySelector(".v2-bar > span").style.setProperty("--v", Math.round(s.fraction * 100) + "%");
      });
      var at = j.ui.steps.map(function (s) {
        return s.state;
      }).indexOf("running");
      var lib = j.ui.outputs.filter(function (o) {
        return o.library;
      })[0];
      var input = ((j.params && j.params.input) || "").split(".").pop().toUpperCase();
      var parts = [];
      if (j.ui.mode === "net") {
        parts.push(j.progress.message + ". Started " + clock.format(ms(j.started)) + ".");
        if (j.ui.resumed_bytes) parts.push("Resumed from " + clock.gb(j.ui.resumed_bytes / 1e9) + " in the .partial file.");
      } else {
        parts.push("Step " + (at + 1) + " of " + j.ui.steps.length + ". Started " + clock.format(ms(j.started)) + ", done about " + clock.format(ms(j.ui.finishes_at)) + ".");
      }
      var tail = lib ? " When it’s done, " + first(j) + " gets a new file, " : "";
      note.replaceChildren(parts.join(" ") + tail, lib ? h("span.v2-mono", lib.path.split(/[\\/]/).pop()) : "",
        lib ? (input && input !== "CRAM" ? ", and the old " + input + " stays." : ".") : "");
      loadLog();
    }
    update(job);
    return { el: el, update: update };
  }

  function queued(job, jobs) {
    var why = h("p.v2-small.v2-muted");
    var el = h("div.v2-sheet__pad.v2-row.v2-row--wrap", { dataset: { jobId: job.id } }, badge(job),
      h("div.v2-grow", h("strong", job.ui.label), why),
      h("button.v2-btn.v2-btn--sm.v2-btn--quiet", { type: "button", onclick: function () {
        act(api.del("/v1/jobs/" + job.id), "Removed from the queue: " + job.ui.label);
      } }, job.ui.mode === "net" ? "Remove" : "Cancel"));
    function update(j, all) {
      var w = j.ui.waiting || {};
      var ahead = (all || []).filter(function (x) {
        return x.id === w.behind;
      })[0];
      var size = j.ui.mode === "net" ? "A " + clock.gb(j.ui.outputs[0].gb) + " download" : "About " + clock.dur(j.ui.est_s * 1000);
      why.textContent = size + (j.ui.mode === "net" ? ". Waiting for a download slot; starts after " : ". Waits because one analysis job runs at a time; starts after ") +
        (ahead ? "“" + ahead.ui.label + "”" : "the job before it") + (w.starts_in_s > 0 ? ", in about " + clock.dur(w.starts_in_s * 1000) : "") + ".";
    }
    update(job, jobs);
    return { el: el, update: update };
  }

  /* Failed or interrupted, with the way out */
  function needs(job) {
    var interrupted = job.status === "interrupted";
    var forWho = job.ui.person_name && !/ for /.test(job.ui.label) && !job.ui.label.match(new RegExp(first(job))) ? h("span.v2-dim.v2-small", { style: { fontWeight: 400 } }, " for " + first(job)) : null;
    var fixIndex = !interrupted && job.error && /no index/i.test(job.error.message);
    var text;
    if (interrupted) {
      text = (job.ui.note ? job.ui.note + " " : "") + (job.ui.mode === "net"
        ? "Resume continues from the last byte downloaded (" + clock.gb((job.ui.resume.from_bytes || 0) / 1e9) + " of " + clock.gb(job.progress.bytes_total / 1e9) + ")."
        : "Resume continues from the last finished step (" + job.ui.resume.from_step + ").");
    } else {
      text = (job.error ? job.error.message : "It failed.") + (fixIndex ? " Creating one fixes it; nothing else was changed." : "");
    }
    var button = fixIndex
      ? h("button.v2-btn.v2-btn--sm", { type: "button", onclick: function () {
        act(api.post("/v1/ops/bam/index", { input: job.params.input, _mock: { person: job.ui.person, label: "Index " + first(job) + "’s BAM", thenRerun: job.id } }),
          "Indexing " + first(job) + "’s BAM, then running again");
      } }, "Create the index and run again · 6 min")
      : h("button.v2-btn.v2-btn--sm", { type: "button", onclick: function () {
        act(api.post("/v1/jobs/" + job.id + "/rerun"), (interrupted ? "Resumed: " : "Running again: ") + job.ui.label);
      } }, interrupted ? "Resume" : "Run again");
    var el = h("div.v2-sheet__pad.v2-section", { dataset: { jobId: job.id, annotId: { "vcf/snp": "needs-snps", "ref/index": "needs-download" }[job.op] || "needs-" + job.id } },
      h("div.v2-row.v2-row--wrap", badge(job), h("strong.v2-grow", job.ui.label, forWho),
        interrupted ? status("attention", "Interrupted at " + percent(job) + "%") : status("bad", "Failed after " + clock.dur(job.ui.took_s * 1000))),
      h("p.v2-small.v2-muted", text), h("div.v2-row", button));
    return { el: el, update: function () {} };
  }

  function href(job) {
    var p = job.ui.person;
    if (job.op === "microarray") return "result.html";
    if (/^lineage\/|^qc\/ancestry$/.test(job.op)) return p === "alex" ? "ancestry-alex.html" : "ancestry.html";
    return PERSON_PAGE[p] || null;
  }

  function summary(job) {
    var m = (job.result && job.result.metrics) || {};
    var took = "took " + clock.dur(job.ui.took_s * 1000);
    if (job.status === "cancelled") return ["Cancelled after " + clock.dur(job.ui.took_s * 1000)];
    if (m.haplogroup) return [h("span.v2-hap", { dataset: { line: job.op.indexOf("y-") >= 0 ? "y" : "mt" } }, m.haplogroup), " · " + took];
    if (m.kits) return [m.kits + " files, for " + m.sites + " · " + took];
    if (m.components) return [m.components + " components, " + m.summary];
    if (job.ui.mode === "net") return [clock.gb(job.ui.outputs[0].gb) + " downloaded · " + took];
    var lib = job.ui.outputs.filter(function (o) {
      return o.library;
    })[0];
    return lib ? ["New file ", h("span.v2-mono", lib.path.split(/[\\/]/).pop()), " · " + took] : [took];
  }

  function done(job) {
    var link = href(job);
    var cost = h("span.v2-do__cost", clock.when(ms(job.finished)), link ? WGSM.icon("chevron-right", "icon--sm") : null);
    var attrs = { dataset: { jobId: job.id } };
    if (link) attrs.href = WGSM.keepParams(link);
    var el = h(link ? "a.v2-do" : "div.v2-do", attrs, badge(job), h("span.v2-do__title", job.ui.label), cost, h("span.v2-do__desc", summary(job)));
    return { el: el, update: function () {} };
  }

  WGSM.v2jobs = { running: running, queued: queued, needs: needs, done: done };
})();
