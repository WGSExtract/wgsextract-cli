/* Empty library states, durable settings, and safe library edits. */
(function () {
  "use strict";
  var W = window.WGSM;
  var h = W.h;
  var api = W.v2api;
  var E = W.v2edge;
  var settingsKeys = {
    "Phone access": "phone_access",
    "Run one analysis job at a time": "max_concurrent_jobs",
    "Keep this computer awake while jobs run": "prevent_sleep",
    "Tell me when a job finishes or needs me": "notifications",
    "Keep running in the menu bar when the window is closed": "menu_bar",
  };

  function settings(s) {
    var folders = W.qs("[data-edge-folders]");
    if (folders) folders.replaceChildren.apply(folders, s.allowed_paths.map(function (path) {
      return h("div.v2-row.v2-row--wrap", h("span.v2-mono.v2-grow", path),
        h("button.v2-btn.v2-btn--sm.v2-btn--quiet", { type: "button", onclick: function () {
          E.settingsPatch({ allowed_paths: s.allowed_paths.filter(function (p) { return p !== path; }) });
        } }, "Remove"));
    }));
    var move = W.qs('[data-edge="workspace"]');
    if (move) {
      /* The seed keeps {{bundleWorkspace}}; show it as a real folder, home as ~ */
      var M = W.v2machine;
      var real = M.realPath(s.workspace);
      var home = M.realPath("~/x").slice(0, -2);
      W.qs(".v2-mono", move.parentElement).textContent = real.indexOf(home) === 0 ? "~" + real.slice(home.length) : real;
      api.get("/v1/disks").then(function (res) {
        if (res.status !== 200) return;
        var matches = res.body.disks.filter(function (d) { return real.indexOf(M.realPath(d.path)) === 0; }).sort(function (a, b) { return b.path.length - a.path.length; });
        var disk = matches[0] || res.body.disks[0];
        W.qs(".v2-dim", move.parentElement).textContent = W.v2clock.gb(Math.max(0, disk.free_gb)) + " free on " + disk.name;
      });
    }
    Object.keys(settingsKeys).forEach(function (label) {
      var box = W.qs('input[aria-label="' + label + '"]');
      var value = s[settingsKeys[label]];
      if (box && value !== undefined) box.checked = settingsKeys[label] === "max_concurrent_jobs" ? value === 1 : value;
    });
    var pairing = W.qs('[data-annot-id="pairing"]');
    if (pairing) pairing.hidden = !s.phone_access;
    var log = W.qs("#access-log tbody");
    if (log) {
      W.qsa("[data-edge-access]", log).forEach(function (row) { row.remove(); });
      (s.requests || []).forEach(function (req) {
        log.prepend(h("tr", { dataset: { edgeAccess: "" } }, h("td", req.at), h("td", "Paired phone"), h("td", req.action + " · " + req.resource)));
      });
    }
  }

  function library() {
    return api.get("/v1/library").then(function (res) {
      if (res.status !== 200) return;
      var people = res.body.people;
      var live = people.filter(function (p) { return !p.removed; });
      people.forEach(function (p) {
        W.qsa('[data-v2-group="' + p.id + '"], [data-annot-id="nav-' + p.id + '"], [data-v2-gen-group="' + p.id + '"]').forEach(function (el) { el.hidden = p.removed; });
      });
      if (W.page.id !== "v2-home") return;
      var count = live.reduce(function (n, p) { return n + p.files.length; }, res.body.unassigned.length);
      W.qs(".v2-head__sub").textContent = live.length + " people, " + count + " files. They stay where they are on your disks; nothing is copied or uploaded.";
      var section = W.qs('[data-annot-id="library"]');
      var unassigned = W.qs('[data-annot-id="unassigned"]');
      if (unassigned) unassigned.hidden = !res.body.unassigned.some(function (f) { return f.id === "fastq"; }) &&
        !people.some(function (p) { return !p.removed && p.files.some(function (f) { return f.id === "fastq"; }); });
      var empty = W.qs("[data-edge-empty]");
      if (!empty) {
        empty = h("div.v2-empty", { dataset: { edgeEmpty: "" } }, h("h2", "Add your first genome"),
          h("p.v2-small", "Add BAM, CRAM, FASTQ, VCF or DNA-test files. WGS Extract reads them in place without copying or uploading them."),
          h("a.v2-btn.v2-btn--primary", { href: "add.html" }, "Add files"));
        section.before(empty);
      }
      var none = !live.length && !res.body.unassigned.length;
      empty.hidden = !none;
      section.hidden = none;
      var notice = W.qs('[data-annot-id="attention"]');
      if (notice && !live.some(function (p) { return p.id === "alex"; })) notice.hidden = true;
    });
  }

  function references() {
    if (!W.qs('[data-edge-ref], [data-edge="remove-clinvar"]')) return;
    api.get("/v1/references").then(function (res) {
      if (res.status !== 200) return;
      res.body.references.forEach(function (ref) {
        W.qsa('[data-edge-ref="' + ref.id + '"]').forEach(function (button) {
          button.disabled = !!(ref.installed || ref.downloading);
          button.textContent = ref.installed ? "Installed" : ref.downloading ? "Downloading · see Jobs" : "Download " + W.v2clock.gb(ref.gb);
          var row = button.closest(".v2-item");
          var status = row && W.qs(".v2-status, .v2-dim", W.qs(".v2-item__aside", row));
          if (status) status.textContent = ref.installed ? "Installed" : ref.downloading ? "In Jobs" : "Not installed";
        });
        if (ref.id === "clinvar") {
          var button = W.qs('[data-open="#remove"]');
          if (!button) return;
          if (!ref.installed) {
            button.removeAttribute("data-open");
            button.dataset.edge = "reference";
            button.dataset.edgeRef = "clinvar";
            button.textContent = "Download ClinVar";
          }
        }
      });
    });
  }

  function edit(event) {
    if (document.body.classList.contains("an-mode")) return;
    var button = event.target.closest("[data-v2-remove], [data-v2-save], [data-v2-reset]");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (button.hasAttribute("data-v2-reset")) {
      api.reset().then(function () { W.pref("v2-state", "{}"); W.pref("v2-convert-output", ""); location.reload(); });
      return;
    }
    var d = button.closest("[data-v2-person]");
    var id = d.dataset.v2Person;
    var removing = button.hasAttribute("data-v2-remove");
    var body = removing ? null : { name: W.qs('[data-v2-field="name"]', d).value, note: W.qs('[data-v2-field="note"]', d).value };
    button.disabled = true;
    var promise = removing ? api.del("/v1/library/people/" + id) : api.patch("/v1/library/people/" + id, body);
    promise.then(function (res) {
      button.disabled = false;
      if (E.refused(res)) return;
      var before = W.v2state.person(id);
      W.v2state.doc.people[id] = Object.assign({}, W.v2state.doc.people[id], removing ? { removed: true } : body);
      W.v2state.save();
      if (body) W.v2state.replaceWords([[before.name, body.name.trim()], [W.v2state.first(before.name), W.v2state.first(body.name.trim())]]);
      W.ui.close(d);
      if (removing) location.href = W.keepParams("home.html");
      else { W.toast("Saved"); library(); }
    });
  }

  /* Register before state.apply's capture listener, so refusal is atomic. */
  document.addEventListener("click", edit, true);

  function start() {
    api.ready.then(function () {
      api.get("/v1/mock/settings").then(function (res) { if (res.status === 200) settings(res.body); });
      library();
      references();
    });
    W.on("v2-settings", settings);
    api.on(function () { library(); references(); });
    document.addEventListener("change", function (event) {
      var box = event.target;
      var key = settingsKeys[box.getAttribute("aria-label")];
      if (!key || box.type !== "checkbox") return;
      var body = {};
      body[key] = key === "max_concurrent_jobs" ? (box.checked ? 1 : 2) : box.checked;
      E.settingsPatch(body);
    });
    var mock = W.qs("[data-v2-mock-slot]");
    if (mock) {
      var select = h("select.v2-select", { "aria-label": "Review edge case" },
        [["empty-jobs", "No jobs"], ["empty-library", "Empty library"], ["disk-full", "Almost full disks"], ["restore-space", "Restore disk space"]].map(function (o) {
          return h("option", { value: o[0] }, o[1]);
        }));
      mock.appendChild(h("div.v2-row.v2-row--wrap", select, h("button.v2-btn.v2-btn--sm", { type: "button", onclick: function () {
        api.post("/v1/mock/scenario", { name: select.value }).then(function (res) {
          if (!E.refused(res)) api.syncGenerated().then(function () { location.reload(); });
        });
      } }, "Apply review scenario")));
    }
  }
  W.v2edgeViews = { start: start };
})();
