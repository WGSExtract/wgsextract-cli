/* Declarative behaviours for mockup markup, so pages need no scripts of
 * their own:
 *   data-depth-toggle         Basic/Advanced switch (remembered)
 *   data-seg="name"           exclusive choice among child buttons; elements
 *                             with data-when="name:value" show for that value
 *   data-toggle-pressed       button toggles its own aria-pressed
 *   role="tablist"            tabs with aria-controls panels, arrow keys
 *   data-open="#dialog"       opens a <dialog> inside the app viewport
 *   data-close                closes the enclosing dialog
 *   data-toast="text"         shows a toast
 *   data-dropzone="#result"   drop/click: "reads" the file, then shows #result
 *   data-reveal="#target"     short loading state, then shows #target
 *   data-steps / data-next / data-prev   wizard steps with a .stepper
 */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var lastFocus = null;

  function vp() {
    return WGSM.chrome.vp;
  }

  function setDepth(depth) {
    var app = WGSM.qs(".app");
    if (app) app.dataset.depth = depth;
    WGSM.pref("depth", depth);
    WGSM.qsa("[data-depth-toggle] button").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.value === depth));
    });
    WGSM.emit("depth", depth);
  }

  function choose(group, button) {
    WGSM.qsa(":scope > button, :scope > * > button.option", group).forEach(function (b) {
      b.setAttribute("aria-pressed", String(b === button));
    });
    var name = group.dataset.seg;
    if (name === "theme" && WGSM.theme.choice !== button.value) WGSM.theme.set(button.value);
    WGSM.qsa('[data-when^="' + name + ':"]').forEach(function (el) {
      el.hidden = el.dataset.when !== name + ":" + button.value;
    });
  }

  function selectTab(tab) {
    var list = tab.closest('[role="tablist"]');
    WGSM.qsa('[role="tab"]', list).forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      var panel = document.getElementById(t.getAttribute("aria-controls"));
      if (panel) panel.hidden = !on;
    });
  }

  function openDialog(dialog) {
    if (!dialog || dialog.open) return;
    lastFocus = document.activeElement;
    var backdrop = h("div.vp-backdrop", { onclick: function () {
      closeDialog(dialog);
    } });
    dialog._backdrop = backdrop;
    vp().appendChild(backdrop);
    vp().appendChild(dialog);
    dialog.show();
    var first = dialog.querySelector("[autofocus], input, select, textarea, button, a[href]");
    if (first) first.focus();
  }

  function closeDialog(dialog) {
    if (!dialog || !dialog.open) return;
    dialog.close();
    if (dialog._backdrop) dialog._backdrop.remove();
    if (lastFocus && lastFocus.isConnected) lastFocus.focus();
  }

  WGSM.toast = function (text, icon) {
    var host = WGSM.qs(".toasts", vp());
    if (!host) {
      host = h("div.toasts", { role: "status", "aria-live": "polite" });
      vp().appendChild(host);
    }
    var body = h("div.toast__body");
    body.textContent = String(text);
    var toast = h("div.toast", WGSM.icon(icon || "circle-check"), body);
    host.appendChild(toast);
    setTimeout(function () {
      toast.remove();
    }, 3600);
  };

  function simulateLoad(button, target, done) {
    var label = button.dataset.loading || "Reading…";
    var original = Array.prototype.slice.call(button.childNodes);
    button.replaceChildren(h("span.spinner", { "aria-hidden": "true" }), h("span", label));
    button.setAttribute("aria-busy", "true");
    setTimeout(function () {
      button.replaceChildren.apply(button, original);
      button.removeAttribute("aria-busy");
      if (target) target.hidden = false;
      if (done) done();
    }, 1100);
  }

  function dropzone(zone) {
    var target = WGSM.qs(zone.dataset.dropzone);
    function accept() {
      zone.dataset.state = "reading";
      var title = WGSM.qs(".dropzone__title", zone);
      var before = title ? title.textContent : "";
      if (title) title.textContent = "Reading the file header…";
      setTimeout(function () {
        zone.dataset.state = "";
        if (title) title.textContent = before;
        if (target) {
          target.hidden = false;
          target.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      }, 1100);
    }
    ["dragenter", "dragover"].forEach(function (type) {
      zone.addEventListener(type, function (event) {
        event.preventDefault();
        zone.dataset.state = "over";
      });
    });
    zone.addEventListener("dragleave", function () {
      zone.dataset.state = "";
    });
    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      accept();
    });
    WGSM.qsa("[data-browse]", zone).forEach(function (btn) {
      btn.addEventListener("click", accept);
    });
  }

  function steps(container) {
    var panels = WGSM.qsa("[data-step]", container);
    var items = WGSM.qsa(".stepper__item", container);
    var index = 0;
    function show(i) {
      index = Math.max(0, Math.min(panels.length - 1, i));
      panels.forEach(function (p, n) {
        p.hidden = n !== index;
      });
      items.forEach(function (item, n) {
        item.setAttribute("aria-current", n === index ? "step" : "false");
        item.dataset.state = n < index ? "done" : "";
      });
    }
    container.addEventListener("click", function (event) {
      if (event.target.closest("[data-next]")) show(index + 1);
      if (event.target.closest("[data-prev]")) show(index - 1);
      var item = event.target.closest(".stepper__item");
      if (item && items.indexOf(item) < index) show(items.indexOf(item));
    });
    show(Number(container.dataset.start || 0));
  }

  function onClick(event) {
    if (document.body.classList.contains("an-mode")) return;
    var el = event.target.closest("button, a, [data-open], [data-toast], [data-reveal]");
    if (!el) return;
    var group = el.parentElement && el.parentElement.closest("[data-seg]");
    if (el.closest("[data-depth-toggle]")) setDepth(el.value);
    else if (group && el.matches("button") && !el.hasAttribute("data-toggle-pressed")) choose(group, el);
    if (el.hasAttribute("data-toggle-pressed")) {
      el.setAttribute("aria-pressed", String(el.getAttribute("aria-pressed") !== "true"));
    }
    if (el.getAttribute("role") === "tab") selectTab(el);
    if (el.dataset.open) {
      event.preventDefault();
      openDialog(WGSM.qs(el.dataset.open));
    }
    if (el.hasAttribute("data-close")) closeDialog(el.closest("dialog"));
    if (el.dataset.toast) WGSM.toast(el.dataset.toast);
    if (el.dataset.reveal) simulateLoad(el, WGSM.qs(el.dataset.reveal));
  }

  function onKey(event) {
    var tab = event.target.closest && event.target.closest('[role="tab"]');
    if (tab && (event.key === "ArrowRight" || event.key === "ArrowLeft")) {
      var tabs = WGSM.qsa('[role="tab"]', tab.closest('[role="tablist"]'));
      var next = tabs[(tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
      selectTab(next);
      next.focus();
    }
    if (event.key === "Escape") {
      var open = WGSM.qsa("dialog[open]", vp()).pop();
      if (open) closeDialog(open);
    }
  }

  WGSM.ui = {
    open: openDialog,
    close: closeDialog,
    init: function () {
      WGSM.qsa("dialog").forEach(function (d) {
        if (!vp().contains(d)) vp().appendChild(d);
      });
      setDepth(WGSM.params.get("depth") || WGSM.pref("depth") || "basic");
      WGSM.qsa("[data-seg]").forEach(function (group) {
        var pressed = group.dataset.seg === "theme"
          ? WGSM.qs('button[value="' + WGSM.theme.choice + '"]', group)
          : WGSM.qs('button[aria-pressed="true"]', group) || WGSM.qs("button", group);
        if (pressed && !group.hasAttribute("data-depth-toggle")) choose(group, pressed);
      });
      WGSM.qsa('[role="tab"][aria-selected="true"]').forEach(selectTab);
      WGSM.qsa("[data-dropzone]").forEach(dropzone);
      WGSM.qsa("[data-steps]").forEach(steps);
      WGSM.qsa('a[href$=".html"], a[href*=".html?"], a[href*=".html#"]').forEach(function (a) {
        a.href = WGSM.keepParams(a.getAttribute("href"));
      });
      document.addEventListener("click", onClick);
      document.addEventListener("keydown", onKey);
    },
  };
})();
