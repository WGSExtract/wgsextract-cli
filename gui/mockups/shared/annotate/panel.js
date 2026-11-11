/* The notes panel: every note on the page with replies, resolve/reopen,
 * jump-to, filters, where notes are saved, and Export/Import. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var panel;
  var listEl;
  var statusEl;
  var filterEl;
  var expanded = {};

  var COLORS = { theontho: "#1d5bd0", Randy: "#a3238f", AI: "#4b5563" };

  function avatar(name) {
    var color = COLORS[name];
    if (!color) {
      var hash = 0;
      for (var i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) % 360;
      color = "hsl(" + hash + " 55% 38%)";
    }
    return h("span.an-avatar", { style: { background: color }, "aria-hidden": "true" }, (name || "?").slice(0, 2).toUpperCase());
  }

  function act(change) {
    return WGSM.notes.apply(change).catch(function (err) {
      WGSM.toast("Couldn't save the note: " + err.message, "circle-alert");
    });
  }

  function storageStatus() {
    var file = "feedback/" + WGSM.page.id + ".json";
    var io = h(
      "div.an-row",
      h("button.an-btn", { type: "button", onclick: function () {
        WGSM.notes.exportFile();
      } }, WGSM.icon("download", "icon--sm"), "Export JSON"),
      h("label.an-btn", { tabindex: "0", onkeydown: function (event) {
        if (event.key === "Enter" || event.key === " ") event.currentTarget.querySelector("input").click();
      } }, WGSM.icon("upload", "icon--sm"), "Import JSON", h("input", { type: "file", accept: "application/json,.json", hidden: true, onchange: function (event) {
        var file = event.target.files[0];
        if (file) WGSM.notes.importFile(file).then(function () {
          WGSM.toast("Imported notes from " + file.name);
        }, function (err) {
          WGSM.toast("Import failed: " + err.message, "circle-alert");
        });
        event.target.value = "";
      } }))
    );
    if (WGSM.notes.mode === "server") {
      return h("div.an-store", h("div.an-row", h("span.an-dot"), h("span", "Saved to ", h("code", file))), WGSM.notes.error ? h("div.an-error", WGSM.notes.error) : null, io);
    }
    return h(
      "div.an-store.an-store--local",
      h("strong", "Notes are only in this browser."),
      h("span", "To share them, run ", h("code", "python3 gui/mockups/serve.py"), " and open the page from it, or Export JSON and save it as ", h("code", file), " in the repo. Import the committed file to see others' notes."),
      io
    );
  }

  function noteItem(note) {
    var open = expanded[note.id];
    var where = note.anchor
      ? h("button.an-link", { type: "button", onclick: function () {
          jump(note);
        } }, WGSM.icon("map-pin", "icon--sm"), note.anchor.label || note.anchor.annot)
      : h("span.an-muted", "Whole page");
    var state = WGSM.pins.placement(note.id);
    var placement = note.anchor && state && state !== "shown"
      ? h("div.an-muted", state === "missing" ? "Its element is gone from the page." : "Not visible in this view" + (note.context && note.context.viewport ? " (made in " + note.context.viewport + " view)" : "") + ".")
      : null;
    var replies = h(
      "ol.an-replies",
      note.replies.map(function (r) {
        return h("li", h("div.an-meta", avatar(r.author), h("strong", r.author), h("span.an-muted", WGSM.relTime(r.created))), h("p.an-text", r.text));
      })
    );
    var replyBox = h("textarea.an-input", { rows: 2, placeholder: "Reply…", "aria-label": "Reply to note " + note.number });
    return h(
      "li.an-item",
      { dataset: { note: note.id, status: note.status } },
      h(
        "div.an-meta",
        h("span.an-num", String(note.number)),
        avatar(note.author),
        h("strong", note.author),
        h("span.an-muted", WGSM.relTime(note.created)),
        note.status === "resolved" ? h("span.an-badge", "Resolved") : null
      ),
      h("p.an-text", note.text),
      h("div.an-row.an-row--start", where),
      placement,
      note.replies.length ? replies : null,
      open
        ? h(
            "div.an-reply",
            replyBox,
            h("div.an-row", h("button.an-btn.an-btn--primary", { type: "button", onclick: function () {
              var text = replyBox.value.trim();
              if (!text) return replyBox.focus();
              if (!WGSM.notes.author()) return WGSM.toast("Pick your name at the top of the panel first", "circle-alert");
              act({ op: "reply", id: note.id, reply: { id: WGSM.uid("r"), author: WGSM.notes.author(), text: text } });
            } }, "Reply"))
          )
        : null,
      h(
        "div.an-row.an-actions",
        h("button.an-btn", { type: "button", "aria-expanded": String(!!open), onclick: function () {
          expanded[note.id] = !open;
          render();
        } }, WGSM.icon("reply", "icon--sm"), open ? "Hide reply" : "Reply" + (note.replies.length ? " (" + note.replies.length + ")" : "")),
        h("button.an-btn", { type: "button", onclick: function () {
          act({ op: "status", id: note.id, status: note.status === "resolved" ? "open" : "resolved" });
        } }, WGSM.icon(note.status === "resolved" ? "undo-2" : "check", "icon--sm"), note.status === "resolved" ? "Reopen" : "Resolve"),
        h("button.an-btn.an-btn--danger", { type: "button", "aria-label": "Delete note " + note.number, onclick: function () {
          if (confirm("Delete note " + note.number + "? Resolving keeps the history; deleting doesn't.")) act({ op: "delete", id: note.id });
        } }, WGSM.icon("trash-2", "icon--sm"))
      )
    );
  }

  function render() {
    if (!panel) return;
    var notes = WGSM.notes.list();
    var counts = {
      open: notes.filter(function (n) {
        return n.status !== "resolved";
      }).length,
    };
    counts.resolved = notes.length - counts.open;
    counts.all = notes.length;
    WGSM.qsa("button", filterEl).forEach(function (b) {
      b.textContent = b.dataset.label + " (" + counts[b.value] + ")";
      b.setAttribute("aria-pressed", String(b.value === WGSM.annotate.filter));
    });
    statusEl.replaceChildren(storageStatus());
    var shown = notes.filter(function (n) {
      var f = WGSM.annotate.filter;
      return f === "all" || (f === "resolved") === (n.status === "resolved");
    });
    listEl.replaceChildren();
    shown.forEach(function (n) {
      listEl.appendChild(noteItem(n));
    });
    if (!shown.length) {
      listEl.appendChild(h("li.an-empty", counts.all ? "No notes in this filter." : "No notes yet. Turn on Annotate (A) and click anything on the page."));
    }
  }

  function jump(note) {
    var placed = WGSM.anchor.place(note.anchor);
    if (placed.el) placed.el.scrollIntoView({ block: "center", behavior: "smooth" });
    setTimeout(function () {
      WGSM.pins.schedule();
      WGSM.pins.flash(note.id);
    }, 350);
  }

  WGSM.panel = {
    isOpen: function () {
      return panel && !panel.hidden;
    },
    toggle: function (force) {
      var show = force === undefined ? panel.hidden : force;
      panel.hidden = !show;
      WGSM.chrome.notesBtn.setAttribute("aria-expanded", String(show));
      document.body.classList.toggle("an-panel-open", show);
      if (show) {
        render();
        WGSM.qs("h2", panel).focus();
      }
      WGSM.pins.schedule();
    },
    open: function (id) {
      var note = WGSM.notes.doc.notes[id];
      if (note && WGSM.annotate.filter !== "all" && (WGSM.annotate.filter === "resolved") !== (note.status === "resolved")) {
        WGSM.annotate.filter = "all";
      }
      expanded[id] = true;
      this.toggle(true);
      var item = WGSM.qs('.an-item[data-note="' + id + '"]', panel);
      if (item) {
        item.scrollIntoView({ block: "nearest" });
        item.classList.add("an-item--focus");
        setTimeout(function () {
          item.classList.remove("an-item--focus");
        }, 1200);
      }
      if (note && note.anchor) jump(note);
    },
    render: render,
    init: function () {
      filterEl = h(
        "div.an-filter",
        { role: "group", "aria-label": "Show notes" },
        [["open", "Open"], ["resolved", "Resolved"], ["all", "All"]].map(function (f) {
          return h("button.an-chip", { type: "button", value: f[0], dataset: { label: f[1] }, onclick: function () {
            WGSM.annotate.filter = f[0];
            render();
            WGSM.pins.schedule();
          } }, f[1]);
        })
      );
      statusEl = h("div");
      listEl = h("ol.an-list", { "aria-label": "Notes" });
      panel = h(
        "aside.an-panel",
        { hidden: true, "aria-label": "Notes on this page", "data-annot-ignore": "" },
        h(
          "div.an-panel__head",
          h("h2", { tabindex: "-1" }, "Notes"),
          h("span.an-muted.an-grow", WGSM.page.title),
          h("button.an-btn", { type: "button", "aria-label": "Close notes", onclick: function () {
            WGSM.panel.toggle(false);
          } }, WGSM.icon("x", "icon--sm"))
        ),
        h("div.an-panel__section", h("div.an-label", "You are"), WGSM.authorPicker()),
        h("div.an-panel__section", statusEl),
        h(
          "div.an-panel__section.an-row",
          filterEl,
          h("button.an-btn", { type: "button", onclick: function () {
            WGSM.annotate.addPageNote();
          } }, WGSM.icon("plus", "icon--sm"), "Page note")
        ),
        listEl
      );
      document.body.appendChild(panel);
      WGSM.on("notes", render);
      var lastPlacement = "";
      WGSM.on("pins-drawn", function (placement) {
        if (placement === lastPlacement) return;
        lastPlacement = placement;
        if (WGSM.panel.isOpen() && !panel.contains(document.activeElement)) render();
      });
    },
  };
})();
