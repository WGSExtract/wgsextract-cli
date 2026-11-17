/*
 * Loads the mockup runtime in order. Each page links only this file (plus
 * the two stylesheets), so adding a shared script means editing SCRIPTS here,
 * not every page. Classic scripts rather than ES modules, because browsers
 * refuse module imports from file:// pages.
 */
(function () {
  "use strict";
  var self = document.currentScript;
  var root = new URL("../", self.src).href;
  var shell = document.body ? document.body.dataset.shell : "";
  var SCRIPTS = [
    "icons/icons.js",
    "shared/core.js",
    "shared/version.js",
    "shared/versions.js",
    "shared/icons.js",
    "shared/theme.js",
    "shared/data/genomes.js",
    "shared/data/jobs.js",
    "shared/data/catalog.js",
    "shared/render/files.js",
    "shared/render/jobs.js",
    "shared/render/misc.js",
    "shared/render/shell.js",
    "shared/render/gallery.js",
    "shared/render/versions.js",
    "shared/chrome.js",
    "shared/version-bar.js",
    "shared/ui.js",
    "shared/palette.js",
    "shared/annotate/about.js",
    "shared/annotate/store.js",
    "shared/annotate/anchor.js",
    "shared/annotate/pins.js",
    "shared/annotate/composer.js",
    "shared/annotate/panel.js",
    "shared/annotate/annotate.js",
  ];
  /* A shell's own modules, loaded before it (shared/shells/<shell>/) */
  var SHELL_MODULES = {
    v2: ["state", "machine-spec", "machine", "machine-editor", "glossary-data", "glossary", "learn", "region", "../../data/api-schema", "requests", "tools", "tools-align", "tools-call", "tools-quality",
      "tools-annotate", "tools-reports", "tools-extract", "tools-lineage", "catalog", "install",
      "api-core", "api-validate", "api-ops", "api-seed", "api-engine", "api-views", "api-routes", "api-proposed", "api-admin", "api-server",
      "api-clock", "api-bind", "api-jobs-cards", "api-jobs"],
  };
  if (shell && shell !== "none") {
    var at = SCRIPTS.indexOf("shared/chrome.js");
    var mine = (SHELL_MODULES[shell] || []).map(function (name) {
      return "shared/shells/" + shell + "/" + name + ".js";
    });
    SCRIPTS.splice.apply(SCRIPTS, [at, 0].concat(mine, ["shared/shells/" + shell + ".js"]));
  }
  SCRIPTS.push("shared/start.js");
  window.WGSM_ROOT = root;
  SCRIPTS.forEach(function (path) {
    var script = document.createElement("script");
    script.src = root + path;
    script.async = false;
    document.head.appendChild(script);
  });
})();
