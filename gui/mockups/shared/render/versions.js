/* <div data-render="versions">: every version in shared/versions.js, newest
 * first, with its screens and a side-by-side link to the version before. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;

  WGSM.render.blocks.versions = function (el) {
    var versions = WGSM.versions;
    el.classList.add("version-list");
    versions.list.slice().reverse().forEach(function (v) {
      var index = versions.list.indexOf(v);
      var before = versions.list[index - 1];
      el.appendChild(
        h(
          "article.card.version",
          { dataset: { annotId: "version-" + v.id } },
          h("div.version__id", v.id),
          h(
            "div.stack.stack--sm",
            h("div", h("strong", v.name), h("span.muted", " · " + v.date)),
            h("p.small", v.summary),
            h(
              "ul.version__screens",
              versions.screens.filter(function (s) {
                return v.screens[s[0]];
              }).map(function (s) {
                var isNew = before && !before.screens[s[0]];
                return h(
                  "li",
                  h("a", { href: WGSM.keepParams(WGSM.link(versions.path(v.id, s[0]))) }, s[1]),
                  isNew ? h("span.version__new", " new") : null,
                  before && !isNew
                    ? [" ", h("a.subtle", { href: WGSM.link("compare.html") + "?screen=" + s[0] + "&a=" + before.id + "&b=" + v.id, title: "Compare with " + before.id }, "⇄")]
                    : null
                );
              })
            )
          )
        )
      );
    });
  };
})();
