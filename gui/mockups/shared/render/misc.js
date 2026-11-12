/* Data-driven blocks: ancestry bar, coverage chart, pairing QR code,
 * microarray destinations and the reference table. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var blocks = WGSM.render.blocks;

  /* <div data-render="ancestry-bar" data-genome="hg002" data-legend="1"> */
  blocks["ancestry-bar"] = function (el, opts) {
    var g = WGSM.data.genome(opts.genome || "hg002");
    var names = WGSM.data.ancestryK7;
    var parts = (g.ancestry || []).map(function (v, i) {
      return { name: names[i], v: v, i: i + 1 };
    }).filter(function (p) {
      return p.v > 0;
    });
    var label = parts.map(function (p) {
      return p.name + " " + p.v + "%";
    }).join(", ");
    el.appendChild(
      h(
        "div.stacked",
        { role: "img", "aria-label": "Ancestry proportions: " + label, style: { "--h": opts.height || "24px" } },
        parts.map(function (p) {
          return h("div.stacked__seg.seg-" + p.i, { style: { "--w": p.v + "%" }, title: p.name + " " + p.v + "%" });
        })
      )
    );
    if (opts.legend) {
      el.appendChild(
        h(
          "ul.legend",
          { style: { marginTop: "var(--sp-2)" } },
          parts.map(function (p) {
            return h("li", h("span.swatch.seg-" + p.i), h("span", p.name), h("strong.num", p.v + "%"));
          })
        )
      );
    }
  };

  var COVERAGE = [
    ["1", 31.9], ["2", 32.4], ["3", 32.1], ["4", 31.2], ["5", 31.8], ["6", 31.7], ["7", 31.0],
    ["8", 31.6], ["9", 29.8], ["10", 31.1], ["11", 31.4], ["12", 31.5], ["13", 30.9], ["14", 30.2],
    ["15", 29.7], ["16", 28.4], ["17", 29.1], ["18", 31.3], ["19", 26.2], ["20", 30.4], ["21", 29.0],
    ["22", 27.1], ["X", 15.6], ["Y", 13.9],
  ];

  blocks["coverage-bars"] = function (el) {
    var max = 36;
    el.classList.add("bars-wrap");
    el.appendChild(
      h(
        "div.bars",
        { role: "img", "aria-label": "Mean coverage per chromosome, about 31× on autosomes and 15× on X and Y", style: { "--h": "140px" } },
        COVERAGE.map(function (c) {
          var flag = c[0] === "X" || c[0] === "Y" ? "sex" : c[1] < 15 ? "low" : null;
          return h(
            "div.bars__col",
            { title: "chr" + c[0] + ": " + c[1] + "×" },
            h("div.bars__bar", { style: { "--v": (c[1] / max) * 100 + "%" }, dataset: flag ? { flag: flag } : {} }),
            h("span.bars__label", c[0])
          );
        })
      )
    );
  };

  /* An illustrative QR-like pattern (not a scannable code) */
  blocks.qr = function (el, opts) {
    var n = 29;
    var seed = 20261111;
    function rand() {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed / 2147483648;
    }
    function finder(x, y, cx, cy) {
      var dx = x - cx;
      var dy = y - cy;
      if (dx < 0 || dy < 0 || dx > 6 || dy > 6) return null;
      var ring = Math.max(Math.abs(dx - 3), Math.abs(dy - 3));
      return ring !== 2;
    }
    var rects = [];
    for (var y = 0; y < n; y++) {
      for (var x = 0; x < n; x++) {
        var f = finder(x, y, 0, 0);
        if (f === null) f = finder(x, y, n - 7, 0);
        if (f === null) f = finder(x, y, 0, n - 7);
        var on = f === null ? rand() > 0.52 : f;
        if (on) rects.push('<rect x="' + x + '" y="' + y + '" width="1" height="1"/>');
      }
    }
    el.innerHTML =
      '<svg viewBox="-2 -2 ' + (n + 4) + " " + (n + 4) + '" role="img" aria-label="' +
      (opts.label || "Pairing QR code") + '" style="width:' + (opts.size || "180px") +
      ';height:auto;background:#fff;border-radius:8px" shape-rendering="crispEdges"><g fill="#111a2e">' +
      rects.join("") + "</g></svg>";
  };

  /* Destination picker for DNA-test (microarray) files */
  blocks.destinations = function (el, opts) {
    var selected = (opts.selected || "gedmatch,myheritage").split(",");
    el.classList.add("grid");
    el.style.setProperty("--grid-min", opts.min || "230px");
    el.setAttribute("role", "group");
    el.setAttribute("aria-label", "Where will you use the files?");
    WGSM.data.destinations.forEach(function (d) {
      var on = selected.indexOf(d.id) >= 0;
      el.appendChild(
        h(
          "div.option" + (d.advanced ? ".adv-only" : ""),
          { dataset: { annotId: "dest-" + d.id } },
          h(
            "button.option__toggle",
            { type: "button", "aria-pressed": on ? "true" : "false", "data-toggle-pressed": "" },
            h("span.option__check", WGSM.icon("check")),
            h(
              "span.row",
              h("span.action__icon", { style: { "--tone": "color-mix(in srgb, var(--chart-" + d.tone + ") 50%, var(--c-text))", "--tone-soft": "color-mix(in srgb, var(--chart-" + d.tone + ") 14%, transparent)", width: "36px", height: "36px", fontWeight: "700", fontSize: "13px" }, "aria-hidden": "true" }, d.mark),
              h("span.stack.stack--xs", h("span.option__title", d.name), h("span.option__meta", d.makes))
            )
          ),
          h(
            "span.row.row--wrap",
            d.uploads
              ? h("span.badge.badge--success", WGSM.icon("upload"), "Accepts uploads")
              : h("span.badge", "Format only"),
            h("span.option__meta", d.markers)
          ),
          d.formats.length > 1
            ? h(
                "label.adv-only.row.small.option__extra",
                h("span.subtle", "Version"),
                h(
                  "select.select.select--sm",
                  { "aria-label": d.name + " version" },
                  d.formats.map(function (f) {
                    return h("option", { value: f[0] }, f[1]);
                  })
                )
              )
            : null
        )
      );
    });
  };

  /* Installed and available reference genomes */
  blocks.references = function (el, opts) {
    var installed = opts.installed === "1";
    var rows = WGSM.data.references.filter(function (r) {
      return !!r.installed === installed;
    });
    var head = installed
      ? ["Genome", "Build", "On disk", "Indexes", "Verified", ""]
      : ["Genome", "Build", "Download", "Used by", ""];
    el.appendChild(
      h(
        "div.table-wrap",
        h(
          "table.table.table--stack",
          h("thead", h("tr", head.map(function (t) {
            return h("th", t || h("span.visually-hidden", "Actions"));
          }))),
          h(
            "tbody",
            rows.map(function (r) {
              var name = h("td", h("div.table__name", WGSM.render.ftMark("fasta", "sm"), h("span.stack.stack--xs", { style: { gap: 0 } }, h("span", r.label), h("code.table__sub", r.code))));
              if (installed) {
                var missing = Object.keys(r.indexes).filter(function (k) {
                  return !r.indexes[k];
                });
                return h(
                  "tr",
                  { dataset: { annotId: "ref-" + r.code } },
                  name,
                  h("td", { "data-label": "Build" }, r.build),
                  h("td.num", { "data-label": "On disk" }, r.disk),
                  h("td", { "data-label": "Indexes" }, h("span.row.row--wrap", Object.keys(r.indexes).map(function (k) {
                    return h("span.badge" + (r.indexes[k] ? ".badge--success" : ""), r.indexes[k] ? WGSM.icon("check") : null, k);
                  }))),
                  h("td", { "data-label": "Verified" }, h("span.badge.badge--success", WGSM.icon("shield-check"), "OK")),
                  h("td.actions", missing.length ? h("button.btn.btn--sm", { type: "button", "data-toast": "Indexing " + r.code + " (" + missing.join(", ") + ")…" }, "Index " + missing[0]) : null)
                );
              }
              return h(
                "tr",
                { dataset: { annotId: "ref-" + r.code } },
                name,
                h("td", { "data-label": "Build" }, r.build, r.recommended ? h("span.badge.badge--primary", { style: { marginLeft: "6px" } }, "Recommended") : null),
                h("td.num", { "data-label": "Download" }, r.download, h("span.subtle", " → " + r.disk)),
                h("td.small.muted", { "data-label": "Used by" }, r.note),
                h("td.actions", h("button.btn.btn--sm.btn--soft", { type: "button", "data-toast": "Installing " + r.code + " — see Jobs" }, WGSM.icon("download"), "Install"))
              );
            })
          )
        )
      )
    );
  };
})();
