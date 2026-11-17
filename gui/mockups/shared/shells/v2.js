/* Version 2, "People first": the sidebar lists the people in the library;
 * every task starts from one of them. Builds its own shell (not the generic
 * defineShell) because the navigation is data, not a fixed list of pages. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var V = "versions/v2/";

  /* The library by person, as named and kept in the demo state (v2/state.js) */
  var PAGES = { reginald: "person.html", gordon: "person-gordon.html", helen: "person-helen.html", alex: "person-alex.html" };
  var STATES = { reginald: "ok", gordon: "busy", helen: "ok", alex: "attention" };

  function people() {
    var S = WGSM.v2state;
    var list = S.people().filter(function (p) {
      return !p.removed;
    }).map(function (p) {
      var state = p.id === "alex" && S.flag("alex-fixed") ? "ok" : p.id === "gordon" && (S.flag("realign-cancelled") || S.flag("realign-done")) ? "ok" : STATES[p.id];
      return { id: p.id, name: S.first(p.name), note: p.id === "alex" && !p.note ? "2 files" : p.note, initials: S.initials(p.name), state: state, href: V + PAGES[p.id] };
    });
    var assigned = S.doc.assign;
    if (assigned && assigned.name) {
      list.push({ id: "new", name: S.first(assigned.name), note: "new", initials: S.initials(assigned.name), state: "attention", href: V + "home.html" });
    }
    return list;
  }

  var PLACES = [
    { id: "jobs", label: "Jobs", icon: "jobs", href: V + "jobs.html", count: "4", also: ["result"] },
    { id: "tools", label: "All tools", icon: "sliders-horizontal", href: V + "tools.html" },
    { id: "references", label: "Reference data", icon: "reference", href: V + "references.html" },
  ];
  var FOOT = [
    { id: "learn", label: "Learn", icon: "reference", href: V + "learn.html" },
    { id: "phone", label: "Phone access", icon: "remote", href: V + "phone.html" },
    { id: "settings", label: "Settings", icon: "settings", href: V + "settings.html" },
  ];
  var TABS = [
    { id: "home", label: "Library", icon: "library-big", href: V + "home.html", also: ["add", "p-reginald", "p-gordon", "p-helen", "p-alex"] },
    { id: "jobs", label: "Jobs", icon: "jobs", href: V + "jobs.html", count: "4", also: ["result"] },
    { id: "tools", label: "Tools", icon: "sliders-horizontal", href: V + "tools.html" },
    { id: "settings", label: "More", icon: "menu", href: V + "settings.html", also: ["references", "phone", "states", "learn"] },
  ];

  function href(path) {
    return WGSM.keepParams(WGSM.link(path));
  }

  function current(item) {
    var nav = WGSM.page.nav;
    return item.id === nav || (item.also || []).indexOf(nav) >= 0 ? "page" : null;
  }

  function link(item) {
    return h(
      "a.v2-nav",
      { href: href(item.href), "aria-current": current(item), dataset: { annotId: "nav-" + item.id } },
      WGSM.icon(item.icon),
      h("span.v2-nav__text", item.label),
      item.count ? h("span.v2-nav__count", item.count) : null
    );
  }

  function person(p) {
    return h(
      "a.v2-nav",
      { href: href(p.href), "aria-current": WGSM.page.nav === "p-" + p.id ? "page" : null, dataset: { annotId: "nav-" + p.id } },
      h("span.v2-avatar", p.initials),
      h("span.v2-nav__text", p.name, " ", h("span.v2-nav__sub", p.note)),
      p.state !== "ok" ? h("span.v2-pip", { dataset: { state: p.state }, title: p.state === "busy" ? "A job is running" : "Needs attention" }) : null
    );
  }

  function sidebar() {
    return h(
      "aside.v2-side",
      { "aria-label": "Main navigation", dataset: { annotId: "sidebar" } },
      h("a.v2-brand", { href: href(V + "home.html") }, h("img", { src: WGSM.link("icons/logos/logo-a-helix-bracket.svg"), alt: "", width: 26, height: 26 }), "WGS Extract"),
      h("nav", { "aria-label": "Library" },
        link({ id: "home", label: "Library", icon: "library-big", href: V + "home.html" }),
        people().map(person),
        link({ id: "add", label: "Add files", icon: "plus", href: V + "add.html" })
      ),
      h("div.v2-side__label", "On this computer"),
      h("nav", { "aria-label": "Work" }, PLACES.map(link)),
      h("div.v2-side__spacer"),
      /* Whichever job is running, from the (mock) server; hidden when none */
      h(
        "a.v2-sidejob",
        { href: href(V + "jobs.html"), hidden: true, dataset: { annotId: "sidebar-job", v2Job: "running", v2JobWhen: "running" } },
        h("strong", { dataset: { v2JobText: "label" } }, "Running"),
        h("div.v2-bar", { role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": "Progress", dataset: { v2JobBar: "" } }, h("span", { style: { "--v": "0%" } })),
        h("span", { dataset: { v2JobText: "" } })
      ),
      h("nav", { "aria-label": "App" }, FOOT.map(link))
    );
  }

  function topbar() {
    var parent = (document.body.dataset.parent || "").split("|");
    return h(
      "header.v2-top",
      { dataset: { annotId: "topbar" } },
      parent[1]
        ? h("a.v2-top__back", { href: href(V + parent[1]) }, WGSM.icon("chevron-left", "icon--sm"), parent[0])
        : h("img.v2-only-phone", { src: WGSM.link("icons/logos/logo-a-helix-bracket.svg"), alt: "WGS Extract", width: 28, height: 28 }),
      h("span.v2-top__grow"),
      h("button.v2-search", { type: "button", "data-palette": "", title: "Search people, files and tools", dataset: { annotId: "search" } },
        WGSM.icon("search", "icon--sm"), h("span.v2-hide-phone", "Search"), h("kbd.v2-hide-phone", M().os === "macos" ? "⌘K" : "Ctrl K"), h("span.visually-hidden", "Search")),
      h("button.v2-where", { type: "button", "data-open": "#v2-mock", title: "Connected to the WGS Extract server on " + M().label + ". Mockup: click to pretend another computer", dataset: { annotId: "conn" } },
        h("span.v2-pip", { dataset: { state: "ok" } }), h("span.v2-hide-phone", M().name))
    );
  }

  function M() {
    return WGSM.v2machine.machine;
  }

  /* Review controls, not app UI: which computer the mockup pretends to be,
   * the simulated clock and server (when present), and a reset */
  function mockDialog() {
    var editor = WGSM.v2machineEditor.build();
    var catalog = WGSM.v2generated.catalogEditor();
    return h("dialog.dialog.v2-mock", { id: "v2-mock", "aria-labelledby": "v2-mock-title", dataset: { v2NoTerms: "" } },
      h("div.dialog__header", h("h2.dialog__title", { id: "v2-mock-title" }, "Mockup settings")),
      h("div.dialog__body",
        h("p.v2-small.v2-muted", "Not part of the app. Pretend WGS Extract runs on another computer, or build your own, to see how pages, estimates and tools change."),
        editor.el,
        h("section", h("h3.v2-mock__h", "Simulation"), h("div", { dataset: { v2MockSlot: "" } })),
        catalog.el),
      h("div.dialog__footer.v2-mock__footer",
        h("button.v2-btn.v2-btn--sm.v2-btn--quiet", { type: "button", dataset: { v2Reset: "", toast: "The demo is back to how it started" } }, "Reset the demo"),
        h("span.v2-spacer"),
        h("button.v2-btn", { type: "button", "data-close": "" }, "Cancel"),
        h("button.v2-btn.v2-btn--primary", { type: "button", onclick: function (event) {
          var dialog = event.currentTarget.closest("dialog");
          /* The catalog first: a new computer reloads the page */
          catalog.apply().then(function (ok) {
            if (!ok) return;
            return editor.apply().then(function (reload) {
              if (reload) location.reload();
              else if (!WGSM.qs(".v2-notice:not([hidden])", dialog)) WGSM.qs("[data-close]", dialog).click();
            });
          });
        } }, "Apply")));
  }

  function tabbar() {
    return h("nav.v2-tabs", { "aria-label": "Main navigation", dataset: { annotId: "tabbar" } }, TABS.map(function (t) {
      return h("a.v2-tab", { href: href(t.href), "aria-current": current(t) }, WGSM.icon(t.icon), h("span", t.label), t.count ? h("span.v2-tab__count", t.count) : null);
    }));
  }

  /* Behaviours this version adds to shared/ui.js:
   *   data-v2-commands             switch: show parameter names and commands
   *   data-v2-count="#form"        text = number of checked boxes in #form
   *   data-v2-need="#form"         disabled while nothing in #form is checked
   *   data-v2-plural="file|files"  word that follows a data-v2-count */
  function commands(on) {
    WGSM.qs(".v2-app").dataset.commands = on ? "on" : "off";
    WGSM.pref("v2-commands", on ? "on" : "off");
    WGSM.qsa("[data-v2-commands]").forEach(function (box) {
      box.checked = on;
    });
  }

  function recount() {
    WGSM.qsa("[data-v2-count]").forEach(function (el) {
      var n = WGSM.qsa("input[type=checkbox]:checked", WGSM.qs(el.dataset.v2Count)).length;
      el.textContent = String(n);
      var word = el.nextElementSibling;
      if (word && word.dataset.v2Plural) word.textContent = word.dataset.v2Plural.split("|")[n === 1 ? 0 : 1];
    });
    WGSM.qsa("[data-v2-need]").forEach(function (button) {
      button.disabled = !WGSM.qs("input[type=checkbox]:checked", WGSM.qs(button.dataset.v2Need));
    });
  }

  WGSM.shell = {
    build: function (nodes) {
      /* shared/ui.js moves dialogs into the viewport frame */
      return h("div.v2-app", { dataset: { annotId: "app" } }, sidebar(), topbar(), h("main.v2-main", { id: "main" }, nodes), tabbar(), mockDialog());
    },
    palette: function () {
      var folks = people().map(function (p) {
        return { label: p.name, sub: p.note, icon: "user", group: "People", href: p.href };
      });
      var go = [
        ["Make DNA-test files", "microarray", "chip", "Do", V + "dna-test.html"],
        ["Ancestry & lineage", "lineage, qc/ancestry", "haplotree", "Do", V + "ancestry.html"],
        ["Convert or save space", "bam/to-cram", "convert", "Do", V + "convert.html"],
        ["Add files", "info", "plus", "Do", V + "add.html"],
        ["Jobs", "", "jobs", "Go to", V + "jobs.html"],
        ["Reference data", "ref/library", "reference", "Go to", V + "references.html"],
        ["Install tools", "deps/check", "wrench", "Go to", V + "install.html"],
        ["Learn: genomics basics & glossary", "DNA, files, variants, health, ancestry", "reference", "Go to", V + "learn.html"],
        ["Settings", "", "settings", "Go to", V + "settings.html"],
      ].map(function (o) {
        return { label: o[0], sub: o[1], icon: o[2], group: o[3], href: o[4] };
      });
      var T = WGSM.v2tools;
      var tools = T.order.map(function (slug) {
        var def = T.defs[slug];
        return { label: def.label, sub: def.op, icon: "sliders-horizontal", group: "Tools · " + def.group, href: V + T.pageOf(slug) };
      });
      return folks.concat(go, tools);
    },
  };

  WGSM.page.onReady = function () {
    commands((WGSM.params.get("commands") || WGSM.pref("v2-commands")) === "on");
    document.addEventListener("change", function (event) {
      if (event.target.matches("[data-v2-commands]")) commands(event.target.checked);
      recount();
    });
    WGSM.v2machineEditor.follow();
    /* Order matters: saved state renames and restores options first, then
     * the modules that read them start */
    WGSM.v2state.apply();
    WGSM.v2machine.localize();
    WGSM.v2region.start();
    WGSM.v2catalog.start();
    WGSM.v2tools.start();
    WGSM.v2generated.start();
    WGSM.v2edge.start();
    WGSM.v2edgeViews.start();
    WGSM.v2terms.start();
    WGSM.qs("[data-v2-mock-slot]").appendChild(WGSM.v2clock.control());
    /* The Jobs count: running, queued, and what needs you */
    WGSM.v2api.on(function () {
      WGSM.v2api.get("/v1/jobs").then(function (res) {
        if (res.status !== 200) return;
        var n = res.body.jobs.filter(function (j) {
          return ["running", "queued", "failed", "interrupted"].indexOf(j.status) >= 0;
        }).length;
        WGSM.qsa(".v2-nav__count, .v2-tab__count").forEach(function (el) {
          el.textContent = String(n);
          el.hidden = !n;
        });
      });
    });
    WGSM.v2terms.wrap(WGSM.qs("#main"));
    recount();
  };
})();
