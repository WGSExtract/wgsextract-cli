/* Working mock equivalents of desktop pickers, diagnostics and approvals.
 * The browser never opens or changes real files. */
(function () {
  "use strict";
  var W = window.WGSM;
  var h = W.h;
  var api = W.v2api;
  var output = null;

  function dialog(title, body, buttons) {
    var d = h("dialog.dialog", { id: "v2-edge-dialog", "aria-label": title },
      h("div.dialog__header", h("h2.dialog__title", title)),
      h("div.dialog__body.v2-section", body),
      h("div.dialog__footer.v2-row--wrap", h("button.v2-btn", { type: "button", "data-close": "" }, "Close"), buttons || null));
    var old = W.qs("#v2-edge-dialog");
    if (old) old.remove();
    d.addEventListener("close", function () { d.remove(); });
    W.ui.open(d);
    return d;
  }

  function refused(res, retry) {
    if (res.status > 0 && res.status < 300) return false;
    var err = res.body && res.body.error || { message: "Could not reach the server. Check that it is running and try again." };
    var old = W.qs("[data-edge-error]");
    if (old) old.remove();
    var box = h("div.v2-notice", { role: "alert", dataset: { tone: "bad", edgeError: err.code || "unreachable" } },
      W.icon("circle-alert"), h("div.v2-notice__text", h("strong", err.message)),
      h("div.v2-notice__actions.v2-row.v2-row--wrap",
        h("a.v2-btn.v2-btn--sm", { href: W.keepParams(err.code === "in_use" ? "jobs.html" : "settings.html#folders") }, err.code === "in_use" ? "View jobs" : "Free space / folders"),
        err.code === "insufficient_space" && retry ? h("button.v2-btn.v2-btn--sm", { type: "button", onclick: function () {
          volume("Choose another volume", function (disk) { retry(disk); });
        } }, "Pick another volume") : null));
    (W.qs("dialog[open] .dialog__body") || W.qs(".v2-page")).prepend(box);
    box.scrollIntoView({ block: "center" });
    W.icons.hydrate(box);
    return true;
  }

  function send(op, params) {
    return api.post("/v1/ops/" + op, params || {}).then(function (res) {
      if (!refused(res, function (disk) {
        send(op, Object.assign({}, params, { _mock: Object.assign({}, params && params._mock, { targetDisk: disk.id }) }));
      })) {
        var old = W.qs("[data-edge-error]");
        if (old) old.remove();
        W.toast("Submitted: " + res.body.ui.label + ". See Jobs.");
        api.refresh();
      }
      return res;
    });
  }

  function volume(title, choose) {
    api.get("/v1/disks").then(function (res) {
      if (refused(res)) return;
      var disks = res.body.disks;
      var select = h("select.v2-select", { "aria-label": "Volume" }, disks.map(function (d) {
        return h("option", { value: d.id }, d.name + " · " + W.v2clock.gb(Math.max(0, d.free_gb)) + " free");
      }));
      var d = dialog(title, [h("p.v2-small.v2-muted", "Mockup only: updates simulated paths without moving real files on your computer."), select],
        h("button.v2-btn.v2-btn--primary", { type: "button", dataset: { edgeChoose: "" }, onclick: function () {
          var disk = disks.filter(function (x) { return x.id === select.value; })[0];
          W.ui.close(d);
          choose(disk);
        } }, "Use this volume"));
    });
  }

  function folder() {
    Promise.all([api.get("/v1/library"), api.get("/v1/jobs?detail=1")]).then(function (responses) {
      if (responses.some(function (res) { return refused(res); })) return;
      var id = (document.body.dataset.nav || "").replace(/^p-/, "");
      if (W.page.id === "v2-result") id = "helen";
      var person = responses[0].body.people.filter(function (p) { return p.id === id; })[0];
      var files = person ? person.files.slice() : [];
      responses[1].body.jobs.forEach(function (job) {
        if (job.ui.person !== id || job.status !== "succeeded") return;
        job.ui.outputs.forEach(function (out) {
          if (!files.some(function (f) { return f.path === out.path; })) files.push({ name: out.path.split(/[\\/]/).pop(), path: out.path });
        });
      });
      dialog("Folder preview", [h("p.v2-small.v2-muted", "Simulated folder contents; real files on your computer are untouched."),
        h("div.v2-sheet", files.length ? files.map(function (f) {
          return h("div.v2-sheet__pad", h("strong.v2-small", f.name), h("p.v2-small.v2-muted", f.path));
        }) : h("p.v2-sheet__pad", "No files in this folder."))]);
    });
  }

  function settingsPatch(body) {
    return api.patch("/v1/mock/settings", body).then(function (res) {
      if (!refused(res)) {
        W.emit("v2-settings", res.body);
        W.toast("Saved");
      }
      return res;
    });
  }

  function allowFolder() {
    var input = h("input.v2-input", { type: "text", "aria-label": "Folder path", value: "~/Desktop" });
    var d = dialog("Allow a folder", [h("p.v2-small", "Add a simulated folder the server may read."), input],
      h("button.v2-btn.v2-btn--primary", { type: "button", onclick: function () {
        if (!input.value.trim()) { input.focus(); return; }
        api.get("/v1/mock/settings").then(function (res) {
          if (refused(res)) return;
          var paths = res.body.allowed_paths.slice();
          if (paths.indexOf(input.value.trim()) < 0) paths.push(input.value.trim());
          settingsPatch({ allowed_paths: paths }).then(function (saved) {
            if (saved.status === 200) W.ui.close(d);
          });
        });
      } }, "Allow folder"));
  }

  function microarray(who) {
    var f = W.v2machine.files[who];
    var params = { input: W.v2machine.realPath(W.v2machine.path(f)) };
    if (who === "reginald") {
      var formats = ["all", W.qs('[aria-label="MyHeritage chip version"]').value, "ftdna_v3", "ldna_v2", "23andme_v5", "ancestry_v2"];
      params.formats = W.qsa("#sites input").map(function (box, i) { return box.checked ? formats[i] : null; }).filter(Boolean).join(",");
      if (!params.formats) { refused({ status: 400, body: { error: { code: "usage", message: "Choose at least one DNA-test format." } } }); return; }
      var strand = W.qs('[data-seg="strand"] [aria-pressed="true"]');
      if (strand) params.strand_ambiguous = strand.value;
      var gq = W.qs('[aria-label="Minimum genotype quality"]'), dp = W.qs('[aria-label="Minimum depth"]');
      if (!gq.reportValidity() || !dp.reportValidity()) return;
      params.min_gq = Number(gq.value);
      params.min_dp = Number(dp.value);
      params.call_reasons = W.qs('[aria-label="Explain blanks"]').checked;
    }
    return send("microarray", params);
  }

  var actions = {
    "choose-files": function () { W.qs("[data-v2-generate] button").click(); },
    "create-person": function () {
      var name = h("input.v2-input", { type: "text", "aria-label": "Person name" });
      var d = dialog("Add a person", [h("p.v2-small", "Add their files now or later."), name],
        h("button.v2-btn.v2-btn--primary", { type: "button", onclick: function () {
          api.post("/v1/library/people", { name: name.value }).then(function (res) {
            if (refused(res)) return;
            api.syncGenerated().then(function () {
              W.ui.close(d);
              location.href = "person-added.html?id=" + encodeURIComponent(res.body.id);
            });
          });
        } }, "Add person"));
    },
    "add-preview": function (el) {
      var name = W.qs('[aria-label="Name of the new person"]').value.trim();
      var owners = ["#who-bam", "#who-fq"].map(function (id) {
        var value = W.qs(id).value;
        return /New person/.test(value) ? { name: name } : { person: value.toLowerCase() };
      });
      if (owners.some(function (owner) { return !owner.person && !owner.name; })) {
        refused({ status: 400, body: { error: { code: "usage", message: "Name the new person, or choose an existing person for each file." } } });
        return;
      }
      api.patch("/v1/library/people/alex", { removed: false }).then(function (res) {
        if (refused(res)) return;
        return api.post("/v1/library/files/alex/assign", owners[0]).then(function (assigned) {
          if (refused(assigned)) return;
          return api.post("/v1/library/files/fastq/assign", owners[1]);
        });
      }).then(function (res) {
        if (!res || refused(res)) return;
        var people = res.body.people;
        var bam = people.filter(function (p) { return p.files.some(function (f) { return f.id === "alex"; }); })[0];
        var fq = people.filter(function (p) { return p.files.some(function (f) { return f.id === "fastq"; }); })[0];
        people.forEach(function (p) { W.v2state.doc.people[p.id] = { name: p.name, note: p.note, removed: p.removed }; });
        W.v2state.doc.assign = { id: fq.id };
        W.v2state.save();
        el.disabled = true;
        el.textContent = "Added · view the Library";
        var old = W.qs("[data-edge-error]");
        if (old) old.remove();
        api.refresh();
        if (W.qs('[data-annot-id="found-bam"] input[type="checkbox"]').checked) {
          send("bam/index", { input: W.v2machine.realPath(W.v2machine.path(W.v2machine.files.alex)),
            _mock: { person: bam.id, label: "Install hs38d1 and index " + bam.name + "’s BAM", gb: 0.9, flags: ["alex-fixed", "ref-hs38d1"] } });
        }
      });
    },
    output: function () { volume("Save results to", function (d) {
      output = d.path;
      W.pref("v2-convert-output", output);
      W.qs("[data-edge-output]").textContent = output;
    }); },
    convert: function () {
      var m = W.v2machine;
      var v = W.qs('[data-seg="cramv"] [aria-pressed="true"]');
      var selected = W.qs('input[name="to"]:checked'), threads = W.qs('[aria-label="Threads"]');
      if (!threads.reportValidity()) return;
      var op = selected && selected.value || "bam/to-cram";
      var params = { input: m.realPath(m.path(m.files.gordon)), outdir: output || m.outdir(m.files.gordon), threads: Number(threads.value) };
      if (op === "bam/to-cram") params.cram_version = v ? v.value : "3.0";
      return send(op, params);
    },
    dna: function () { microarray("reginald"); },
    "rerun-result": function () { microarray("helen"); },
    example: function () { send("ref/test-genome", { _mock: { gb: 0.12, label: "Download small example genome (120 MB)" } }); },
    workspace: function () { volume("Move the workspace", function (d) {
      settingsPatch({ workspace: d.path.replace(/\/$/, "") + "/wgs-extract" });
    }); },
    "allow-folder": allowFolder,
    folder: folder,
    check: function () {
      send("deps/check", {}).then(function (res) {
        if (res.status >= 300 || !res.status) return;
        dialog("Tool check", [h("p.v2-small", "Checked the simulated computer. WGS Extract checks which tools are present and does not install them automatically."),
          h("ul.v2-plain-list", W.v2machine.missingSets().map(function (s) { return h("li", s[1] + " · not installed"); })),
          h("a.v2-link", { href: "install.html" }, "Installation instructions")]);
      });
    },
    report: function () {
      api.get("/v1/system").then(function (res) {
        if (refused(res)) return;
        var text = JSON.stringify({ version: "mock", os: res.body.os, arch: res.body.arch, cores: res.body.cores, ram_gb: res.body.ram_gb }, null, 2);
        dialog("Problem report", [h("p.v2-small", "Contains system specs only. No names, genome data or file paths are included."), h("pre.v2-code", text)],
          h("button.v2-btn", { type: "button", onclick: function () {
            if (!navigator.clipboard) { W.toast("Select the report text to copy it"); return; }
            navigator.clipboard.writeText(text).then(function () { W.toast("Report copied"); }, function () { W.toast("Clipboard unavailable. Select the report text to copy it."); });
          } }, "Copy report"));
      });
    },
    "resume-index": function () {
      api.get("/v1/jobs?detail=1").then(function (res) {
        var job = res.body.jobs.filter(function (j) { return j.op === "ref/index" && ["interrupted", "cancelled", "failed"].indexOf(j.status) >= 0; }).pop();
        if (!job) { W.toast("No interrupted index download. See Jobs."); return; }
        api.post("/v1/jobs/" + job.id + "/rerun").then(function (r) {
          if (!refused(r)) W.toast("Resumed. See Jobs.");
        });
      });
    },
    "verify-references": function () { send("ref/verify", {}); },
    "remove-clinvar": function (el) {
      api.del("/v1/references/clinvar").then(function (res) {
        if (!refused(res)) {
          W.ui.close(el.closest("dialog"));
          W.toast("ClinVar removed");
          api.refresh();
        }
      });
    },
    "save-genomes": function () { api.get("/v1/disks").then(function (res) {
      if (refused(res)) return;
      var disk = res.body.disks.filter(function (d) { return d.id === "genomes"; })[0];
      W.pref("v2-convert-output", disk.path);
      W.qs('[data-annot-id="state-disk"] .v2-notice__text').textContent = "Output volume set to " + disk.name + ". Open Convert when you are ready to start the job.";
    }); },
    ask: function () { approval("ask"); },
    decline: function () { approval("decline"); },
    allow: function () { approval("allow"); },
  };

  function approval(action) {
    api.post("/v1/mock/approval", { action: action, resource: action === "ask" ? "start_jobs" : "clinvar" }).then(function (res) {
      if (refused(res)) return;
      var box = W.qs('[data-annot-id="' + (action === "ask" ? "state-readonly" : "state-confirm") + '"]');
      W.qs(".v2-notice__text", box).textContent = action === "ask" ? "Request recorded on the computer. Open Phone access to review it." : action === "allow" ? "Approved once. ClinVar removed." : "Declined; ClinVar stays installed.";
      W.qs(".v2-notice__actions", box).hidden = true;
      api.refresh();
    });
  }

  /* The static showcase's former toast-only controls get real actions.
   * Remove data-toast so shared/ui.js cannot announce a false success. */
  function bind() {
    W.qsa("[data-toast]").forEach(function (el) {
      if (el.matches("[data-v2-download], [data-v2-reset], [data-v2-assign], [data-v2-remove], [data-v2-set]")) return;
      var text = el.dataset.toast;
      var action = /^Opening /.test(text) ? "folder" : /Checked/.test(text) ? "check" :
        /Choose another folder|Choose a new place/.test(text) ? "workspace" :
        /Report copied/.test(text) ? "report" : /small example/.test(text) ? "example" :
        /allowed folders/.test(text) ? "allow-folder" : /saved on Genomes/.test(text) ? "save-genomes" :
        /Asked on/.test(text) ? "ask" : text === "Declined" ? "decline" : text === "ClinVar removed" ? "allow" : null;
      var ref = /^Downloading (hs38d1|T2T-CHM13 v2.0|hs38DH|hg38|hg19|hs37|gnomAD|the VEP cache)/.exec(text);
      if (ref) {
        var ids = { "T2T-CHM13 v2.0": "T2Tv20", gnomAD: "gnomad", "the VEP cache": "vep-cache" };
        el.dataset.edgeRef = ids[ref[1]] || ref[1];
        action = "reference";
      }
      if (action) { el.dataset.edge = action; el.removeAttribute("data-toast"); }
    });
    var dry = W.qs('[data-toast^="Dry run:"]');
    if (dry) { dry.dataset.edge = "dry-run"; dry.removeAttribute("data-toast"); }
    output = W.pref("v2-convert-output");
    if (output && W.qs("[data-edge-output]")) W.qs("[data-edge-output]").textContent = output;
    var add = W.qs("[data-v2-generate] > .v2-row");
    if (add) add.appendChild(h("button.v2-btn.v2-btn--quiet", { type: "button", dataset: { edge: "create-person" } }, "Add a person without files…"));
  }

  actions.reference = function (el) {
    var id = el.dataset.edgeRef;
    if (id === "gnomad") send("ref/gnomad", {});
    else if (id === "vep-cache") send("vep/download", {});
    else if (id === "clinvar") send("ref/clinvar", {});
    else send("ref/library", { install: id });
  };
  actions["dry-run"] = function () {
    var root = W.qs("[data-tool]");
    var reqs = W.v2tools.requests(root);
    Promise.all(reqs.map(function (req) { return api.post("/v1/estimate", req); })).then(function (results) {
      var bad = results.filter(function (r) { return r.status !== 200; })[0];
      if (bad) { refused(bad); return; }
      dialog("Dry run · no jobs started", results.map(function (res) {
        return h("section.v2-section", h("strong", res.body.label),
          h("ol.v2-plain-list", res.body.steps.map(function (s) { return h("li", s.name); })));
      }));
    });
  };

  document.addEventListener("click", function (event) {
    if (document.body.classList.contains("an-mode")) return;
    var flag = event.target.closest("[data-v2-set], [data-v2-unset]");
    if (flag) {
      event.preventDefault();
      event.stopImmediatePropagation();
      var body = { set: (flag.dataset.v2Set || "").split(" ").filter(Boolean), unset: (flag.dataset.v2Unset || "").split(" ").filter(Boolean) };
      api.post("/v1/mock/flags", body).then(function (res) {
        if (refused(res)) return;
        if (body.set.length) W.v2state.setFlags(body.set.join(" "), true);
        if (body.unset.length) W.v2state.setFlags(body.unset.join(" "), false);
        W.toast(flag.dataset.toast || "Saved");
      });
      return;
    }
    var go = event.target.closest("[data-v2-go], [data-v2-download]");
    if (go && !go.disabled) {
      event.preventDefault();
      event.stopImmediatePropagation();
      go.disabled = true;
      var run = go.hasAttribute("data-v2-go") ? W.v2tools.run(W.qs("[data-tool]")) : W.v2requests.download(go.dataset.v2Download, go.dataset.v2Label || go.textContent);
      Promise.resolve(run).finally(function () { go.disabled = false; W.v2tools.refresh(); });
      return;
    }
    var el = event.target.closest("[data-edge]");
    if (!el || el.disabled) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (actions[el.dataset.edge]) actions[el.dataset.edge](el);
  }, true);

  function beforeRun(ctx, requests) {
    return Promise.all([api.get("/v1/library"), api.get("/v1/references")]).then(function (responses) {
      if (responses.some(function (res) { return refused(res); })) return false;
      var p = responses[0].body.people.filter(function (p) { return p.id === ctx.file.person; })[0];
      if (!ctx.def.noInput && p && p.removed) {
        dialog("No input file", [h("p", "This person is no longer in the library."), h("a.v2-btn", { href: "add.html" }, "Add files")]);
        return false;
      }
      var needed = requests.map(function (r) { return r.params.ref; }).filter(Boolean);
      if (!ctx.def.noInput && ctx.file.tag === "CRAM") needed.push(ctx.file.ref);
      var missing = responses[1].body.references.filter(function (ref) {
        return !ref.installed && needed.some(function (path) {
          return String(path).split(/[\\/]/).pop().replace(/\.(fa|fasta|fna)(\.gz)?$/, "") === ref.id;
        });
      })[0];
      if (!missing) return true;
      var d = dialog("Missing reference genome", [
        h("p", "This tool requires " + missing.name + " before it can run."),
        h("p.v2-small.v2-muted", "Downloading fetches the public reference file in Jobs; your sample files are not sent anywhere.")],
        h("button.v2-btn.v2-btn--primary", { type: "button", onclick: function () {
          send("ref/library", { install: missing.id }).then(function (res) {
            if (res.status > 0 && res.status < 300) W.ui.close(d);
          });
        } }, "Download " + missing.name + " · " + W.v2clock.gb(missing.gb)));
      return false;
    });
  }
  W.v2edge = { start: bind, dialog: dialog, refused: refused, send: send, volume: volume, settingsPatch: settingsPatch, beforeRun: beforeRun };
})();
