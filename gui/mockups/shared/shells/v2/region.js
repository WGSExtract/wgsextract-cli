/* A region picker for every tool that takes --region: the whole genome,
 * chromosomes (or part of one), a gene, or the exact text as a fallback.
 * Its value is the region string the CLI takes, kept in a hidden control
 * (data-v2-key="region") so saved options and the command line read it.
 * Chromosome names follow the file's build: chr1 on GRCh38, 1 on GRCh37.
 *
 * WGSM.v2region.build(field) → element; start() wires it once the page's
 * saved options are restored; share(value, build) → share of the genome. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;

  /* GRCh38 lengths in Mb; close enough for GRCh37 shares */
  var CHROMS = [
    ["1", 248.96], ["2", 242.19], ["3", 198.3], ["4", 190.21], ["5", 181.54], ["6", 170.81], ["7", 159.35], ["8", 145.14],
    ["9", 138.39], ["10", 133.8], ["11", 135.09], ["12", 133.28], ["13", 114.36], ["14", 107.04], ["15", 101.99], ["16", 90.34],
    ["17", 83.26], ["18", 80.37], ["19", 58.62], ["20", 64.44], ["21", 46.71], ["22", 50.82], ["X", 156.04], ["Y", 57.23], ["M", 0.0166],
  ];
  var TOTAL = CHROMS.reduce(function (sum, c) {
    return sum + c[1];
  }, 0);

  /* Illustrative gene spans: [GRCh38, GRCh37] chromosome, start, end */
  var GENES = {
    BRCA1: ["17", [43044295, 43125483], [41196312, 41277500], "Breast and ovarian cancer risk"],
    BRCA2: ["13", [32315508, 32400268], [32889611, 32973805], "Breast and ovarian cancer risk"],
    TP53: ["17", [7661779, 7687550], [7565097, 7590856], "Tumour suppressor"],
    APOE: ["19", [44905796, 44909393], [45409039, 45412650], "Cholesterol transport; Alzheimer’s risk"],
    CYP2D6: ["22", [42126499, 42130881], [42522501, 42526883], "Breaks down about a quarter of common medicines"],
    "HLA-A": ["6", [29941260, 29945884], [29909037, 29913661], "Immune system; transplant matching"],
    MTHFR: ["1", [11785723, 11806455], [11845780, 11866977], "Folate metabolism"],
    LCT: ["2", [135787850, 135837184], [136545415, 136594750], "Lactose tolerance"],
    MC1R: ["16", [89912119, 89920973], [89978527, 89987385], "Red hair and fair skin"],
    SMN1: ["5", [70924941, 70953015], [70220768, 70248842], "Spinal muscular atrophy carriers"],
    HTT: ["4", [3074681, 3243960], [3076408, 3245687], "Huntington’s disease repeat"],
    FMR1: ["X", [147911951, 147951125], [146993469, 147032647], "Fragile X repeat"],
  };

  function grch37(build) {
    return build === "GRCh37";
  }

  function name(chrom, build) {
    if (grch37(build)) return chrom === "M" ? "MT" : chrom;
    return "chr" + chrom;
  }

  function bare(text) {
    return text.replace(/^chr/i, "").replace(/^MT$/i, "M").toUpperCase();
  }

  function length(chrom) {
    var found = CHROMS.filter(function (c) {
      return c[0] === chrom;
    })[0];
    return found ? found[1] : null;
  }

  function commas(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  /* Parse "chr1,chr2" or "chr17:43044295-43125483" into parts, or an error */
  function parse(value) {
    if (!value) return { parts: [] };
    var parts = [];
    var bad = null;
    value.split(",").map(function (s) {
      return s.trim();
    }).filter(Boolean).forEach(function (piece) {
      var m = /^([^:]+)(?::([\d,]+)(?:-([\d,]+))?)?$/.exec(piece);
      var chrom = m && bare(m[1]);
      var len = chrom && length(chrom);
      if (!len) {
        bad = bad || "“" + piece + "” isn’t a chromosome";
        return;
      }
      var start = m[2] ? Number(m[2].replace(/,/g, "")) : null;
      var end = m[3] ? Number(m[3].replace(/,/g, "")) : start !== null ? start : null;
      if (start !== null && (start < 1 || end < start || end > len * 1e6 + 1e6)) {
        bad = bad || "“" + piece + "” is outside " + name(chrom) + " (" + len + " Mb)";
        return;
      }
      parts.push({ chrom: chrom, start: start, end: end });
    });
    return bad ? { error: bad, parts: parts } : { parts: parts };
  }

  function share(value) {
    var p = parse(value);
    if (!p.parts.length) return 1;
    return p.parts.reduce(function (sum, part) {
      var len = length(part.chrom);
      return sum + (part.start !== null ? (part.end - part.start + 1) / 1e6 : len);
    }, 0) / TOTAL;
  }

  function percent(fraction) {
    if (fraction >= 0.995) return "the whole genome";
    if (fraction >= 0.01) return Math.round(fraction * 100) + "% of the genome";
    if (fraction >= 0.0001) return (fraction * 100).toFixed(2) + "% of the genome";
    return "a small slice of the genome";
  }

  function geneFor(value, build) {
    var p = parse(value);
    if (p.parts.length !== 1 || p.parts[0].start === null) return null;
    var part = p.parts[0];
    return Object.keys(GENES).filter(function (g) {
      var span = GENES[g][grch37(build) ? 2 : 1];
      return GENES[g][0] === part.chrom && span[0] === part.start && span[1] === part.end;
    })[0] || null;
  }

  /* --- the control ---------------------------------------------------- */

  var MODES = [["whole", "Whole genome"], ["chroms", "Chromosomes"], ["gene", "A gene"], ["text", "Type it"]];

  function build(field) {
    var list = h("datalist", { id: "v2-genes-" + field.key }, Object.keys(GENES).map(function (g) {
      return h("option", { value: g }, GENES[g][3]);
    }));
    return h("div.v2-region", { dataset: { v2Region: "" } },
      h("input", { type: "hidden", "aria-label": field.label, dataset: { v2Key: field.key } }),
      h("div.v2-region__modes", { role: "group", "aria-label": "Which part of the genome" }, MODES.map(function (m) {
        return h("button", { type: "button", value: m[0], dataset: { v2RegionMode: m[0] } }, m[1]);
      })),
      h("div.v2-region__pane", { dataset: { v2RegionPane: "chroms" }, hidden: true },
        h("div.v2-chroms", { role: "group", "aria-label": "Chromosomes" }, CHROMS.map(function (c) {
          return h("button.v2-chrom", { type: "button", value: c[0], "aria-pressed": "false", title: c[1] + " Mb" }, c[0] === "M" ? "mt" : c[0]);
        })),
        h("div.v2-row.v2-row--wrap.v2-small",
          h("button.v2-link", { type: "button", dataset: { v2RegionPick: "auto" } }, "1–22"),
          h("button.v2-link", { type: "button", dataset: { v2RegionPick: "sex" } }, "X and Y"),
          h("button.v2-link", { type: "button", dataset: { v2RegionPick: "none" } }, "Clear")),
        h("div.v2-region__range", { hidden: true },
          h("span.v2-small.v2-muted", { dataset: { v2RegionOf: "" } }),
          h("input.v2-input", { type: "number", min: 0, step: "any", "aria-label": "From (Mb)", placeholder: "0", dataset: { v2RegionFrom: "", v2Keep: "off" }, style: { maxWidth: "110px" } }),
          h("span.v2-small", "to"),
          h("input.v2-input", { type: "number", min: 0, step: "any", "aria-label": "To (Mb)", placeholder: "end", dataset: { v2RegionTo: "", v2Keep: "off" }, style: { maxWidth: "110px" } }),
          h("span.v2-small.v2-muted", "Mb"),
          h("div.v2-region__bar", { "aria-hidden": "true" }, h("span")))),
      h("div.v2-region__pane", { dataset: { v2RegionPane: "gene" }, hidden: true },
        h("input.v2-input.v2-input--mono", { type: "text", list: "v2-genes-" + field.key, placeholder: "BRCA1", "aria-label": "Gene", dataset: { v2RegionGene: "", v2Keep: "off" }, style: { maxWidth: "200px" } }),
        list),
      h("div.v2-region__pane", { dataset: { v2RegionPane: "text" }, hidden: true },
        h("input.v2-input.v2-input--mono", { type: "text", placeholder: "chr20 or chr20:1000000-2000000, chr21", "aria-label": "Region text", dataset: { v2RegionText: "", v2Keep: "off" }, style: { maxWidth: "340px" } })),
      h("p.v2-small.v2-muted.v2-region__sum", { dataset: { v2RegionSum: "" }, "aria-live": "polite" }));
  }

  function buildOf(el) {
    var tool = el.closest("[data-tool]");
    return (tool && tool.dataset.build) || "GRCh38";
  }

  function chosen(el) {
    return WGSM.qsa(".v2-chrom[aria-pressed=true]", el).map(function (b) {
      return b.value;
    });
  }

  /* Write the hidden value from the active pane and tell listeners */
  function commit(el, value) {
    var input = WGSM.qs("input[type=hidden]", el);
    if (input.value === value) return paint(el);
    input.value = value;
    paint(el);
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function fromPane(el) {
    var mode = el.dataset.mode;
    var b = buildOf(el);
    if (mode === "chroms") {
      var picks = chosen(el);
      var from = Number(WGSM.qs("[data-v2-region-from]", el).value);
      var to = Number(WGSM.qs("[data-v2-region-to]", el).value);
      if (picks.length === 1 && (from > 0 || (to && to < length(picks[0])))) {
        return name(picks[0], b) + ":" + Math.max(1, Math.round(from * 1e6)) + "-" + Math.round((to || length(picks[0])) * 1e6);
      }
      return picks.map(function (c) {
        return name(c, b);
      }).join(",");
    }
    if (mode === "gene") {
      var gene = GENES[WGSM.qs("[data-v2-region-gene]", el).value.trim().toUpperCase()];
      if (!gene) return "";
      var span = gene[grch37(b) ? 2 : 1];
      return name(gene[0], b) + ":" + span[0] + "-" + span[1];
    }
    if (mode === "text") return WGSM.qs("[data-v2-region-text]", el).value.trim();
    return "";
  }

  function setMode(el, mode) {
    el.dataset.mode = mode;
    WGSM.qsa("[data-v2-region-mode]", el).forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.value === mode));
    });
    WGSM.qsa("[data-v2-region-pane]", el).forEach(function (pane) {
      pane.hidden = pane.dataset.v2RegionPane !== mode;
    });
  }

  /* Show a value in the panes: the mode it reads best in */
  function load(el) {
    var value = WGSM.qs("input[type=hidden]", el).value;
    var b = buildOf(el);
    var p = parse(value);
    var gene = geneFor(value, b);
    var mode = !value ? "whole" : gene ? "gene" : p.error || p.parts.length > 1 && p.parts.some(function (x) {
      return x.start !== null;
    }) ? "text" : "chroms";
    setMode(el, mode);
    WGSM.qs("[data-v2-region-gene]", el).value = gene || "";
    WGSM.qs("[data-v2-region-text]", el).value = value;
    var chroms = p.parts.map(function (x) {
      return x.chrom;
    });
    WGSM.qsa(".v2-chrom", el).forEach(function (b2) {
      b2.setAttribute("aria-pressed", String(chroms.indexOf(b2.value) >= 0));
    });
    var one = p.parts.length === 1 && p.parts[0].start !== null ? p.parts[0] : null;
    WGSM.qs("[data-v2-region-from]", el).value = one ? +(one.start / 1e6).toFixed(3) : "";
    WGSM.qs("[data-v2-region-to]", el).value = one ? +(one.end / 1e6).toFixed(3) : "";
    paint(el);
  }

  function paint(el) {
    var value = WGSM.qs("input[type=hidden]", el).value;
    var b = buildOf(el);
    var picks = chosen(el);
    var range = WGSM.qs(".v2-region__range", el);
    range.hidden = !(el.dataset.mode === "chroms" && picks.length === 1);
    if (!range.hidden) {
      var len = length(picks[0]);
      WGSM.qs("[data-v2-region-of]", el).textContent = "Part of " + name(picks[0], b) + " (" + len + " Mb):";
      var from = Number(WGSM.qs("[data-v2-region-from]", el).value) || 0;
      var to = Number(WGSM.qs("[data-v2-region-to]", el).value) || len;
      var bar = WGSM.qs(".v2-region__bar span", el);
      bar.style.setProperty("--lo", (from / len) * 100 + "%");
      bar.style.setProperty("--hi", (Math.min(to, len) / len) * 100 + "%");
    }
    var p = parse(value);
    var sum = WGSM.qs("[data-v2-region-sum]", el);
    var gene = geneFor(value, b);
    sum.dataset.state = p.error ? "bad" : "";
    sum.textContent = p.error
      ? p.error + "; preflight will stop the run. Valid chromosomes are " + name("1", b) + "–" + name("22", b) + ", " + name("X", b) + ", " + name("Y", b) + " and " + name("M", b) + "."
      : !value ? "Whole genome (every chromosome)." : (gene ? gene + ": " + GENES[gene][3] + ". " : "") + value.replace(/(\d{4,})/g, function (n) {
        return commas(n);
      }) + " · " + percent(share(value)) + (gene ? ", positions on " + b : "");
  }

  function onClick(event) {
    var el = event.target.closest("[data-v2-region]");
    if (!el) return;
    var mode = event.target.closest("[data-v2-region-mode]");
    if (mode) {
      setMode(el, mode.value);
      commit(el, fromPane(el));
      return;
    }
    var chip = event.target.closest(".v2-chrom");
    if (chip) {
      chip.setAttribute("aria-pressed", String(chip.getAttribute("aria-pressed") !== "true"));
      WGSM.qs("[data-v2-region-from]", el).value = "";
      WGSM.qs("[data-v2-region-to]", el).value = "";
      commit(el, fromPane(el));
      return;
    }
    var pick = event.target.closest("[data-v2-region-pick]");
    if (pick) {
      WGSM.qsa(".v2-chrom", el).forEach(function (b) {
        var auto = /^\d+$/.test(b.value);
        var on = pick.dataset.v2RegionPick === "auto" ? auto : pick.dataset.v2RegionPick === "sex" ? b.value === "X" || b.value === "Y" : false;
        b.setAttribute("aria-pressed", String(on));
      });
      commit(el, fromPane(el));
    }
  }

  function onInput(event) {
    var el = event.target.closest("[data-v2-region]");
    if (!el || event.target.type === "hidden") return;
    event.stopPropagation();
    commit(el, fromPane(el));
  }

  WGSM.v2region = {
    build: build,
    share: share,
    parse: parse,
    name: name,
    /* Show each picker's (restored) value; when the file's build changes,
     * rename its chromosomes (chr1 ↔ 1) */
    refresh: function (scope, build) {
      WGSM.qsa("[data-v2-region]", scope).forEach(function (el) {
        var tool = el.closest("[data-tool]");
        var before = tool && tool.dataset.build;
        if (tool) tool.dataset.build = build;
        if (before && before !== build) {
          var input = WGSM.qs("input[type=hidden]", el);
          var p = parse(input.value);
          var gene = geneFor(input.value, before);
          if (gene) {
            /* A gene sits elsewhere on the other build */
            var span = GENES[gene][grch37(build) ? 2 : 1];
            input.value = name(GENES[gene][0], build) + ":" + span[0] + "-" + span[1];
          } else if (!p.error && p.parts.length) {
            input.value = p.parts.map(function (x) {
              return name(x.chrom, build) + (x.start !== null ? ":" + x.start + "-" + x.end : "");
            }).join(",");
          }
        }
        if (!el.dataset.mode || before !== build) load(el);
      });
    },
    start: function () {
      document.addEventListener("click", onClick);
      ["input", "change"].forEach(function (type) {
        document.addEventListener(type, onInput, true);
      });
    },
  };
})();
