/* Job rows and lists, from WGSM.data.jobs (shaped like GET /v1/jobs). */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;

  var STATUS = {
    running: ["Running", "badge--primary"],
    queued: ["Queued", ""],
    succeeded: ["Done", "badge--success"],
    failed: ["Failed", "badge--danger"],
    interrupted: ["Interrupted", "badge--warn"],
    cancelled: ["Cancelled", ""],
  };

  var GROUPS = {
    active: ["running", "queued", "interrupted"],
    done: ["succeeded", "cancelled"],
    failed: ["failed", "interrupted"],
  };

  function stages(job) {
    return h(
      "div.stages",
      { "aria-label": "Steps" },
      job.stages.map(function (s) {
        return h(
          "div.stage",
          { dataset: { state: s.state }, style: { "--w": s.w || 1 } },
          h("div.stage__bar", h("span", { style: { "--v": (s.v || 0) + "%" } })),
          h("div.stage__label", s.label)
        );
      })
    );
  }

  function bar(job) {
    var tone = { interrupted: ".progress--warn", failed: ".progress--danger" }[job.status] || "";
    return h(
      "div.progress" + tone,
      { role: "progressbar", "aria-valuenow": job.progress, "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": job.title },
      h("div.progress__fill", { style: { "--v": job.progress + "%" } })
    );
  }

  function actions(job) {
    var a = [];
    var logs = h("button.btn.btn--sm.btn--ghost.adv-only", { type: "button", "data-open": "#job-log" }, WGSM.icon("scroll-text"), "Log");
    if (job.status === "running") {
      a.push(logs, h("button.btn.btn--sm", { type: "button", "data-toast": "Cancelling… partial outputs are kept" }, "Cancel"));
    } else if (job.status === "queued") {
      a.push(h("button.btn.btn--sm", { type: "button", "data-toast": "Removed from the queue" }, "Cancel"));
    } else if (job.status === "succeeded") {
      a.push(h("a.btn.btn--sm", { href: WGSM.keepParams(WGSM.link("directions/c/ancestry.html")) }, "Open result"));
    } else if (job.status === "failed") {
      a.push(logs, h("button.btn.btn--sm.btn--primary", { type: "button", "data-toast": "Indexing, then running again" }, "Fix and retry"));
    } else if (job.status === "interrupted") {
      a.push(h("button.btn.btn--sm.btn--primary", { type: "button", "data-toast": "Resuming from the last finished step" }, WGSM.icon("play"), "Resume"));
    }
    return h("div.job__actions", a);
  }

  WGSM.render.jobRow = function (job) {
    var genome = WGSM.data.genome(job.genome);
    var status = STATUS[job.status];
    var showBar = job.status === "running" || job.status === "interrupted";
    return h(
      "div.job",
      { dataset: { status: job.status, annotId: "job-" + job.op.replace(/\//g, "-") } },
      h("div.job__icon", WGSM.icon(job.icon)),
      h(
        "div.job__main",
        h(
          "div.job__title",
          h("span.truncate", job.title),
          h("span.badge" + (status[1] ? "." + status[1] : ""), status[0])
        ),
        h(
          "div.xsmall.subtle.row.row--wrap",
          h("span", genome ? genome.name : ""),
          h("code.adv-only", job.op)
        ),
        job.stages.length ? stages(job) : showBar ? bar(job) : null,
        h(
          "div.job__meta",
          job.status === "running" ? h("span", h("strong", job.progress + "%"), " · " + job.eta) : h("span", job.eta),
          h("span", job.detail),
          job.resources ? h("span.adv-only", job.resources) : null
        ),
        job.fix
          ? h("div.callout.callout--warn", { style: { padding: "var(--sp-2) var(--sp-3)" } }, WGSM.icon("wrench"), h("span", job.fix))
          : null
      ),
      actions(job)
    );
  };

  /* <div data-render="jobs" data-filter="active|done|failed|all" data-genome="hg002" data-limit="3"> */
  WGSM.render.blocks.jobs = function (el, opts) {
    var wanted = GROUPS[opts.filter];
    var jobs = WGSM.data.jobs.filter(function (job) {
      return (!wanted || wanted.indexOf(job.status) >= 0) && (!opts.genome || job.genome === opts.genome);
    });
    if (opts.limit) jobs = jobs.slice(0, Number(opts.limit));
    el.classList.add("card", "card--flush");
    jobs.forEach(function (job) {
      el.appendChild(WGSM.render.jobRow(job));
    });
  };
})();
