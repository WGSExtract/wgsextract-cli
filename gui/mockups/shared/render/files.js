/* Renderers for repeated, data-driven blocks. A page asks for one with
 * <div data-render="name" data-...></div>; WGSM.render.hydrate() fills it. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var blocks = {};

  var FT_ICON = {
    bam: "file-bam",
    cram: "file-cram",
    fastq: "file-fastq",
    vcf: "file-vcf",
    raw: "file-raw",
    fasta: "file-fasta",
  };

  WGSM.render = {
    blocks: blocks,
    hydrate: function (scope) {
      WGSM.qsa("[data-render]", scope).forEach(function (el) {
        var fn = blocks[el.dataset.render];
        if (!fn) {
          console.warn("Unknown render block: " + el.dataset.render);
          return;
        }
        el.replaceChildren();
        fn(el, el.dataset);
        el.removeAttribute("data-render");
      });
    },
    ftMark: function (type, size) {
      return h(
        "span.ft" + (size ? ".ft--" + size : ""),
        { dataset: { ft: type } },
        WGSM.icon(FT_ICON[type] || "file")
      );
    },
    ftLabel: function (genome) {
      return h("span.ft-label", { dataset: { ft: genome.type } }, genome.typeLabel);
    },
    fileHref: function (genome) {
      return WGSM.keepParams(WGSM.link("directions/c/file.html?g=" + genome.id));
    },
    refCell: function (genome) {
      return h(
        "span.row",
        h("span", genome.build),
        h("span.subtle", genome.ref),
        genome.refOk ? null : h("span.badge.badge--warn", WGSM.icon("triangle-alert"), "Missing")
      );
    },
  };

  function haplos(genome) {
    if (!genome.y && !genome.mt) return h("span.subtle", "—");
    return h(
      "span.row.mono.small",
      { style: { whiteSpace: "nowrap" } },
      genome.y ? h("span", { title: "Y haplogroup" }, genome.y) : null,
      genome.y && genome.mt ? h("span.subtle", "·") : null,
      genome.mt ? h("span", { title: "mtDNA haplogroup" }, genome.mt) : null
    );
  }

  function statusCell(genome) {
    var tone = { busy: "info", attention: "warn", ready: "" }[genome.status] || "";
    return h(
      "span.row.small",
      h("span.dot" + (tone ? ".dot--" + tone : ".dot--success")),
      h("span.truncate", genome.activity)
    );
  }

  /* <div data-render="library-table" data-limit="4" data-select="1" data-picked="hg002,hg004"> */
  blocks["library-table"] = function (el, opts) {
    var genomes = WGSM.data.genomes.slice(0, Number(opts.limit) || undefined);
    var selectable = opts.select === "1";
    var picked = (opts.picked || "").split(",");
    var head = h(
      "thead",
      h(
        "tr",
        selectable ? h("th", { style: { width: "1%" } }, h("input", { type: "checkbox", "aria-label": "Select all" })) : null,
        h("th", "Name"),
        h("th", "Build · reference"),
        h("th.num", "Coverage"),
        h("th.num", "Size"),
        h("th", "Haplogroups"),
        h("th", "Status"),
        h("th.actions", h("span.visually-hidden", "Actions"))
      )
    );
    var body = h(
      "tbody",
      genomes.map(function (g) {
        return h(
          "tr",
          { dataset: { annotId: "lib-row-" + g.id }, "aria-selected": selectable && picked.indexOf(g.id) >= 0 ? "true" : null },
          selectable
            ? h("td", { "data-label": "" }, h("input", { type: "checkbox", "aria-label": "Select " + g.name, checked: picked.indexOf(g.id) >= 0 }))
            : null,
          h(
            "td",
            h(
              "div.table__name",
              WGSM.render.ftMark(g.type, "sm"),
              h(
                "div.stack.stack--xs",
                { style: { gap: "0" } },
                h("a", { href: WGSM.render.fileHref(g) }, g.name),
                h("span.table__sub.truncate", g.file)
              )
            )
          ),
          h("td", { "data-label": "Build" }, WGSM.render.refCell(g)),
          h("td.num.hide-phone", { "data-label": "Coverage" }, g.coverage ? g.coverage.toFixed(1) + "×" : "—"),
          h("td.num", { "data-label": "Size" }, g.size),
          h("td.hide-phone", { "data-label": "Haplogroups" }, haplos(g)),
          h("td", { "data-label": "Status" }, statusCell(g)),
          h(
            "td.actions.hide-phone",
            h(
              "a.btn.btn--sm.btn--ghost.btn--icon",
              { href: WGSM.render.fileHref(g), "aria-label": "Open " + g.name },
              WGSM.icon("chevron-right")
            )
          )
        );
      })
    );
    el.appendChild(
      h("div.table-wrap", h("table.table.table--stack", { "aria-label": "Genome files" }, head, body))
    );
  };

  /* Card grid version of the library (Direction A, phone home) */
  blocks["library-cards"] = function (el, opts) {
    var genomes = WGSM.data.genomes.slice(0, Number(opts.limit) || undefined);
    el.classList.add("grid");
    el.style.setProperty("--grid-min", opts.min || "240px");
    genomes.forEach(function (g) {
      el.appendChild(
        h(
          "a.card.card--interactive",
          { href: WGSM.render.fileHref(g), dataset: { annotId: "lib-card-" + g.id } },
          h(
            "div.card__header",
            WGSM.render.ftMark(g.type),
            h("div.grow", h("div.card__title.truncate", g.name), h("div.xsmall.subtle.truncate", g.file))
          ),
          h(
            "div.row.row--wrap.small",
            WGSM.render.ftLabel(g),
            h("span.muted", g.build),
            h("span.subtle", "·"),
            h("span.muted", g.size),
            g.coverage ? h("span.subtle", "· " + g.coverage.toFixed(0) + "×") : null
          ),
          statusCell(g)
        )
      );
    });
  };

  /* The "Change" dialog for genome pickers, made once per page */
  function pickDialog() {
    if (WGSM.qs("#pick-genome")) return;
    document.body.appendChild(
      h(
        "dialog.dialog",
        { id: "pick-genome", "aria-labelledby": "pick-genome-title", dataset: { annotId: "pick-genome" } },
        h("div.dialog__header", h("h2.dialog__title", { id: "pick-genome-title" }, "Choose a genome file"), h("button.btn.btn--ghost.btn--icon", { type: "button", "data-close": "", "aria-label": "Close" }, WGSM.icon("x"))),
        h(
          "div.dialog__body",
          WGSM.data.genomes.map(function (g) {
            return h(
              "button.action",
              { type: "button", "data-close": "", "data-toast": "Using " + g.name },
              WGSM.render.ftMark(g.type),
              h("span", h("span.action__title", g.name), h("br"), h("span.action__desc", g.typeLabel + " · " + g.build + " · " + g.size)),
              g.refOk ? null : h("span.badge.badge--warn", "Needs setup")
            );
          })
        )
      )
    );
  }

  /* Compact file chooser row: the selected genome as a card with "Change" */
  blocks["genome-pick"] = function (el, opts) {
    pickDialog();
    var g = WGSM.data.genome(opts.genome || "hg002");
    el.appendChild(
      h(
        "div.card",
        { style: { padding: "var(--sp-3) var(--sp-4)" } },
        h(
          "div.row",
          WGSM.render.ftMark(g.type),
          h(
            "div.grow",
            h("div", h("strong", g.name)),
            h(
              "div.xsmall.subtle.row.row--wrap",
              h("span", g.file),
              h("span", "· " + g.build + " · " + g.size + (g.coverage ? " · " + g.coverage + "×" : ""))
            )
          ),
          h("button.btn.btn--sm", { type: "button", "data-open": "#pick-genome" }, "Change")
        )
      )
    );
  };
})();
