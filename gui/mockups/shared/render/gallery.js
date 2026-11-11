/* Blocks for the reference pages: the icon gallery and the design-token
 * swatches, read live from icons.js and the CSS custom properties so the
 * docs can't drift from the source. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var blocks = WGSM.render.blocks;

  /* <div data-render="icon-gallery" data-set="wgse|lucide"> */
  blocks["icon-gallery"] = function (el, opts) {
    var names = WGSM.icons.names().filter(function (name) {
      return !opts.set || WGSM.icons.meta(name).set === opts.set;
    });
    el.classList.add("icon-grid");
    names.forEach(function (name) {
      el.appendChild(
        h(
          "button.icon-tile",
          { type: "button", title: "Copy <i data-icon=\"" + name + "\"></i>", dataset: { name: name, annotId: "icon-" + name }, onclick: function () {
            var snippet = '<i data-icon="' + name + '"></i>';
            if (navigator.clipboard) navigator.clipboard.writeText(snippet).catch(function () {});
            WGSM.toast("Copied " + snippet);
          } },
          WGSM.icon(name),
          h("span.icon-tile__name", name)
        )
      );
    });
  };

  /* Live filter for the gallery: <input data-icon-filter> */
  document.addEventListener("input", function (event) {
    if (!event.target.matches("[data-icon-filter]")) return;
    var q = event.target.value.trim().toLowerCase();
    WGSM.qsa(".icon-tile").forEach(function (tile) {
      tile.hidden = q && tile.dataset.name.indexOf(q) < 0;
    });
  });

  /* Open/resolved notes per page, from serve.py or this browser */
  blocks["feedback-summary"] = function (el) {
    el.appendChild(h("p.small.subtle", "Loading notes…"));
    WGSM.on("notes-ready", function (mode) {
      WGSM.notes.summary().then(function (data) {
        var pages = (data.pages || []).filter(function (p) {
          return p.error || p.open + p.resolved > 0;
        });
        el.replaceChildren(
          h("p.small.muted", mode === "server"
            ? "Notes are saved in gui/mockups/feedback/ by serve.py; commit them to share."
            : "Notes are saved in this browser. Export and commit the JSON to gui/mockups/feedback/ (or open locally with just mockups to save directly)."),
          pages.length
            ? h(
                "div.table-wrap",
                h(
                  "table.table.table--compact",
                  { "aria-label": "Notes per page" },
                  h("thead", h("tr", h("th", "Page"), h("th.num", "Open"), h("th.num", "Resolved"))),
                  h("tbody", pages.map(function (p) {
                    return h(
                      "tr",
                      h("td", h("a", { href: WGSM.keepParams(WGSM.link(pagePath(p.page)) + "?notes=1") }, p.title || p.page), h("code.xsmall.subtle", " " + p.page), p.error ? h("div.field__error", p.error) : null),
                      h("td.num", String(p.open)),
                      h("td.num", String(p.resolved))
                    );
                  }))
                )
              )
            : h("p.small.subtle", "No notes yet.")
        );
      });
    });
  };

  /* Page ids are "<direction>-<file>" for mockups, else the page itself */
  function pagePath(id) {
    var match = /^([abc])-(.+)$/.exec(id);
    if (match) return "directions/" + match[1] + "/" + match[2] + ".html";
    return { index: "index.html", "design-system": "design-system/design-system.html", icons: "icons/icons.html" }[id] || "index.html";
  }

  /* <div data-render="swatches" data-tokens="--c-bg,--c-surface,..."> */
  blocks.swatches = function (el, opts) {
    el.classList.add("swatches");
    var style = getComputedStyle(document.documentElement);
    opts.tokens.split(",").forEach(function (token) {
      var value = h("code.swatch-card__value");
      el.appendChild(
        h(
          "div.swatch-card",
          { dataset: { annotId: "token" + token } },
          h("span.swatch-card__chip", { style: { background: "var(" + token + ")" } }),
          h("code", token),
          value
        )
      );
      function paint() {
        value.textContent = getComputedStyle(el).getPropertyValue(token).trim() || style.getPropertyValue(token).trim();
      }
      paint();
      WGSM.on("theme", paint);
    });
  };
})();
