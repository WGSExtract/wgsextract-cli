/* Generic app shell builder. A direction's shells/<x>.js calls
 * WGSM.defineShell(config) with its navigation; start.js then wraps the
 * page's content in sidebar + top bar + main + tab bar. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;

  function href(path) {
    return WGSM.keepParams(WGSM.link(path));
  }

  function current(item) {
    var nav = WGSM.page.nav;
    return item.id === nav || (item.also || []).indexOf(nav) >= 0;
  }

  function navItem(item) {
    return h(
      "a.nav-item" + (item.adv ? ".adv-only" : ""),
      { href: href(item.href), "aria-current": current(item) ? "page" : null, title: item.label, dataset: { annotId: "nav-" + item.id } },
      WGSM.icon(item.icon),
      h("span.nav-item__label", item.label),
      item.badge ? h("span.badge.badge--primary", item.badge) : null,
      item.dot ? h("span.dot.dot--" + item.dot, { "aria-label": item.dotLabel || "" }) : null
    );
  }

  function depthToggle() {
    return h(
      "div.seg.seg--block",
      { role: "group", "aria-label": "Detail level", "data-depth-toggle": "", dataset: { annotId: "depth-toggle" } },
      h("button.seg__btn", { type: "button", value: "basic" }, "Basic"),
      h("button.seg__btn", { type: "button", value: "advanced" }, "Advanced")
    );
  }

  function sidebar(config) {
    var job = WGSM.data.jobs[0];
    return h(
      "aside.sidebar",
      { "aria-label": "Main navigation", dataset: { annotId: "sidebar" } },
      h(
        "a.sidebar__brand",
        { href: href(config.home), "aria-label": config.brand },
        h("img", { src: WGSM.link("icons/logos/" + config.logo), alt: "", width: 28, height: 28 }),
        h("span", config.brand)
      ),
      config.groups.map(function (group) {
        return h(
          "nav.sidebar__group" + (group.adv ? ".adv-only" : ""),
          { "aria-label": group.heading || "Primary" },
          group.heading ? h("div.sidebar__heading.eyebrow", group.heading) : null,
          group.items.map(navItem)
        );
      }),
      h("div.sidebar__spacer"),
      config.sidebarJob
        ? h(
            "a.sidebar__job",
            { href: href(config.jobsHref), dataset: { annotId: "sidebar-job" } },
            h("span.row", h("span.spinner", { "aria-hidden": "true" }), h("strong.truncate", job.title)),
            h("div.progress", h("div.progress__fill", { style: { "--v": job.progress + "%" } })),
            h("span", job.progress + "% · " + job.eta)
          )
        : null,
      h("nav.sidebar__group", { "aria-label": "App" }, config.footer.map(navItem)),
      config.depthToggle ? h("div", { style: { padding: "var(--sp-2) var(--sp-1) 0" } }, depthToggle()) : null
    );
  }

  /* The page's own heading names it, so the top bar holds the way back
   * (data-parent="Label|path") and, on phones, the logo. */
  function topbar(config) {
    var parent = (document.body.dataset.parent || "").split("|");
    return h(
      "header.topbar",
      { dataset: { annotId: "topbar" } },
      h(
        "div.topbar__title.row",
        parent[1]
          ? h("a.btn.btn--ghost.btn--sm", { href: href(parent[1]) }, WGSM.icon("chevron-left"), parent[0])
          : config.layout === "topnav"
            ? h("a.sidebar__brand", { href: href(config.home), "aria-label": config.brand, style: { padding: 0 } }, h("img", { src: WGSM.link("icons/logos/" + config.logo), alt: "", width: 28, height: 28 }), h("span.hide-phone", config.brand))
            : h("img.only-phone", { src: WGSM.link("icons/logos/" + config.logo), alt: config.brand, width: 28, height: 28 })
      ),
      config.layout === "topnav"
        ? h("nav.topnav", { "aria-label": "Main navigation" }, config.groups[0].items.map(navItem))
        : null,
      config.topbar ? config.topbar() : null
    );
  }

  function tabbar(config) {
    return h(
      "nav.tabbar",
      { "aria-label": "Main navigation", dataset: { annotId: "tabbar" } },
      config.tabs.map(function (item) {
        return h(
          "a.tabbar__item",
          { href: href(item.href), "aria-current": current(item) ? "page" : null, dataset: { annotId: "tab-" + item.id } },
          WGSM.icon(item.icon),
          h("span", item.label),
          item.badge ? h("span.badge", item.badge) : null
        );
      })
    );
  }

  WGSM.defineShell = function (config) {
    WGSM.shell = {
      config: config,
      build: function (nodes) {
        var main = h("main.main", { id: "main" }, nodes);
        var app = h(
          "div.app" + (config.layout ? ".app--" + config.layout : ""),
          { dataset: { annotId: "app", depth: "basic" } },
          sidebar(config),
          topbar(config),
          main,
          tabbar(config)
        );
        /* Tablets get the icon rail; "rail" layouts always have it */
        new ResizeObserver(function () {
          app.classList.toggle("app--rail", config.layout === "rail" || app.clientWidth <= 1080);
        }).observe(app);
        return app;
      },
    };
  };

  /* Shared top-bar pieces */
  WGSM.shellParts = {
    search: function (placeholder) {
      return h(
        "button.topbar__search",
        { type: "button", "data-palette": "", title: "Search and commands (⌘K)", dataset: { annotId: "search" } },
        WGSM.icon("search", "icon--sm"),
        h("span", placeholder || "Search files, tasks, settings"),
        h("span.visually-hidden.only-phone", "Search"),
        h("kbd", "⌘K")
      );
    },
    jobsRing: function (jobsHref) {
      var job = WGSM.data.jobs[0];
      return h(
        "a.btn.btn--ghost.btn--sm",
        { href: href(jobsHref), title: "2 jobs, " + job.progress + "% done", dataset: { annotId: "jobs-ring" } },
        h("span.ring", { style: { "--v": job.progress } }, h("span", "2")),
        h("span.hide-phone", "Jobs"),
        h("span.visually-hidden", " jobs, " + job.progress + "% done")
      );
    },
    connection: function () {
      return h(
        "span.conn",
        { dataset: { annotId: "conn" }, title: "Connected to the WGS Extract server on this computer" },
        h("span.dot.dot--success"),
        h("span.conn__label", "This Mac")
      );
    },
  };
})();
