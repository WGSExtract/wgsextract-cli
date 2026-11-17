/* All tools: the list on the left of every tool page, built from the tool
 * definitions, and the filter above it. On narrow screens it folds into a
 * "Tool: … Change" row. <nav data-render="v2-catalog" data-current="vep"> */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var GROUPS = ["Align and call", "Quality", "Annotate", "Reports", "Extract", "Lineage"];

  function tools(group) {
    var T = WGSM.v2tools;
    return T.order.map(function (slug) {
      return T.defs[slug];
    }).filter(function (def) {
      return def.group === group;
    });
  }

  WGSM.render.blocks["v2-catalog"] = function (el, opts) {
    var T = WGSM.v2tools;
    var current = T.defs[opts.current || "realign"];
    el.appendChild(h("button.v2-btn.v2-catalog__toggle", { type: "button", "aria-expanded": "false", dataset: { v2CatalogToggle: "" } },
      h("span.v2-grow", h("span.v2-muted", "Tool: "), current.label), "Change", WGSM.icon("chevron-down", "icon--sm")));
    el.appendChild(h("input.v2-input", { type: "search", placeholder: "Filter tools", "aria-label": "Filter tools", dataset: { v2Filter: "" } }));
    GROUPS.forEach(function (group) {
      el.appendChild(h("h2", group));
      tools(group).forEach(function (def) {
        el.appendChild(h("a", {
          href: WGSM.keepParams(T.pageOf(def.slug)),
          "aria-current": def === current ? "page" : null,
          dataset: { search: (def.label + " " + def.op + " " + (def.keywords || "")).toLowerCase() },
        }, def.label));
      });
    });
  };

  function filter(box) {
    var q = box.value.trim().toLowerCase();
    var nav = box.closest(".v2-catalog");
    WGSM.qsa("a", nav).forEach(function (a) {
      a.hidden = !!q && a.dataset.search.indexOf(q) < 0;
    });
    WGSM.qsa("h2", nav).forEach(function (head) {
      var any = false;
      for (var n = head.nextElementSibling; n && n.tagName === "A"; n = n.nextElementSibling) any = any || !n.hidden;
      head.hidden = !any;
    });
  }

  WGSM.v2catalog = {
    /* The page of the tool that runs a server operation, else All tools */
    pageFor: function (op) {
      var T = WGSM.v2tools;
      var slug = T.order.filter(function (s) {
        return (" " + T.defs[s].op.replace(/[·,]/g, " ") + " ").indexOf(" " + op + " ") >= 0;
      })[0];
      return T.pageOf(slug || "realign");
    },
    start: function () {
      document.addEventListener("input", function (event) {
        if (event.target.matches("[data-v2-filter]")) filter(event.target);
      });
      document.addEventListener("click", function (event) {
        var toggle = event.target.closest("[data-v2-catalog-toggle]");
        if (!toggle) return;
        var open = toggle.getAttribute("aria-expanded") !== "true";
        toggle.setAttribute("aria-expanded", String(open));
        toggle.parentElement.toggleAttribute("data-open", open);
      });
    },
  };
})();
