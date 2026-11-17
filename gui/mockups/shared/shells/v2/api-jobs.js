/* <div data-render="v2-jobs" data-section="running|queued|downloads|needs-you|done"
 * [data-limit="8"]>: one section of the Jobs page, live from
 * GET /v1/jobs?detail=1 and updated on every WGSM.v2api tick. Cards come
 * from api-jobs-cards.js. Needs you holds failed and interrupted jobs that
 * nothing has rerun or fixed yet; Done holds finished and cancelled ones,
 * newest first. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var api = WGSM.v2api;
  var cards = WGSM.v2jobs;
  var sections = [];

  var EMPTY = {
    running: "Nothing is running.",
    queued: "Nothing is waiting.",
    downloads: "No downloads running or waiting.",
    "needs-you": "Nothing needs you.",
    done: "Nothing has finished yet.",
  };

  /* A later job reran it, or a running fix will */
  function handled(job, jobs) {
    return jobs.some(function (j) {
      return j.ui.rerun_of === job.id || (j.ui.then_rerun === job.id && ["queued", "running", "succeeded"].indexOf(j.status) >= 0);
    });
  }

  function pick(name, jobs, limit) {
    var list = jobs.filter(function (j) {
      if (name === "downloads") return j.ui.mode === "net" && (j.status === "running" || j.status === "queued");
      if (name === "running") return j.ui.mode === "cpu" && j.status === "running";
      if (name === "queued") return j.ui.mode === "cpu" && j.status === "queued";
      if (name === "needs-you") return (j.status === "failed" || j.status === "interrupted") && !handled(j, jobs);
      return j.status === "succeeded" || (j.status === "cancelled" && !handled(j, jobs));
    });
    if (name === "done") {
      list.sort(function (a, b) {
        return Date.parse(b.finished) - Date.parse(a.finished);
      });
      list = list.slice(0, limit);
    }
    return list;
  }

  function make(name, job, jobs) {
    if (name === "downloads") return job.status === "running" ? cards.running(job) : cards.queued(job, jobs);
    if (name === "running") return cards.running(job);
    if (name === "queued") return cards.queued(job, jobs);
    if (name === "needs-you") return cards.needs(job);
    if (job.status === "cancelled") return cards.needs(job);
    return cards.done(job);
  }

  function render(s, jobs) {
    var list = pick(s.name, jobs, s.limit);
    var keys = list.map(function (j) {
      return j.id + ":" + j.status;
    });
    var next = {};
    list.forEach(function (j, i) {
      var card = s.cards[keys[i]] || make(s.name, j, jobs);
      card.update(j, jobs);
      next[keys[i]] = card;
    });
    s.cards = next;
    if (keys.join() === s.keys) return;
    s.keys = keys.join();
    s.el.replaceChildren(list.length
      ? h("div.v2-sheet", keys.map(function (k) {
        return next[k].el;
      }))
      : h("p.v2-small.v2-muted.v2-jobs-empty", EMPTY[s.name]));
    WGSM.icons.hydrate(s.el);
  }

  function refresh() {
    var live = sections.filter(function (s) {
      return s.el.isConnected;
    });
    if (!live.length) return Promise.resolve();
    return api.get("/v1/jobs?detail=1&full=1").then(function (res) {
      if (res.status !== 200) return;
      live.forEach(function (s) {
        render(s, res.body.jobs);
      });
    });
  }

  WGSM.render.blocks["v2-jobs"] = function (el, opts) {
    var name = EMPTY[opts.section] ? opts.section : "running";
    el.classList.add("v2-jobs");
    el.dataset.v2JobsSection = name;
    sections.push({ el: el, name: name, limit: Number(opts.limit) || 8, cards: {}, keys: null });
    if (sections.length === 1) {
      api.ready.then(refresh);
      api.on(refresh);
    }
  };
})();
