/* Version 2's demo state, kept in this browser so changes survive moving
 * between pages: renamed or removed people, whose the unassigned reads are,
 * the file picked on each tool page, every option set on a page, and a few
 * actions (cancel a job, fix a file).
 * Settings has a reset.
 *
 * Markup hooks:
 *   data-v2-person="id"        dialog that edits a person; inside it
 *     data-v2-field="name|note"  inputs, data-v2-save, data-v2-remove
 *   data-v2-note="id"          text = the person's note
 *   data-v2-group="id"         hidden when the person is removed
 *   data-v2-set="flag …"       clicking sets flags (data-v2-unset clears them)
 *   data-v2-if="flag"          shown only while the flag is set ("!flag": unset)
 *   data-v2-assign             saves the #assign dialog's choice
 *   data-v2-reset              clears all of it
 *   data-v2-keep="off"         a control whose value isn't remembered */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var KEY = "v2-state";

  var PEOPLE = {
    reginald: { name: "Reginald McDonald", note: "son" },
    gordon: { name: "Gordon McDonald", note: "father" },
    helen: { name: "Helen McDonald", note: "mother" },
    alex: { name: "Alex Moreno", note: "" },
  };

  function load() {
    try {
      return JSON.parse(WGSM.pref(KEY) || "{}") || {};
    } catch (err) {
      return {};
    }
  }

  var doc = load();
  ["people", "flags", "forms", "picks"].forEach(function (k) {
    doc[k] = doc[k] || {};
  });

  function save() {
    WGSM.pref(KEY, JSON.stringify(doc));
  }

  /* Generated files and made-up people, mirrored from the mock (see
   * api-files.js): {files: {key: FILES entry}, people: {id: {name, note}}} */
  function generated() {
    try {
      var g = JSON.parse(WGSM.pref("v2-generated") || "null") || {};
      return { files: g.files || {}, people: g.people || {} };
    } catch (err) {
      return { files: {}, people: {} };
    }
  }

  function person(id) {
    var base = PEOPLE[id] || generated().people[id] || { name: id, note: "" };
    var own = doc.people[id] || {};
    return { id: id, name: own.name || base.name, note: own.note !== undefined ? own.note : base.note, removed: !!own.removed };
  }

  function first(name) {
    return name.trim().split(/\s+/)[0];
  }

  function initials(name) {
    var words = name.trim().split(/\s+/);
    return ((words[0] || "?")[0] + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
  }

  function escape(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  /* Replace whole words in text nodes, text inputs and the title. File names
   * (reginald-mcdonald.cram, genome_Alex_…) don't match: - and _ join words. */
  function replaceWords(pairs, scope) {
    var rules = pairs
      .filter(function (p) {
        return p[0] && p[0] !== p[1];
      })
      .map(function (p) {
        return [new RegExp("(^|[^\\w-])" + escape(p[0]) + "(?![\\w-])", "g"), p[1]];
      });
    if (!rules.length) return;
    function fix(text) {
      rules.forEach(function (r) {
        text = text.replace(r[0], function (m, lead) {
          return lead + r[1];
        });
      });
      return text;
    }
    var walker = document.createTreeWalker(scope || document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var parent = node.parentElement;
        if (!parent || parent.closest("script, style, .v2-avatar, [data-v2-note], .an-panel, .an-composer")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      var next = fix(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
    });
    if (!scope) {
      WGSM.qsa("input[type=text]:not([data-v2-field])").forEach(function (input) {
        input.value = fix(input.value);
      });
      document.title = fix(document.title);
    }
  }

  /* Show one person under a new name everywhere on the page */
  function rename(from, to) {
    replaceWords([[from, to], [first(from), first(to)]]);
    WGSM.qsa(".v2-avatar").forEach(function (avatar) {
      if (avatar.textContent === initials(from)) avatar.textContent = initials(to);
    });
  }

  function applyPerson(id) {
    var p = person(id);
    WGSM.qsa('[data-v2-note="' + id + '"]').forEach(function (el) {
      el.textContent = p.note;
    });
    WGSM.qsa('[data-v2-group="' + id + '"]').forEach(function (el) {
      el.hidden = p.removed;
    });
    WGSM.qsa('[data-v2-person="' + id + '"]').forEach(function (dialog) {
      WGSM.qsa("[data-v2-field]", dialog).forEach(function (input) {
        input.value = p[input.dataset.v2Field];
      });
    });
  }

  function flag(name) {
    return !!doc.flags[name];
  }

  function applyFlags() {
    WGSM.qsa("[data-v2-if]").forEach(function (el) {
      var want = el.dataset.v2If;
      el.hidden = want.charAt(0) === "!" ? flag(want.slice(1)) : !flag(want);
    });
  }

  function setFlags(names, on) {
    names.split(" ").forEach(function (f) {
      if (on) doc.flags[f] = true;
      else delete doc.flags[f];
    });
    save();
    applyFlags();
    WGSM.emit("v2-flags");
  }

  /* The unassigned FASTQ pair: whose it became (an id, or a new name) */
  function ownerName() {
    var a = doc.assign;
    if (!a) return null;
    return a.id ? first(person(a.id).name) : a.name;
  }

  function applyAssign() {
    var a = doc.assign;
    if (!a) return;
    replaceWords([["Not assigned", ownerName()]]);
    var group = WGSM.qs('[data-annot-id="unassigned"]');
    if (!group) return;
    var target = a.id && WGSM.qs('[data-v2-group="' + a.id + '"] .v2-files');
    if (target) {
      WGSM.qsa(".v2-file", group).forEach(function (row) {
        target.appendChild(row);
      });
      group.hidden = true;
      return;
    }
    WGSM.qs(".v2-person__name", group).replaceChildren(a.name, " ", WGSM.h("small", "new"));
    WGSM.qs(".v2-avatar", group).textContent = initials(a.name);
    WGSM.qs(".v2-person__next", group).replaceChildren(
      WGSM.h("a.v2-btn.v2-btn--sm", { href: WGSM.keepParams("tool-align.html") }, WGSM.icon("align"), "Align · about 5 h")
    );
  }

  /* Options on the page, saved per page under a key from their label */
  function controls() {
    var seen = {};
    return WGSM.qsa("input, select, textarea, [data-seg]")
      .filter(function (el) {
        if (el.closest("dialog, nav, .v2-top, [data-v2-keep='off']")) return false;
        if (el.matches("[data-v2-commands], [type=search]")) return false;
        return el.matches("[data-seg]") || !el.closest("[data-seg]");
      })
      .map(function (el) {
        var base = el.matches("[data-seg]")
          ? "seg:" + el.dataset.seg
          : el.getAttribute("aria-label") || el.id || el.name || (el.closest("label") || el).textContent.trim();
        seen[base] = (seen[base] || 0) + 1;
        return { el: el, key: base + "#" + seen[base] };
      });
  }

  function readControl(el) {
    if (el.matches("[data-seg]")) {
      var on = WGSM.qs('button[aria-pressed="true"]', el);
      return on ? on.value : null;
    }
    if (el.type === "checkbox" || el.type === "radio") return el.checked;
    return el.value;
  }

  function writeControl(el, value) {
    if (el.matches("[data-seg]")) {
      var button = WGSM.qs('button[value="' + value + '"]', el);
      if (button && button.getAttribute("aria-pressed") !== "true") button.click();
    } else if (el.type === "checkbox" || el.type === "radio") {
      el.checked = !!value;
    } else if (!el.matches("select") || WGSM.qsa("option", el).some(function (o) {
      return o.value === value;
    })) {
      el.value = value;
    }
  }

  function restoreForm() {
    var saved = doc.forms[WGSM.page.id] || {};
    controls().forEach(function (c) {
      if (Object.prototype.hasOwnProperty.call(saved, c.key)) writeControl(c.el, saved[c.key]);
    });
  }

  function rememberForm() {
    var form = (doc.forms[WGSM.page.id] = {});
    controls().forEach(function (c) {
      form[c.key] = readControl(c.el);
    });
    save();
    WGSM.emit("v2-form");
  }

  function savePerson(dialog) {
    var id = dialog.dataset.v2Person;
    var before = person(id);
    var name = WGSM.qs('[data-v2-field="name"]', dialog).value.trim();
    var note = WGSM.qs('[data-v2-field="note"]', dialog).value.trim();
    if (!name) {
      WGSM.toast("A person needs a name", "circle-alert");
      return false;
    }
    doc.people[id] = Object.assign({}, doc.people[id], { name: name, note: note });
    save();
    rename(before.name, name);
    applyPerson(id);
    if (before.name !== name) WGSM.toast("Renamed to " + name);
    return true;
  }

  function assign(dialog) {
    var pick = WGSM.qs("select", dialog).value;
    var name = WGSM.qs("input", dialog).value.trim();
    if (pick === "new" && !name) {
      WGSM.toast("Type the new person’s name", "circle-alert");
      return false;
    }
    doc.assign = pick === "new" ? { name: name } : { id: pick };
    save();
    setTimeout(function () {
      location.reload();
    }, 700);
    return true;
  }

  /* Runs before shared/ui.js's handler, so a refused save keeps its dialog */
  function onClick(event) {
    if (document.body.classList.contains("an-mode")) return;
    var el = event.target.closest("[data-v2-save], [data-v2-remove], [data-v2-set], [data-v2-unset], [data-v2-assign], [data-v2-reset]");
    if (!el) return;
    var ok = true;
    if (el.hasAttribute("data-v2-save")) ok = savePerson(el.closest("[data-v2-person]"));
    if (el.hasAttribute("data-v2-assign")) ok = assign(el.closest("dialog"));
    if (!ok) {
      event.stopImmediatePropagation();
      return;
    }
    if (el.hasAttribute("data-v2-remove")) {
      var id = el.closest("[data-v2-person]").dataset.v2Person;
      doc.people[id] = Object.assign({}, doc.people[id], { removed: true });
      save();
      setTimeout(function () {
        location.href = WGSM.keepParams("home.html");
      }, 900);
    }
    if (el.dataset.v2Set) setFlags(el.dataset.v2Set, true);
    if (el.dataset.v2Unset) setFlags(el.dataset.v2Unset, false);
    if (el.hasAttribute("data-v2-reset")) {
      WGSM.pref(KEY, "{}");
      setTimeout(function () {
        location.reload();
      }, 700);
    }
  }

  WGSM.v2state = {
    doc: doc,
    save: save,
    person: person,
    first: first,
    initials: initials,
    flag: flag,
    ownerName: ownerName,
    generated: generated,
    replaceWords: replaceWords,
    /* File picked on a tool page, by FILES key (machine.js) */
    pick: function (key) {
      if (arguments.length) {
        doc.picks[WGSM.page.id] = key;
        save();
      }
      return doc.picks[WGSM.page.id];
    },
    setFlags: setFlags,
    people: function () {
      return Object.keys(PEOPLE).map(person);
    },
    /* Called once the page is built: show everything as it was left */
    apply: function () {
      Object.keys(PEOPLE).forEach(function (id) {
        rename(PEOPLE[id].name, person(id).name);
        applyPerson(id);
      });
      applyAssign();
      applyFlags();
      restoreForm();
      document.addEventListener("click", onClick, true);
      document.addEventListener("change", rememberForm);
      document.addEventListener("input", function (event) {
        if (event.target.matches("input[type=text], input[type=number], textarea")) rememberForm();
      });
      document.addEventListener("click", function (event) {
        if (event.target.closest("[data-seg] > button")) setTimeout(rememberForm, 0);
      });
    },
  };
})();
