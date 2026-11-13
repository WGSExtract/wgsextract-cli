/* The note editor that pops up where you clicked, and the author picker it
 * shares with the notes panel. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var KNOWN = ["theontho", "Randy"];
  var current = null;

  /* Quick-pick for who is writing: remembered in this browser */
  WGSM.authorPicker = function (onChange) {
    var name = WGSM.notes.author();
    var other = h("input.an-input", {
      type: "text",
      placeholder: "Your name",
      "aria-label": "Your name",
      maxlength: "40",
      value: KNOWN.indexOf(name) < 0 ? name : "",
      oninput: function () {
        set(other.value.trim());
      },
    });
    var chips = h("div.an-authors", { role: "group", "aria-label": "You are" });
    function paint() {
      var me = WGSM.notes.author();
      var isOther = !!chips.dataset.other || (me !== "" && KNOWN.indexOf(me) < 0);
      WGSM.qsa("button", chips).forEach(function (b) {
        b.setAttribute("aria-pressed", String(b.value === "" ? isOther : b.value === me && !isOther));
      });
      other.hidden = !isOther;
    }
    function set(value) {
      WGSM.notes.author(value);
      paint();
      if (onChange) onChange(value);
    }
    KNOWN.concat([""]).forEach(function (value) {
      chips.appendChild(
        h("button.an-chip", { type: "button", value: value, onclick: function () {
          if (value === "") {
            chips.dataset.other = "1";
            set(other.value.trim());
            other.hidden = false;
            other.focus();
          } else {
            delete chips.dataset.other;
            set(value);
          }
        } }, value || "Other…")
      );
    });
    chips.appendChild(other);
    paint();
    return chips;
  };

  function close() {
    if (current) {
      current.remove();
      current = null;
    }
  }

  WGSM.composer = {
    isOpen: function () {
      return !!current;
    },
    close: close,
    /* opts: {x, y, label, title, onSave(text)} */
    open: function (opts) {
      close();
      var text = h("textarea.an-input.an-textarea", { "aria-label": "Note", placeholder: opts.placeholder || "What should change here, or what do you think?", rows: 4 });
      var error = h("div.an-error", { role: "alert" });
      function save() {
        if (WGSM.notes.mode === "server" && !WGSM.notes.available) {
          error.textContent = WGSM.notes.error;
          return;
        }
        var value = text.value.trim();
        if (!WGSM.notes.author()) {
          error.textContent = "Pick your name first.";
          return;
        }
        if (!value) {
          error.textContent = "Write a note first.";
          text.focus();
          return;
        }
        opts.onSave(value);
        close();
      }
      current = h(
        "div.an-composer",
        { role: "dialog", "aria-label": opts.title || "New note", "data-annot-ignore": "" },
        h("div.an-composer__head", h("strong", opts.title || "New note"), opts.label ? h("span.an-muted", opts.label) : null),
        WGSM.authorPicker(),
        text,
        error,
        h(
          "div.an-row",
          h("span.an-muted", "⌘/Ctrl + Enter to save"),
          h("button.an-btn", { type: "button", onclick: close }, "Cancel"),
          h("button.an-btn.an-btn--primary", { type: "button", onclick: save }, "Save note")
        )
      );
      current.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          event.stopPropagation();
          close();
        } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
          save();
        }
      });
      document.body.appendChild(current);
      var w = current.offsetWidth;
      var hgt = current.offsetHeight;
      var x = Math.min(Math.max(8, (opts.x || innerWidth / 2) + 12), innerWidth - w - 8);
      var y = Math.min(Math.max(52, (opts.y || innerHeight / 3) + 12), innerHeight - hgt - 8);
      current.style.left = x + "px";
      current.style.top = y + "px";
      text.focus();
    },
  };
})();
