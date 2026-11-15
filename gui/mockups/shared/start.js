/* Starts a mockup page once every script has loaded: wrap the content in the
 * direction's app shell (or a plain scrolling document), mount the review
 * chrome, fill data-driven blocks, then wire behaviours and annotations. */
(function () {
  "use strict";
  var WGSM = window.WGSM;

  var nodes = Array.prototype.filter.call(document.body.childNodes, function (node) {
    return node.nodeName !== "SCRIPT";
  });
  var root = WGSM.shell
    ? WGSM.shell.build(nodes)
    : WGSM.h("main.vp-doc", { dataset: { annotId: "doc" } }, nodes);

  WGSM.chrome.mount(root);
  WGSM.render.hydrate(document);
  WGSM.icons.hydrate(document);
  WGSM.ui.init();
  WGSM.palette.init();
  WGSM.annotate.init().catch(function (err) {
    console.error("Annotations unavailable:", err);
  });
  if (typeof WGSM.page.onReady === "function") WGSM.page.onReady();
  document.body.classList.add("mk-ready");
})();
