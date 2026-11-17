/* Adds the version switch (v1 / v2 / …) and the Compare link to the review
 * bar on pages listed in shared/versions.js, and the bar-less "embed" view
 * that compare.html frames. Kept apart from chrome.js because the hosted
 * review server serves chrome.js and core.js from its own checkout, not
 * from the branch being previewed. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var here = WGSM.versions.locate(location.pathname);
  var embed = WGSM.params.has("embed");

  /* Links inside a framed page stay bar-less */
  var keepParams = WGSM.keepParams;
  WGSM.keepParams = function (href) {
    var kept = keepParams(href);
    if (!embed) return kept;
    var url = new URL(kept, location.href);
    url.searchParams.set("embed", "1");
    return url.href;
  };

  function switcher() {
    var group = h("div.mk-seg", { role: "group", "aria-label": "Design version" });
    WGSM.versions.list.forEach(function (v) {
      var path = WGSM.versions.path(v.id, here.screen);
      var title = v.id + " · " + v.name + " (" + v.date + ")";
      group.appendChild(
        path
          ? h("a.mk-seg__btn", { href: WGSM.keepParams(WGSM.link(path)), title: title, "aria-current": v.id === here.version ? "page" : null }, v.id)
          : h("span.mk-seg__btn", { "aria-disabled": "true", title: "This screen is not in " + v.id }, v.id)
      );
    });
    return group;
  }

  var mount = WGSM.chrome.mount;
  WGSM.chrome.mount = function (root) {
    mount.call(this, root);
    if (embed) document.body.classList.add("mk-embed");
    var title = WGSM.qs(".mk-bar .mk-title");
    if (!here || !title) return;
    var version = WGSM.versions.get(here.version);
    if (!WGSM.qs(".mk-sub", title)) title.appendChild(h("span.mk-sub.mk-hide-narrow", version.id + " · " + version.name));
    title.after(
      switcher(),
      h("a.mk-btn", { href: WGSM.link("compare.html") + "?screen=" + here.screen, title: "Show this screen in two versions side by side" }, WGSM.icon("panel-left", "icon--sm"), h("span.mk-hide-narrow", "Compare"))
    );
  };
})();
