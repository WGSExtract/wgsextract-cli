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

  /* Which iteration this is: the published copy names its commit */
  blocks.version = function (el) {
    var v = window.WGSM_VERSION || {};
    if ((v.source !== "pages" && v.source !== "preview") || !v.sha) {
      el.append("Local copy of the mockups (your checkout). The published site shows the commit it was built from here.");
      return;
    }
    var preview = v.source === "preview";
    el.append(
      "Published from commit ",
      h("a", { href: "https://github.com/WGSExtract/wgsextract-cli/commit/" + v.sha }, h("code", v.sha.slice(0, 7))),
      " of " + (preview ? "branch " + v.branch : "main") + ", " + v.date.replace("T", " ").replace(/([+-]\d\d:\d\d|Z)$/, " $1") + ". ",
      h("a", { href: WGSM.link(preview ? "../index.html" : "preview/index.html") }, "Branch previews")
    );
  };

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
