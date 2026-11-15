/* Command palette (⌘K / Ctrl+K, or any [data-palette] button): search
 * genomes, tasks and every server operation by name. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var dialog;
  var list;
  var input;
  var active = 0;

  function entries() {
    var genomes = WGSM.data.genomes.map(function (g) {
      return { label: g.name, sub: g.file, icon: "file-" + (g.type === "raw" ? "raw" : g.type), group: "Genomes", href: "directions/c/file.html?g=" + g.id };
    });
    var ops = WGSM.data.ops.map(function (o) {
      return { label: o[0], sub: o[1], icon: o[2], group: o[3], href: o[4] };
    });
    return genomes.concat(ops);
  }

  function render(query) {
    var q = query.trim().toLowerCase();
    var items = entries().filter(function (e) {
      return !q || (e.label + " " + e.sub + " " + e.group).toLowerCase().indexOf(q) >= 0;
    });
    list.replaceChildren();
    var lastGroup = "";
    items.forEach(function (e, i) {
      if (e.group !== lastGroup) {
        list.appendChild(h("li.palette__group.eyebrow", { role: "presentation" }, e.group));
        lastGroup = e.group;
      }
      list.appendChild(
        h(
          "li.palette__item",
          { role: "option", id: "pal-" + i, "aria-selected": String(i === 0), dataset: { href: e.href }, onclick: function () {
            go(e.href);
          } },
          WGSM.icon(e.icon),
          h("span.truncate", e.label),
          h("span.palette__op", e.sub)
        )
      );
    });
    if (!items.length) list.appendChild(h("li.empty.small", "No matches. Try “haplogroup”, “cram” or “vep”."));
    active = 0;
    input.setAttribute("aria-activedescendant", items.length ? "pal-0" : "");
  }

  function move(delta) {
    var options = WGSM.qsa(".palette__item", list);
    if (!options.length) return;
    active = (active + delta + options.length) % options.length;
    options.forEach(function (o, i) {
      o.setAttribute("aria-selected", String(i === active));
    });
    options[active].scrollIntoView({ block: "nearest" });
    input.setAttribute("aria-activedescendant", options[active].id);
  }

  function go(href) {
    location.href = WGSM.keepParams(WGSM.link(href));
  }

  function build() {
    input = h("input", { type: "text", placeholder: "Search genomes, tasks and tools…", "aria-label": "Search", role: "combobox", "aria-expanded": "true", "aria-controls": "palette-list", autocomplete: "off" });
    list = h("ul.palette__list", { id: "palette-list", role: "listbox", "aria-label": "Results" });
    dialog = h(
      "dialog.dialog.palette",
      { "aria-label": "Command palette", dataset: { annotId: "palette" } },
      h("div.palette__input", WGSM.icon("search"), input, h("kbd", "esc")),
      list
    );
    input.addEventListener("input", function () {
      render(input.value);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        move(event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Enter") {
        var option = WGSM.qsa(".palette__item", list)[active];
        if (option) go(option.dataset.href);
      }
    });
  }

  WGSM.palette = {
    open: function () {
      if (!dialog) build();
      input.value = WGSM.params.get("q") || "";
      render(input.value);
      WGSM.ui.open(dialog);
      input.focus();
    },
    init: function () {
      document.addEventListener("click", function (event) {
        if (!document.body.classList.contains("an-mode") && event.target.closest("[data-palette]")) WGSM.palette.open();
      });
      document.addEventListener("keydown", function (event) {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
          event.preventDefault();
          WGSM.palette.open();
        }
      });
      if (WGSM.params.has("palette")) WGSM.palette.open();
    },
  };
})();
