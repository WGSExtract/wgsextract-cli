/* Numbered pins (and region boxes) drawn over the page for each note, kept
 * in place on scroll, resize, viewport switches and DOM changes. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var layer;
  var queued = false;
  var placement = {};

  function visibleNotes() {
    var filter = WGSM.annotate.filter;
    return WGSM.notes.list().filter(function (n) {
      if (!n.anchor) return false;
      if (filter === "open") return n.status !== "resolved";
      if (filter === "resolved") return n.status === "resolved";
      return true;
    });
  }

  function draw() {
    queued = false;
    if (!layer) return;
    var stage = WGSM.chrome.stage.getBoundingClientRect();
    layer.style.clipPath = "inset(" + stage.top + "px " + (innerWidth - stage.right) + "px " + (innerHeight - stage.bottom) + "px " + stage.left + "px)";
    layer.replaceChildren();
    var hidden = WGSM.annotate.pinsHidden;
    visibleNotes().forEach(function (note) {
      var placed = WGSM.anchor.place(note.anchor);
      var state = placed.missing ? "missing" : placed.hidden ? "hidden" : "shown";
      placement[note.id] = state;
      if (hidden || state !== "shown") return;
      if (placed.box) {
        layer.appendChild(
          h("div.an-region", {
            dataset: { status: note.status, note: note.id },
            style: { left: placed.box.left + "px", top: placed.box.top + "px", width: placed.box.width + "px", height: placed.box.height + "px" },
          })
        );
      }
      layer.appendChild(
        h(
          "button.an-pin",
          {
            type: "button",
            dataset: { status: note.status, note: note.id },
            style: { left: placed.x + "px", top: placed.y + "px" },
            "aria-label": "Note " + note.number + " by " + note.author + ": " + note.text.slice(0, 80),
            title: note.author + ": " + note.text.slice(0, 120),
            onclick: function (event) {
              event.stopPropagation();
              WGSM.panel.open(note.id);
            },
          },
          String(note.number)
        )
      );
    });
    WGSM.emit("pins-drawn", JSON.stringify(placement));
  }

  WGSM.pins = {
    /* "shown", "hidden" (display:none or scrolled away) or "missing" */
    placement: function (id) {
      return placement[id];
    },
    schedule: function () {
      if (queued) return;
      queued = true;
      requestAnimationFrame(draw);
    },
    flash: function (id) {
      var pin = WGSM.qs('.an-pin[data-note="' + id + '"]', layer);
      if (!pin) return;
      pin.classList.remove("an-pin--flash");
      void pin.offsetWidth;
      pin.classList.add("an-pin--flash");
    },
    init: function () {
      layer = h("div.an-layer", { "data-annot-ignore": "" });
      document.body.appendChild(layer);
      var schedule = this.schedule;
      addEventListener("resize", schedule);
      document.addEventListener("scroll", schedule, { capture: true, passive: true });
      new ResizeObserver(schedule).observe(WGSM.chrome.stage);
      new MutationObserver(schedule).observe(WGSM.chrome.vp, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden", "class", "open", "aria-selected", "data-depth"] });
      ["viewport", "theme", "depth", "notes"].forEach(function (name) {
        WGSM.on(name, schedule);
      });
      schedule();
    },
  };
})();
