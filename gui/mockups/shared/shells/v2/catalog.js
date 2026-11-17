/* All tools: the list on the left of every tool page, built from the tool
 * definitions, and the filter above it. On narrow screens it folds into a
 * "Tool: … Change" row. <nav data-render="v2-catalog" data-current="vep"> */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var GROUPS = ["Files", "Align", "Long reads", "Call variants", "Quality", "Annotate", "Reports", "Extract", "Lineage"];

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
    var head = heading(el);
    if (head) head.setAttribute("data-current", "");
    requestAnimationFrame(function () {
      fit(el);
      reveal(el);
    });
  };

  var KEY = "wgsm.v2.catalogScroll";

  /* The nearest group heading above the current tool */
  function heading(nav) {
    var n = nav.querySelector("a[aria-current]");
    while (n && n.tagName !== "H2") n = n.previousElementSibling;
    return n;
  }

  /* Height the sticky list may use: from where it sits now down to the
   * action bar (or the column's end), so its last item is never hidden. */
  function fit(nav) {
    var main = nav.closest(".v2-main");
    if (!main) return;
    var queued = false;
    var measure = function () {
      queued = false;
      var bar = main.querySelector(".v2-actionbar");
      var box = main.getBoundingClientRect();
      var bottom = bar ? bar.getBoundingClientRect().top : box.top + main.clientHeight;
      var h = bottom - Math.max(nav.getBoundingClientRect().top, box.top) - 16;
      nav.style.setProperty("--v2-catalog-h", Math.max(h, 200) + "px");
    };
    var later = function () {
      if (!queued) requestAnimationFrame(measure);
      queued = true;
    };
    main.addEventListener("scroll", later, { passive: true });
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(later);
      ro.observe(main);
      var bar = main.querySelector(".v2-actionbar");
      if (bar) ro.observe(bar);
    }
    measure();
  }

  /* Keep the list where it was on the previous tool page when the current
   * tool is visible there; otherwise centre the current tool. */
  function reveal(nav) {
    var link = nav.querySelector("a[aria-current]");
    if (!link || nav.scrollHeight <= nav.clientHeight) return;
    var saved = parseFloat(remember());
    var top = link.getBoundingClientRect().top - nav.getBoundingClientRect().top + nav.scrollTop;
    var input = nav.querySelector(".v2-input");
    var hidden = input ? input.offsetHeight + 8 : 0;
    if (isFinite(saved)) nav.scrollTop = saved;
    var inView = top - nav.scrollTop >= hidden && top + link.offsetHeight - nav.scrollTop <= nav.clientHeight;
    if (!inView) nav.scrollTop = top - (nav.clientHeight - link.offsetHeight) / 2;
  }

  function remember(value) {
    try {
      if (arguments.length) sessionStorage.setItem(KEY, value);
      return sessionStorage.getItem(KEY);
    } catch (err) {
      return null;
    }
  }

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
        var link = event.target.closest(".v2-catalog a[href]");
        if (link) remember(link.closest(".v2-catalog").scrollTop);
        var toggle = event.target.closest("[data-v2-catalog-toggle]");
        if (!toggle) return;
        var open = toggle.getAttribute("aria-expanded") !== "true";
        toggle.setAttribute("aria-expanded", String(open));
        toggle.parentElement.toggleAttribute("data-open", open);
      });
    },
  };
})();
