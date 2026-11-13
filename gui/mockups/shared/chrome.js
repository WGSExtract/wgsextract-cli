/* Mockup chrome: the review bar above every mockup (back to index, direction,
 * viewport switcher, theme, annotate) and the stage that sizes the app
 * viewport to phone, tablet or desktop. Not part of the product UI. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;

  var DIRECTIONS = {
    A: "Guided",
    B: "Console",
    C: "Genome hub",
  };
  var VIEWPORTS = [
    ["fit", "Fit", "monitor", "Fit the window"],
    ["phone", "Phone", "smartphone", "Phone 390 × 844"],
    ["tablet", "Tablet", "tablet", "Tablet 820 × 1180"],
    ["desktop", "Desktop", "laptop", "Desktop 1440 × 900"],
  ];

  function viewportSwitcher(stage) {
    var group = h("div.mk-seg", { role: "group", "aria-label": "Preview size" });
    function select(mode) {
      stage.dataset.vp = mode;
      WGSM.qsa("button", group).forEach(function (b) {
        b.setAttribute("aria-pressed", String(b.value === mode));
      });
      WGSM.emit("viewport", mode);
    }
    VIEWPORTS.forEach(function (v) {
      group.appendChild(
        h(
          "button.mk-seg__btn",
          { type: "button", value: v[0], title: v[3], "aria-label": v[3], onclick: function () {
            WGSM.pref("vp", v[0]);
            select(v[0]);
          } },
          WGSM.icon(v[2], "icon--sm"),
          h("span.mk-hide-narrow", v[1])
        )
      );
    });
    select(WGSM.params.get("vp") || WGSM.pref("vp") || "fit");
    return group;
  }

  function themeButton() {
    var btn = h("button.mk-btn", { type: "button", onclick: function () {
      WGSM.theme.toggle();
    } });
    function paint() {
      var dark = WGSM.theme.current() === "dark";
      btn.replaceChildren(WGSM.icon(dark ? "sun" : "moon", "icon--sm"));
      btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
      btn.title = btn.getAttribute("aria-label");
    }
    WGSM.on("theme", paint);
    paint();
    return btn;
  }

  WGSM.chrome = {
    mount: function (appRoot) {
      var page = WGSM.page;
      var stage = h("div.mk-stage", h("div.mk-device", h("div.vp", { id: "vp" }, appRoot)));
      this.annotBtn = h(
        "button.mk-btn.mk-btn--annot",
        { type: "button", "aria-pressed": "false", title: "Annotate: click or drag on the page to leave a note (A)", "aria-keyshortcuts": "A" },
        WGSM.icon("message-square", "icon--sm"),
        h("span.mk-hide-narrow", "Annotate"),
        (this.countEl = h("span.mk-count", "0")),
        h("span.visually-hidden", "open notes")
      );
      this.notesBtn = h(
        "button.mk-btn",
        { type: "button", "aria-expanded": "false", title: "Show all notes on this page (N)", "aria-keyshortcuts": "N" },
        WGSM.icon("list", "icon--sm"),
        h("span.mk-hide-narrow", "Notes")
      );
      var dir = page.direction;
      var bar = h(
        "div.mk-bar",
        { "data-annot-ignore": "", role: "region", "aria-label": "Mockup review tools" },
        page.id === "index"
          ? h("span.mk-brand", WGSM.icon("helix", "icon--sm"), "Mockups")
          : h("a.mk-btn", { href: WGSM.keepParams(WGSM.link("index.html")), title: "All mockups" }, WGSM.icon("arrow-left", "icon--sm"), h("span.mk-hide-narrow", "Mockups")),
        dir ? h("span.mk-dir", { dataset: { dir: dir }, title: "Direction " + dir + ": " + (DIRECTIONS[dir] || "") }, dir) : null,
        h(
          "span.mk-title",
          h("strong", page.title),
          dir && DIRECTIONS[dir] ? h("span.mk-sub.mk-hide-narrow", "Direction " + dir + " · " + DIRECTIONS[dir] + (dir === "C" ? " (recommended)" : "")) : null
        ),
        viewportSwitcher(stage),
        themeButton(),
        this.annotBtn,
        this.notesBtn,
        h("a.mk-btn", { href: new URL(WGSM.link("notes.html"), location.href).href }, "All notes")
      );
      var version = window.WGSM_VERSION || {};
      var preview = version.source === "preview"
        ? h(
            "div.mk-preview",
            { role: "note", "data-annot-ignore": "" },
            h("strong", "Preview of branch " + version.branch + ", not main."),
            " Commit ",
            h("code", String(version.sha).slice(0, 7)),
            ". ",
            h("a", { href: WGSM.link("../../index.html") }, "Main mockups"),
            " · ",
            h("a", { href: WGSM.link("../index.html") }, "All previews")
          )
        : null;
      document.body.replaceChildren.apply(document.body, [bar, preview, stage].filter(Boolean));
      this.stage = stage;
      this.vp = WGSM.qs("#vp", stage);
    },
  };
})();
