/* The mockup-settings "Computer" editor (review tooling, not app UI): pick a
 * preset, or change anything (OS, processor, cores, memory, GPU, volumes,
 * installed tool sets) to make a custom computer. Checked by
 * machine-spec.js, the same rules the shared mock server applies.
 *
 *   WGSM.v2machineEditor.build() -> {el, apply()}
 *   apply() saves the choice (and, on a shared mock server, sends it there)
 *   and resolves true when the page should reload. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var SPEC = window.WGSMMock.machineSpec;
  var OS = [["macos", "macOS"], ["linux", "Linux"], ["windows", "Windows"]];
  var OS_NAME = { macos: "macOS", linux: "Linux", windows: "Windows" };
  var ARCH = [["arm64", "ARM64"], ["x86-64", "x86-64"]];
  /* Defaults that follow the OS when you haven't changed them */
  var SYSTEM_NAME = { macos: "Macintosh HD", linux: "Home", windows: "C:" };
  var GENOMES_PATH = { macos: "/Volumes/Genomes", linux: "/mnt/genomes", windows: "D:/Genomes" };
  var GPU = [["none", "None"], ["metal", "Apple (Metal)"], ["nvidia", "NVIDIA (CUDA)"]];

  function clone(x) {
    return JSON.parse(JSON.stringify(x));
  }

  /* A preset as an editable profile: every field present */
  function editable(p) {
    var d = clone(p);
    d.gpuGB = d.gpuGB || 0;
    d.system = [d.system[0], d.system[1], d.system[2] || SPEC.niceTotal(d.system[1])];
    d.genomes = [d.genomes[0], d.genomes[1], d.genomes[2], d.genomes[3] || SPEC.niceTotal(d.genomes[1])];
    d.volumes = (d.volumes || []).map(function (v) {
      return [v[0], v[1], v[2], v[3] || SPEC.niceTotal(v[1])];
    });
    d.missing = d.missing || [];
    return d;
  }

  function seg(label, options, value, pick) {
    var el = h("div.v2-seg", { role: "group", "aria-label": label }, options.map(function (o) {
      return h("button", { type: "button", value: o[0], "aria-pressed": String(o[0] === value), onclick: function (event) {
        WGSM.qsa("button", el).forEach(function (b) {
          b.setAttribute("aria-pressed", String(b === event.currentTarget));
        });
        pick(o[0]);
      } }, o[1]);
    }));
    return el;
  }

  function build() {
    var mm = WGSM.v2machine;
    var presets = mm.profiles;
    var current = mm.machine.key;
    var choice = current;
    var saved = mm.custom();
    var draft = editable(current === "custom" ? mm.profile() : presets[current]);

    var cards = h("div.v2-mock__presets", { role: "radiogroup", "aria-label": "Computer" });
    var details = h("div.v2-mock__details");
    var problems = h("div.v2-notice", { dataset: { tone: "warn" }, hidden: true }, WGSM.icon("triangle-alert"), h("div.v2-notice__text"));
    var summary = h("p.v2-small.v2-muted");

    function card(key, label, os, spec) {
      return h("label.v2-choice.v2-mock__preset",
        h("input.v2-check", { type: "radio", name: "v2-machine", value: key, checked: key === choice ? true : null, onchange: function () {
          pickPreset(key);
        } }),
        h("span.v2-choice__title", label), h("span.v2-choice__aside", { "aria-hidden": "true" }, os),
        h("span.v2-choice__desc", spec));
    }

    function renderCards() {
      cards.replaceChildren.apply(cards, Object.keys(presets).map(function (key) {
        var p = presets[key];
        return card(key, p.label, OS_NAME[p.os], p.spec);
      }).concat([card("custom", "Custom", draft && choice === "custom" ? OS_NAME[draft.os] : "",
        choice === "custom" ? "Custom settings below" : saved ? saved.spec : "Change anything below")]));
    }

    function pickPreset(key) {
      choice = key;
      if (key === "custom") draft = editable(saved || draft);
      else draft = editable(presets[key]);
      renderCards();
      renderDetails();
    }

    /* Any edit makes the computer a custom one */
    function edited() {
      if (choice !== "custom") {
        choice = "custom";
        WGSM.qsa('input[name="v2-machine"]', cards).forEach(function (r) {
          r.checked = r.value === "custom";
        });
        WGSM.qs('input[value="custom"]', cards).closest("label").querySelector(".v2-choice__desc").textContent = "Custom settings below";
      }
      check();
    }

    function check() {
      var result = SPEC.normalize(draft);
      problems.hidden = !result.errors.length || choice !== "custom";
      WGSM.qs(".v2-notice__text", problems).replaceChildren(h("strong", "Fix these settings first: "), h("span", result.errors.join(". ") + "."));
      summary.textContent = result.profile.spec + " · " + OS_NAME[result.profile.os] + " " + result.profile.arch;
      return result;
    }

    function input(value, set, opts) {
      var o = opts || {};
      return h("input.v2-input" + (o.mono ? ".v2-input--mono" : ""), {
        type: o.type || "text", value: value === null || value === undefined ? "" : String(value), min: o.min, max: o.max, step: o.step,
        "aria-label": o.label, style: o.width ? { width: o.width + "px" } : null, placeholder: o.placeholder,
        oninput: function (e) {
          set(o.type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value);
          edited();
        },
      });
    }

    function field(label, small, control) {
      return h("div.v2-field", h("span.v2-field__label", label, small ? h("small", small) : null), h("div.v2-field__control", control));
    }

    function volumeRow(v, kind, index) {
      var pathAt = kind === "system" ? null : 2;
      var totalAt = kind === "system" ? 2 : 3;
      return h("tr",
        h("td", input(v[0], function (x) {
          v[0] = x;
        }, { label: "Volume name" })),
        h("td", kind === "system"
          ? h("span.v2-mono.v2-small", SPEC.systemPath(draft.os))
          : input(v[pathAt], function (x) {
            v[pathAt] = x;
          }, { label: "Mounted at", mono: true })),
        h("td", input(v[1], function (x) {
          v[1] = x;
        }, { type: "number", min: 0, label: "Free GB", width: 96 })),
        h("td", input(v[totalAt], function (x) {
          v[totalAt] = x;
        }, { type: "number", min: 1, label: "Size GB", width: 96 })),
        h("td", kind === "extra"
          ? h("button.v2-btn.v2-btn--sm.v2-btn--quiet", { type: "button", title: "Remove this volume", "aria-label": "Remove " + v[0], onclick: function () {
            draft.volumes.splice(index, 1);
            edited();
            renderDetails();
          } }, WGSM.icon("x", "icon--sm"))
          : h("span.v2-small.v2-muted", kind === "system" ? "System" : "Genomes")));
    }

    function renderDetails() {
      var d = draft;
      var gpuOptions = GPU.filter(function (g) {
        if (g[0] === "metal") return d.os === "macos" && d.arch === "arm64";
        if (g[0] === "nvidia") return d.os !== "macos";
        return true;
      });
      var archOptions = d.os === "windows" ? ARCH.slice(1) : ARCH;
      var installed = h("div.v2-mock__sets", mm.envSets().map(function (e) {
        return h("label.v2-row.v2-small",
          h("input.v2-check", { type: "checkbox", checked: d.missing.indexOf(e[0]) < 0 ? true : null, onchange: function (ev) {
            d.missing = d.missing.filter(function (m) {
              return m !== e[0];
            });
            if (!ev.target.checked) d.missing.push(e[0]);
            edited();
          } }), e[1]);
      }));
      details.replaceChildren(
        h("div.v2-fields",
          field("Name", "Shown in the top bar", input(d.label, function (x) {
            d.label = x;
          }, { label: "Name" })),
          field("Operating system", null, seg("Operating system", OS, d.os, function (x) {
            if (d.system[0] === SYSTEM_NAME[d.os]) d.system[0] = SYSTEM_NAME[x];
            if (d.genomes[2] === GENOMES_PATH[d.os]) d.genomes[2] = GENOMES_PATH[x];
            d.os = x;
            if (x === "windows") d.arch = "x86-64";
            if (x === "macos" && d.gpu === "nvidia") d.gpu = null;
            if (x !== "macos" && d.gpu === "metal") d.gpu = null;
            if (x === "macos" && d.arch === "arm64" && !d.gpu) d.gpu = "metal";
            edited();
            renderDetails();
          })),
          field("Processor", d.os === "macos" ? "ARM64 is Apple silicon; x86-64 is Intel" : d.os === "windows" ? "WGS Extract runs on Windows x86-64" : null,
            h("div.v2-row.v2-row--wrap", seg("Processor type", archOptions, d.arch, function (x) {
              d.arch = x;
              if (d.os === "macos") d.gpu = x === "arm64" ? "metal" : null;
              edited();
              renderDetails();
            }), input(d.cpu, function (x) {
              d.cpu = x;
            }, { label: "Processor name", placeholder: "Processor name", width: 200 }))),
          field("Cores", "Performance cores sort and compress", h("div.v2-row.v2-row--wrap",
            input(d.cores, function (x) {
              d.cores = x;
            }, { type: "number", min: 1, max: 256, label: "Cores", width: 90 }), h("span.v2-small.v2-muted", "cores, of which"),
            input(d.bigCores, function (x) {
              d.bigCores = x;
            }, { type: "number", min: 1, max: 256, label: "Performance cores", width: 90 }), h("span.v2-small.v2-muted", "performance"))),
          field("Memory", "Available RAM determines which aligners can run", h("div.v2-row", input(d.ramGB, function (x) {
            d.ramGB = x;
          }, { type: "number", min: 2, max: 4096, label: "Memory GB", width: 110 }), h("span.v2-small.v2-muted", "GB"))),
          field("GPU", d.os === "macos" ? (d.arch === "arm64" ? "Apple silicon’s GPU, through Metal" : "No GPU acceleration on Intel Macs") : "NVIDIA GPUs run CUDA builds; 16 GB or more on Linux allows Parabricks",
            h("div.v2-row.v2-row--wrap", seg("GPU", gpuOptions, d.gpu || "none", function (x) {
              d.gpu = x === "none" ? null : x;
              if (x === "nvidia" && !d.gpuGB) d.gpuGB = 12;
              edited();
              renderDetails();
            }), d.gpu === "nvidia" ? h("span.v2-row", input(d.gpuGB, function (x) {
              d.gpuGB = x;
            }, { type: "number", min: 1, max: 192, label: "GPU memory GB", width: 90 }), h("span.v2-small.v2-muted", "GB of GPU memory")) : null))),
        h("h3.v2-mock__h", "Volumes"),
        h("p.v2-small.v2-muted", "Free space drives preflight checks, estimates and simulated volumes. Gordon’s files live on the Genomes volume."),
        h("div.v2-mock__scroll", h("table.v2-mock__volumes",
          h("thead", h("tr", h("th", "Name"), h("th", "Mounted at"), h("th", "Free GB"), h("th", "Size GB"), h("th", h("span.visually-hidden", "Role")))),
          h("tbody", [volumeRow(d.system, "system"), volumeRow(d.genomes, "genomes")].concat(d.volumes.map(function (v, i) {
            return volumeRow(v, "extra", i);
          }))))),
        d.volumes.length < 6 ? h("div", h("button.v2-btn.v2-btn--sm", { type: "button", onclick: function () {
          var n = d.volumes.length + 1;
          d.volumes.push(["Volume " + n, 500, d.os === "windows" ? String.fromCharCode(68 + n) + ":/" : d.os === "macos" ? "/Volumes/Volume " + n : "/mnt/volume" + n, 1000]);
          edited();
          renderDetails();
        } }, WGSM.icon("plus", "icon--sm"), "Add a volume")) : null,
        h("h3.v2-mock__h", "Installed tool sets"),
        h("p.v2-small.v2-muted", "Optional Pixi environments; unchecked sets appear as not installed on tool pages and Install tools."),
        installed,
        summary,
        problems);
      check();
    }

    renderCards();
    renderDetails();

    var el = h("section.v2-mock__computer",
      h("h3.v2-mock__h", "Computer"),
      cards,
      h("details.v2-mock__more", { open: choice === "custom" ? true : null }, h("summary", "Details: OS, processor, memory, GPU, volumes, tools"), details));

    /* Open the details when Custom is picked */
    cards.addEventListener("change", function () {
      if (choice === "custom") WGSM.qs("details", el).open = true;
    });

    function apply() {
      var custom = choice === "custom" ? check() : null;
      if (custom && custom.errors.length) {
        WGSM.qs("details", el).open = true;
        problems.hidden = false;
        problems.scrollIntoView({ block: "nearest" });
        return Promise.resolve(false);
      }
      var same = choice === current && (choice !== "custom" || JSON.stringify(custom.profile) === JSON.stringify(mm.profile()));
      if (same) return Promise.resolve(false);
      mm.choose(choice, custom ? custom.profile : null);
      if (WGSM.v2api.mode() !== "shared") return Promise.resolve(true);
      return WGSM.v2api.post("/v1/mock/machine", { profile: choice, custom: custom ? custom.profile : undefined }).then(function (res) {
        if (res.status !== 200) WGSM.toast("Shared server rejected the profile: " + ((res.body && res.body.error && res.body.error.message) || res.status), "circle-alert");
        return true;
      });
    }

    return { el: el, apply: apply };
  }

  /* On a shared mock server, everyone sees its computer: adopt it once */
  function follow() {
    var api = WGSM.v2api;
    if (api.mode() !== "shared") return;
    api.get("/v1/mock/machine").then(function (res) {
      if (res.status !== 200) return;
      var mm = WGSM.v2machine;
      var b = res.body;
      var same = b.profile === mm.machine.key && (b.profile !== "custom" || JSON.stringify(b.custom) === JSON.stringify(mm.profile()));
      if (same) return;
      var marker = "v2-machine-follow:" + JSON.stringify([b.profile, b.custom]);
      if (sessionStorage.getItem(marker)) return;
      sessionStorage.setItem(marker, "1");
      mm.choose(b.profile, b.custom);
      location.reload();
    });
  }

  WGSM.v2machineEditor = { build: build, follow: follow };
})();
