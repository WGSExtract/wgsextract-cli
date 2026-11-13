/* Annotate mode: click an element (or drag a box) to drop a numbered note.
 * Keys: A toggles annotate mode, N toggles the notes panel, Escape leaves
 * the mode; in annotate mode Enter notes the focused element, so it works
 * from the keyboard too. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var hover;
  var drag;
  var hint;
  var start = null;

  function inStage(el) {
    return el && WGSM.chrome.vp.contains(el) && !el.closest("[data-annot-ignore]");
  }

  /* The view the note was written in, so a reviewer can reproduce it */
  function context() {
    var app = WGSM.qs(".app");
    return {
      depth: app ? app.dataset.depth : "",
      theme: WGSM.theme.current(),
      viewport: WGSM.chrome.stage.dataset.vp,
    };
  }

  function addNote(anchor, x, y) {
    WGSM.composer.open({
      x: x,
      y: y,
      label: anchor ? anchor.label : "Whole page",
      title: anchor ? "Note on " + (anchor.annot || "page") : "Page note",
      onSave: function (text) {
        WGSM.notes.apply({
          op: "add",
          note: { id: WGSM.uid("n"), author: WGSM.notes.author(), text: text, anchor: anchor, context: context() },
        }).then(function () {
          WGSM.toast("Note saved" + (WGSM.notes.mode === "server" ? " to feedback/" + WGSM.page.id + ".json" : " in this browser"));
        }, function (err) {
          WGSM.toast("Couldn't save: " + err.message, "circle-alert");
        });
      },
    });
  }

  function setMode(on) {
    WGSM.annotate.on = on;
    document.body.classList.toggle("an-mode", on);
    WGSM.chrome.annotBtn.setAttribute("aria-pressed", String(on));
    hint.hidden = !on;
    if (!on) {
      hover.hidden = true;
      drag.hidden = true;
      WGSM.composer.close();
    }
    WGSM.pins.schedule();
  }

  function box(el, rect) {
    el.style.left = rect.left + "px";
    el.style.top = rect.top + "px";
    el.style.width = rect.width + "px";
    el.style.height = rect.height + "px";
    el.hidden = false;
  }

  function regionFrom(event) {
    var left = Math.min(start.x, event.clientX);
    var top = Math.min(start.y, event.clientY);
    return { left: left, top: top, width: Math.abs(event.clientX - start.x), height: Math.abs(event.clientY - start.y) };
  }

  function onPointerDown(event) {
    if (!WGSM.annotate.on || !inStage(event.target) || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    start = { x: event.clientX, y: event.clientY, el: event.target };
  }

  function onPointerMove(event) {
    if (!WGSM.annotate.on) return;
    if (start) {
      if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 8) {
        hover.hidden = true;
        box(drag, regionFrom(event));
      }
      return;
    }
    var el = inStage(event.target) ? WGSM.anchor.target(event.target) : null;
    if (!el) {
      hover.hidden = true;
      return;
    }
    box(hover, el.getBoundingClientRect());
  }

  function onPointerUp(event) {
    if (!start) return;
    event.preventDefault();
    event.stopPropagation();
    var region = drag.hidden ? null : regionFrom(event);
    drag.hidden = true;
    var anchor = WGSM.anchor.describe(start.el, start, region);
    start = null;
    addNote(anchor, event.clientX, event.clientY);
  }

  /* Keep clicks in annotate mode from activating the page underneath */
  function swallow(event) {
    if (WGSM.annotate.on && inStage(event.target)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }

  function onKey(event) {
    if (WGSM.isTyping(event) || event.metaKey || event.ctrlKey || event.altKey) return;
    var key = event.key.toLowerCase();
    if (key === "a") {
      setMode(!WGSM.annotate.on);
    } else if (key === "n") {
      WGSM.panel.toggle();
    } else if (event.key === "Escape" && WGSM.annotate.on && !WGSM.composer.isOpen()) {
      setMode(false);
    } else if (event.key === "Enter" && WGSM.annotate.on && inStage(document.activeElement)) {
      event.preventDefault();
      event.stopPropagation();
      var el = document.activeElement;
      var r = el.getBoundingClientRect();
      var point = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      addNote(WGSM.anchor.describe(el, point), point.x, point.y);
    }
  }

  WGSM.annotate = {
    on: false,
    filter: "open",
    pinsHidden: false,
    setMode: setMode,
    addPageNote: function () {
      addNote(null);
    },
    updateCount: function () {
      var open = WGSM.notes.list().filter(function (n) {
        return n.status !== "resolved";
      }).length;
      var count = WGSM.chrome.countEl;
      count.textContent = String(open);
      count.toggleAttribute("data-zero", open === 0);
    },
    init: function () {
      hover = h("div.an-hover", { hidden: true, "data-annot-ignore": "" });
      drag = h("div.an-drag", { hidden: true, "data-annot-ignore": "" });
      hint = h("div.an-hint", { hidden: true, "data-annot-ignore": "", role: "status" }, "Annotate: click an element or drag a box to add a note. Esc to stop.");
      document.body.append(hover, drag, hint);
      WGSM.pins.init();
      WGSM.panel.init();
      WGSM.chrome.annotBtn.addEventListener("click", function () {
        setMode(!WGSM.annotate.on);
      });
      WGSM.chrome.notesBtn.addEventListener("click", function () {
        WGSM.panel.toggle();
      });
      document.addEventListener("pointerdown", onPointerDown, true);
      document.addEventListener("pointermove", onPointerMove, true);
      document.addEventListener("pointerup", onPointerUp, true);
      document.addEventListener("click", swallow, true);
      document.addEventListener("keydown", onKey, true);
      WGSM.on("notes", WGSM.annotate.updateCount);
      return WGSM.notes.init().then(function () {
        WGSM.annotate.updateCount();
        WGSM.pins.schedule();
        if (WGSM.params.get("pins") === "0") WGSM.annotate.pinsHidden = true;
        if (WGSM.params.has("notes")) WGSM.panel.toggle(true);
        if (WGSM.params.has("note")) WGSM.panel.open(WGSM.params.get("note"));
        if (WGSM.params.has("annotate")) setMode(true);
      });
    },
  };
})();
