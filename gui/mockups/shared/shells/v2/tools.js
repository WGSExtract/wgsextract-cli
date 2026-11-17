/* The v2 tool pages: one engine renders every tool from its definition (the
 * tools-*.js files), and recomputes the request, the command, the estimates
 * and the checks whenever an option or the input file changes.
 *
 * A definition: { slug, label, ops: [API operations], group, desc, accepts:
 * ["reads"|"vcf"|"fastq"|"kit"], file, requires: [tools this computer must
 * have], learn (plain-language "what is this for", paragraphs split by blank
 * lines), lesson (optional {href, label}), fields, more, moreTitle, checklist, extra(ctx), requests(ctx, base),
 * run(ctx) }. noInput hides the library-file picker for explicit path inputs;
 * allowUnready lets inspection and repair run on files needing attention.
 * Field keys are the operations' Params names (requests.js checks
 * them against the API schema); a field the API doesn't take says ui: true.
 * requests(ctx, base) returns [{op, params}] when a run isn't one request
 * of the first operation; base(op, extra) builds one from the fields.
 * run(ctx) returns { hours, memGB, disk: [[where, GB]], checks, notes,
 * warnings, makes, blocked, button, timeText, jobName }; timeText stands in
 * for hours when no rate is documented. ctx has v (option values by key),
 * file, name (whose file), M (the machine), m (machine.js helpers), flag()
 * and no(tool), why this computer can't run a tool. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var defs = {};
  var order = [];

  /* Field constructors. key is the Params name the field sets; the page
   * shows it beside the label when "parameter names" are on. */
  var f = {
    /* Control, options and default from the API schema */
    param: function (key, label, small, extra) {
      return Object.assign({ type: "param", key: key, label: label, small: small }, extra);
    },
    select: function (key, label, small, options, def, extra) {
      return Object.assign({ type: "select", key: key, label: label, small: small, options: options, def: def }, extra);
    },
    seg: function (key, label, small, options, def, extra) {
      return Object.assign({ type: "seg", key: key, label: label, small: small, options: options, def: def }, extra);
    },
    toggle: function (key, label, small, def, extra) {
      return Object.assign({ type: "switch", key: key, label: label, small: small, def: !!def }, extra);
    },
    number: function (key, label, small, def, extra) {
      return Object.assign({ type: "number", key: key, label: label, small: small, def: def }, extra);
    },
    text: function (key, label, small, placeholder, extra) {
      return Object.assign({ type: "text", key: key, label: label, small: small, placeholder: placeholder, def: "" }, extra);
    },
    show: function (label, small, value) {
      return { type: "show", key: "_" + label, label: label, small: small, value: value, ui: true };
    },
    region: function (small) {
      return { type: "region", key: "region", label: "Region", small: small || "", def: "" };
    },
    /* Empty means every core (the API's null); estimates use c.v.threads || cores */
    threads: function () {
      return f.number("threads", "CPU threads", function (c) {
        return "Empty: all " + c.M.cores + " cores";
      }, "", { width: 90, placeholder: "auto" });
    },
    sortMemory: function (small) {
      return f.select("memory", "Sort memory per thread", small || "Auto: a quarter of the RAM, at least 1 GB per thread",
        [["", "Auto (1 GB)"], ["2G", "2 GB"], ["768M", "768 MB"], ["512M", "512 MB"], ["256M", "256 MB"]], "", { width: 200 });
    },
  };

  /* Static markup a definition supplies (a table of earlier results) */
  function html(el, markup) {
    el.innerHTML = markup;
    return el;
  }

  function resolve(value, ctx) {
    return typeof value === "function" ? value(ctx) : value;
  }

  function define(def) {
    def.op = def.ops.join(" · ");
    WGSM.v2requests.resolve(def);
    defs[def.slug] = def;
    order.push(def.slug);
  }

  function pageOf(slug) {
    return slug === "realign" ? "tools.html" : "tool-" + slug + ".html";
  }

  /* --- reading the form --------------------------------------------- */

  function readField(field, root) {
    var el = WGSM.qs('[data-v2-key="' + field.key + '"]', root);
    if (!el) return field.def;
    if (field.type === "seg") {
      var on = WGSM.qs('button[aria-pressed="true"]', el);
      return on ? on.value : field.def;
    }
    if (field.type === "switch") return el.checked;
    if (field.type === "number") return el.value === "" ? "" : Number(el.value);
    return el.value;
  }

  function context(def, root) {
    var M = WGSM.v2machine;
    var key = WGSM.v2state.pick() || def.file;
    if (!M.files[key] || def.accepts.indexOf(M.files[key].kind) < 0) key = def.file;
    var file = M.files[key];
    var v = {};
    (def.fields || []).concat(def.more || []).forEach(function (field) {
      v[field.key] = readField(field, root);
    });
    if (def.checklist) {
      v[def.checklist.key] = WGSM.qsa("#v2-list-" + def.slug + " input:checked", root).map(function (box) {
        return box.value;
      });
    }
    return {
      def: def, v: v, file: file, fileKey: key, name: M.owner(file), M: M.machine, m: M,
      flag: WGSM.v2state.flag,
      /* Why this computer can't run a tool, or null */
      no: M.unavailable,
    };
  }

  /* --- building it ---------------------------------------------------- */

  function control(field, ctx) {
    var label = field.label;
    var width = field.width ? { maxWidth: field.width + "px" } : null;
    if (field.type === "select") {
      return h("select.v2-select", { "aria-label": label, dataset: { v2Key: field.key }, style: width || { maxWidth: "320px" } },
        resolve(field.options, ctx).map(function (o) {
          return h("option", { value: o[0], selected: o[0] === field.def ? true : null }, o[1]);
        }));
    }
    if (field.type === "seg") {
      return h("div.v2-seg", { role: "group", "aria-label": label, dataset: { seg: ctx.def.slug + "-" + field.key, v2Key: field.key } },
        field.options.map(function (o) {
          return h("button", { type: "button", value: o[0], "aria-pressed": o[0] === field.def ? "true" : null }, o[1]);
        }));
    }
    if (field.type === "switch") {
      return h("label.v2-switch", h("input", { type: "checkbox", role: "switch", "aria-label": label, checked: field.def || null, dataset: { v2Key: field.key } }));
    }
    if (field.type === "number") {
      return h("input.v2-input", { type: "number", min: field.min === undefined ? 0 : field.min, max: field.max === undefined ? null : field.max,
        step: field.spec && field.spec.type === "number" ? "any" : 1, value: field.def, placeholder: field.placeholder || null,
        "aria-label": label, dataset: { v2Key: field.key }, style: { maxWidth: (field.width || 100) + "px" } });
    }
    if (field.type === "text") {
      return h("input.v2-input" + (field.mono ? ".v2-input--mono" : ""), { type: "text", value: field.def || "", placeholder: field.placeholder || "", "aria-label": label, dataset: { v2Key: field.key }, style: { maxWidth: (field.width || 240) + "px" } });
    }
    if (field.type === "region") return WGSM.v2region.build(field);
    return h("span.v2-small.v2-mono", { dataset: { v2Show: field.key } });
  }

  function fieldRow(field, ctx) {
    return h("div.v2-field", { dataset: { v2Field: field.key } },
      h("span.v2-field__label", field.label, h("small", { dataset: { v2Small: field.key } }), field.ui ? null : h("code.v2-cmd", field.key)),
      h("div.v2-field__control", control(field, ctx)));
  }

  function checklist(list, ctx) {
    return h("form.v2-section", { id: "v2-list-" + ctx.def.slug, dataset: { annotId: list.key } },
      h("h2", list.title),
      h("div.v2-sheet", list.items.map(function (item) {
        return h("label.v2-choice",
          h("input.v2-check", { type: "checkbox", value: item.value, checked: item.on ? true : null, "aria-label": item.label }),
          h("span.v2-choice__title", item.label), h("span.v2-choice__aside", item.aside || ""), h("span.v2-choice__desc", item.desc || ""));
      })));
  }

  function inputRow(ctx) {
    var def = ctx.def;
    var M = WGSM.v2machine;
    var choices = Object.keys(M.files).filter(function (k) {
      return def.accepts.indexOf(M.files[k].kind) >= 0;
    });
    var row = h("div.v2-row.v2-row--wrap.v2-small", { dataset: { annotId: "input", v2Input: "" } });
    if (choices.length > 1) {
      document.body.appendChild(h("dialog.dialog", { id: "pick-" + def.slug, "aria-labelledby": "pick-title-" + def.slug },
        h("div.dialog__header", h("h2.dialog__title", { id: "pick-title-" + def.slug }, "Which file?")),
        h("div.dialog__body",
          h("select.v2-select", { "aria-label": "Genome file", dataset: { v2Pick: def.slug } }, choices.map(function (k) {
            return h("option", { value: k }, M.owner(M.files[k]) + " · " + M.files[k].name);
          })),
          h("p.v2-small.v2-muted", "Only files this tool can read are listed.")),
        h("div.dialog__footer",
          h("button.v2-btn", { type: "button", "data-close": "" }, "Cancel"),
          h("button.v2-btn.v2-btn--primary", { type: "button", "data-close": "", dataset: { v2UsePick: def.slug } }, "Use this file"))));
    }
    return { row: row, many: choices.length > 1 };
  }

  function fillInput(root, ctx, many) {
    var row = WGSM.qs("[data-v2-input]", root);
    if (!row) return;
    row.replaceChildren();
    WGSM.append(row, [h("span.v2-muted", "For"), h("strong", ctx.name), h("span.v2-dim", "·"), h("span.v2-tag", ctx.file.tag),
      h("span.v2-file__name", ctx.file.name),
      many ? h("button.v2-btn.v2-btn--sm.v2-btn--quiet", { type: "button", "data-open": "#pick-" + ctx.def.slug }, "Change") : null]);
    var select = WGSM.qs('[data-v2-pick="' + ctx.def.slug + '"]');
    if (select) select.value = ctx.fileKey;
  }

  function notice(n) {
    var action = null;
    if (n.action) {
      action = n.action.href
        ? h("a.v2-btn.v2-btn--sm", { href: WGSM.keepParams(n.action.href) }, n.action.label)
        : h("button.v2-btn.v2-btn--sm", { type: "button", dataset: { v2Download: n.action.set, v2Label: n.action.label, toast: n.action.toast || "" } }, n.action.label);
    }
    return h("div.v2-notice", { role: "status", dataset: { tone: n.tone || "info", annotId: n.id || "notice" } },
      WGSM.icon(n.icon || "info"),
      h("div.v2-notice__text", h("strong", n.title), h("span", n.text)),
      action ? h("div.v2-notice__actions", action) : null);
  }

  function check(c) {
    var words = { ok: "OK", attention: "Low", bad: "Too little", busy: "", none: "" };
    return h("div.v2-item", h("span.v2-item__title", c[0]), h("span.v2-item__desc", c[1]),
      h("span.v2-item__aside.v2-status", h("span.v2-pip", { dataset: { state: c[2] || "ok" } }), c[3] !== undefined ? c[3] : words[c[2] || "ok"]));
  }

  /* Memory and disk rows from the run's needs */
  function resourceChecks(out, ctx) {
    var rows = [];
    var M = ctx.M;
    if (out.memGB) {
      var budget = M.ramGB - 2;
      var state = out.memGB <= budget ? "ok" : out.memGB <= M.ramGB * 1.5 ? "attention" : "bad";
      var desc = "Needs about " + Math.round(out.memGB) + " GB; this Mac has " + M.ramGB + " GB";
      if (state === "attention") desc += ". It will run, swapping and slower; fewer threads or less sort memory fit";
      rows.push(["Memory", desc, state]);
    }
    (out.disk || []).forEach(function (d) {
      var free = M.disks[d[0]];
      rows.push(["Space on " + d[0], "Needs about " + ctx.m.gb(d[1]) + "; " + ctx.m.gb(free) + " free", d[1] <= free ? "ok" : "bad"]);
    });
    return rows;
  }

  function summary(out, ctx) {
    var parts = [];
    if (out.count) parts.push(out.count);
    /* Without a measured rate, say what the CLI's help says ("hours on a 30x genome") */
    parts.push(typeof out.hours === "number" ? ctx.m.duration(out.hours) : out.timeText || "time not estimated");
    if (out.memGB) parts.push("up to " + Math.round(out.memGB) + " GB memory");
    (out.disk || []).forEach(function (d) {
      parts.push(ctx.m.gb(d[1]) + " on " + d[0]);
    });
    return parts.join(" · ");
  }

  function update(root) {
    var def = defs[root.dataset.tool];
    WGSM.v2region.refresh(root, context(def, root).file.build || "GRCh38");
    var ctx = context(def, root);
    var out = def.run(ctx) || {};
    if (ctx.v.region && WGSM.v2region.parse(ctx.v.region).error) out.blocked = "Fix the region.";
    (def.requires || []).forEach(function (tool) {
      var why = ctx.no(tool);
      if (!why || out.blocked === why + ".") return;
      out.blocked = why + ".";
      var missing = /isn’t installed$/.test(why);
      (out.notes = out.notes || []).unshift({ tone: "warn", icon: missing ? "wrench" : "triangle-alert", id: "unavailable", title: missing ? why : "Not on this computer",
        text: missing ? "It’s an optional tool set. WGS Extract checks for tools but doesn’t install them: pixi install --locked -e " + ctx.m.env(tool) + "." : why + ". Run it on a computer that has it; this app can connect to one.",
        action: missing ? { label: "How to install", href: "install.html" } : null });
    });
    if (!def.noInput && !def.allowUnready && !ctx.m.ready(ctx.file) && !out.blocked) {
      out.blocked = "Fix this file first.";
      (out.notes = out.notes || []).unshift({ tone: "warn", icon: "wrench", id: "fix", title: ctx.name + "’s " + ctx.file.tag + " can’t be used yet",
        text: "It has no index, and its reference isn’t installed.", action: { label: "Fix it · 9 min", set: ctx.file.needs, toast: "Downloading hs38d1 and indexing the BAM. See Jobs." } });
    }
    var all = (def.fields || []).concat(def.more || []);
    all.forEach(function (field) {
      var wrap = WGSM.qs('[data-v2-field="' + field.key + '"]', root);
      if (wrap) wrap.hidden = field.when ? !field.when(ctx) : false;
      var small = WGSM.qs('[data-v2-small="' + field.key + '"]', root);
      var reason = field.disabled ? field.disabled(ctx) : null;
      if (small) small.textContent = reason || resolve(field.small, ctx) || "";
      var el = WGSM.qs('[data-v2-key="' + field.key + '"]', root);
      if (el && el.matches("input, select")) el.disabled = !!reason;
      var shown = WGSM.qs('[data-v2-show="' + field.key + '"]', root);
      if (shown) shown.textContent = resolve(field.value, ctx);
    });
    fillInput(root, ctx, !!WGSM.qs("#pick-" + def.slug));
    WGSM.qs("[data-v2-notes]", root).replaceChildren.apply(WGSM.qs("[data-v2-notes]", root), (out.notes || []).map(notice));
    var rows = resourceChecks(out, ctx).concat(out.checks || []);
    var pre = WGSM.qs("[data-v2-preflight]", root);
    pre.hidden = !rows.length && !(out.warnings || []).length;
    WGSM.qs("[data-v2-checks]", root).replaceChildren.apply(WGSM.qs("[data-v2-checks]", root), rows.map(check));
    WGSM.qs("[data-v2-checks]", root).hidden = !rows.length;
    WGSM.qs("[data-v2-warnings]", root).replaceChildren.apply(WGSM.qs("[data-v2-warnings]", root), (out.warnings || []).map(notice));
    WGSM.qs("[data-v2-command]", root).textContent = WGSM.v2requests.command(WGSM.v2requests.build(ctx));
    var bar = WGSM.qs("[data-v2-bar]");
    WGSM.qs("strong", bar).textContent = summary(out, ctx);
    WGSM.qs("span", bar).textContent = [out.makes, out.blocked].filter(Boolean).join(" ");
    var go = WGSM.qs("[data-v2-go]");
    go.textContent = out.button || def.button || "Start";
    go.disabled = !!out.blocked;
    go.dataset.toast = "Queued: " + (out.jobName || def.label + (def.noInput ? "" : " for " + ctx.name)) + ". See Jobs.";
    WGSM.icons.hydrate(root);
    WGSM.v2terms.wrap(root);
  }

  WGSM.render.blocks["v2-tool"] = function (el, opts) {
    var def = defs[opts.tool];
    el.dataset.tool = def.slug;
    var ctx = context(def, el);
    var input = def.noInput ? null : inputRow(ctx);
    WGSM.append(el, [
      h("section.v2-section", { dataset: { annotId: "tool-head" } },
        h("div.v2-section__head", h("h2.v2-h2--lg", def.label), h("code.v2-cmd.v2-dim.v2-small", def.op)),
        h("p.v2-muted", def.desc),
        def.learn ? h("details.v2-more", { dataset: { annotId: "learn" } }, h("summary", "What is this for?"),
          h("div.v2-more__body.v2-section", def.learn.split("\n\n").map(function (para) {
            return h("p.v2-small", para);
          }), def.lesson ? h("p.v2-small", "Learn the basics: ",
            h("a.v2-link", { href: WGSM.keepParams(def.lesson.href) }, def.lesson.label)) : null)) : null,
        input ? input.row : null),
      h("div.v2-stack", { dataset: { v2Notes: "" } }),
      def.checklist ? checklist(def.checklist, ctx) : null,
      def.fields && def.fields.length
        ? h("div.v2-sheet", { dataset: { annotId: "options" } }, h("div.v2-sheet__pad.v2-fields", def.fields.map(function (field) {
          return fieldRow(field, ctx);
        })))
        : null,
      def.more && def.more.length
        ? h("details.v2-more", { dataset: { annotId: "more" } }, h("summary", def.moreTitle || "More options"),
          h("div.v2-more__body.v2-fields", def.more.map(function (field) {
            return fieldRow(field, ctx);
          })))
        : null,
      h("section.v2-section", { dataset: { annotId: "preflight", v2Preflight: "" } },
        h("h2", "Before it starts"), h("div.v2-sheet", { dataset: { v2Checks: "" } }), h("div.v2-stack", { dataset: { v2Warnings: "" } })),
      def.extra ? html(h("div", { dataset: { annotId: "extra" } }), def.extra(ctx)) : null,
      h("pre.v2-code.v2-cmd", { dataset: { annotId: "command", v2Command: "" } })]);
    var page = el.closest(".v2-page");
    page.appendChild(h("div.v2-actionbar", { dataset: { annotId: "action" } },
      h("div.v2-actionbar__sum", { dataset: { v2Bar: "" } }, h("strong"), h("span")),
      h("div.v2-actionbar__btns",
        def.dryRun ? h("button.v2-btn", { type: "button", dataset: { toast: "Dry run: lists the steps that would run or be skipped, and why; nothing is written" } }, "Dry run") : null,
        h("button.v2-btn.v2-btn--primary.v2-btn--lg", { type: "button", dataset: { v2Go: "" } }, def.button || "Start"))));
  };

  function startJob(root) {
    var def = defs[root.dataset.tool];
    var ctx = context(def, root);
    return WGSM.v2requests.start(ctx, def.run(ctx) || {});
  }

  function refresh() {
    WGSM.qsa("[data-tool]").forEach(update);
  }

  WGSM.v2tools = {
    f: f,
    define: define,
    defs: defs,
    order: order,
    pageOf: pageOf,
    refresh: refresh,
    /* The API requests a tool page would send now */
    requests: function (root) {
      return WGSM.v2requests.build(context(defs[root.dataset.tool], root));
    },
    /* Wire live updates; called after saved options are restored */
    start: function () {
      document.addEventListener("click", function (event) {
        var dl = event.target.closest("[data-v2-download]");
        if (dl && !document.body.classList.contains("an-mode")) WGSM.v2requests.download(dl.dataset.v2Download, dl.dataset.v2Label || dl.textContent);
      });
      if (!WGSM.qs("[data-tool]")) return;
      refresh();
      ["change", "input"].forEach(function (type) {
        document.addEventListener(type, function (event) {
          if (event.target.closest("[data-tool]")) refresh();
        });
      });
      document.addEventListener("click", function (event) {
        var go = event.target.closest("[data-v2-go]");
        if (go && !go.disabled) startJob(WGSM.qs("[data-tool]"));
        var use = event.target.closest("[data-v2-use-pick]");
        if (use) {
          WGSM.v2state.pick(WGSM.qs('[data-v2-pick="' + use.dataset.v2UsePick + '"]').value);
          refresh();
          WGSM.toast("Using " + WGSM.qs("[data-v2-input] .v2-file__name").textContent);
        }
        if (event.target.closest("[data-tool] [data-seg] > button")) setTimeout(refresh, 0);
      });
      WGSM.on("v2-flags", refresh);
    },
  };
})();
