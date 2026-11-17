/* WGSM.v2generated: generated files, for trying tools on more than the
 * demo's four people (review tooling; the mock's side is api-files.js).
 *
 *   data-v2-generate        Add files page: "Add generated files…", the
 *                           dialog that picks templates and whose they are,
 *                           and the list of files added so far
 *   data-v2-added           Library: generated files under their people;
 *                           made-up people get their own group
 *   data-v2-person-added    person-added.html?id=…: a made-up person
 *   catalogEditor()         Mockup settings: edit the catalog the files
 *                           are made from -> {el, apply()}
 *
 * The files live in the mock (this browser, or the shared server); the
 * page reads the mirror api-server.js keeps (WGSM.v2state.generated()). */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var api = WGSM.v2api;
  var F = window.WGSMMock.fmt;
  var CAT = window.WGSMMock.fileCatalog;
  var WHAT = { reads: "Aligned reads", fastq: "Raw reads", vcf: "Variant calls", kit: "DNA-test raw data" };
  var PLATFORM = { illumina: "Illumina", ont: "Oxford Nanopore", pacbio: "PacBio HiFi", mgi: "MGI DNBSEQ", "": "—" };

  function S() {
    return WGSM.v2state;
  }

  function mirror() {
    return S().generated();
  }

  function ownerOf(file) {
    return file.person ? S().person(file.person).name : "Not assigned";
  }

  function buildText(f) {
    if (f.kind === "fastq") return "Not aligned yet";
    return [f.build, f.ref].filter(Boolean).join(" · ") || "—";
  }

  function size(f) {
    return f.r1 ? "2 × " + F.gb(f.size / 2) : F.gb(f.size);
  }

  function personHref(id) {
    return "person-added.html?id=" + encodeURIComponent(id);
  }

  function removeFile(key) {
    return api.del("/v1/library/files/" + encodeURIComponent(key)).then(function (res) {
      if (res.status !== 200) {
        WGSM.toast(res.body.error ? res.body.error.message : "Couldn’t remove it", "triangle-alert");
        return;
      }
      return api.syncGenerated().then(function () {
        WGSM.toast("Removed from the library; the file stays where it is");
      });
    });
  }

  /* A library row, as on the Library page */
  function fileRow(key, f) {
    var issues = f.issues || [];
    return h("div.v2-file", { dataset: { v2Gen: key } },
      h("span"), h("span.v2-tag", f.tag), h("span.v2-file__name", f.name, h("small.v2-gen__source", f.source ? " · " + f.source : "")),
      h("span", buildText(f)),
      h("span.v2-num", f.depth ? f.depth + "×" : "—"),
      h("span.v2-num", size(f)),
      h("span.v2-status", h("span.v2-pip", { dataset: { state: issues.length ? "attention" : "ok" } }), issues.length ? issues[0].text.replace(/[,.;].*$/, "") : "Ready",
        h("button.v2-btn.v2-btn--sm.v2-btn--quiet.v2-gen__remove", { type: "button", title: "Remove from the library", "aria-label": "Remove " + f.name,
          onclick: function () {
            removeFile(key);
          } }, WGSM.icon("x", "icon--sm"))));
  }

  /* --- Library -------------------------------------------------------- */

  function renderLibrary(root) {
    WGSM.qsa("[data-v2-gen], [data-v2-gen-group]").forEach(function (el) {
      el.remove();
    });
    var g = mirror();
    var groups = {};
    Object.keys(g.files).forEach(function (key) {
      var f = g.files[key];
      var target = f.person && WGSM.qs('[data-v2-group="' + f.person + '"] .v2-files');
      if (target) {
        target.appendChild(fileRow(key, f));
        return;
      }
      var who = f.person || "";
      (groups[who] = groups[who] || []).push(key);
    });
    Object.keys(groups).sort(function (a, b) {
      return !a ? 1 : !b ? -1 : ownerOf(g.files[groups[a][0]]).localeCompare(ownerOf(g.files[groups[b][0]]));
    }).forEach(function (who) {
      var name = who ? S().person(who).name : "Not assigned";
      root.appendChild(h("div.v2-group", { dataset: { v2GenGroup: who || "unassigned", annotId: "gen-" + (who || "unassigned") } },
        h("div.v2-person",
          h("span.v2-avatar.v2-avatar--md", who ? S().initials(name) : "?"),
          who ? h("a.v2-person__name", { href: personHref(who) }, S().first(name), h("small", "generated")) : h("span.v2-person__name", "Not assigned", h("small", "generated files")),
          h("div.v2-person__found"),
          h("div.v2-person__next", who ? h("a.v2-btn.v2-btn--sm", { href: personHref(who) }, "Open") : null)),
        h("div.v2-files", groups[who].map(function (key) {
          return fileRow(key, g.files[key]);
        }))));
    });
    WGSM.v2terms.wrap(root);
  }

  /* --- Add files page ------------------------------------------------- */

  function generateDialog() {
    var list = h("div.v2-sheet.v2-gen__list");
    var whose = h("select.v2-select", { "aria-label": "Whose files", style: { maxWidth: "320px" } });
    var name = h("input.v2-input", { type: "text", placeholder: "Name, for example Dorothy Pemberton", "aria-label": "Name of the new person", hidden: true, style: { maxWidth: "280px" } });
    var note = h("p.v2-small.v2-muted");
    var go = h("button.v2-btn.v2-btn--primary", { type: "button", disabled: true }, "Add files");
    var templates = [];

    function chosen() {
      return WGSM.qsa("input[type=checkbox]:checked", list).map(function (box) {
        var count = WGSM.qs('input[type=number][data-for="' + box.value + '"]', list);
        return { template: box.value, count: Math.max(1, Math.min(CAT.MAX_COUNT, Number(count.value) || 1)) };
      });
    }

    function update() {
      var n = chosen().reduce(function (sum, c) {
        return sum + c.count;
      }, 0);
      go.disabled = !n;
      go.textContent = n ? "Add " + n + " file" + (n === 1 ? "" : "s") : "Add files";
      name.hidden = whose.value !== "named";
      note.textContent = whose.value === "new" ? "Each file gets its own made-up person." : whose.value === "named" ? "All of them belong to one new person." : "";
    }

    function load() {
      return api.get("/v1/mock/catalog").then(function (res) {
        if (res.status !== 200) return;
        templates = res.body.templates;
        list.replaceChildren.apply(list, templates.map(function (t) {
          return h("label.v2-choice.v2-gen__choice",
            h("input.v2-check", { type: "checkbox", value: t.id, "aria-label": t.label, onchange: update }),
            h("span.v2-choice__title", t.label),
            h("span.v2-choice__aside", h("input.v2-input", { type: "number", min: 1, max: CAT.MAX_COUNT, value: 1, "aria-label": "How many: " + t.label, dataset: { for: t.id }, style: { width: "64px" },
              onclick: function (event) {
                event.preventDefault();
              }, oninput: update })),
            h("span.v2-choice__desc", [t.format, t.build, t.depth ? t.depth + "×" : "", F.gb(t.size_gb), t.source, PLATFORM[t.platform || ""] !== "—" ? PLATFORM[t.platform] : ""].filter(Boolean).join(" · ")));
        }));
        var people = S().people().filter(function (p) {
          return !p.removed;
        }).concat(Object.keys(mirror().people).map(function (id) {
          return S().person(id);
        }));
        whose.replaceChildren(h("option", { value: "new" }, "Made-up people, one per file"), h("option", { value: "named" }, "One new person…"),
          h("optgroup", { label: "In the library" }, people.map(function (p) {
            return h("option", { value: p.id }, p.name);
          })), h("option", { value: "unassigned" }, "Not assigned yet"));
        update();
      });
    }

    var dialog = h("dialog.dialog.v2-gen", { id: "v2-generate", "aria-labelledby": "v2-generate-title", dataset: { v2NoTerms: "" } },
      h("div.dialog__header", h("h2.dialog__title", { id: "v2-generate-title" }, "Add generated files")),
      h("div.dialog__body",
        h("p.v2-small.v2-muted", "Mockup only: made-up files from the catalog in Mockup settings, added to the library as if they were in Downloads or on the genomes drive. They take disk space and tools can use them."),
        list,
        h("div.v2-field", h("span.v2-field__label", "Whose are they?"), h("div.v2-field__control.v2-row.v2-row--wrap", whose, name)),
        note),
      h("div.dialog__footer",
        h("button.v2-btn", { type: "button", "data-close": "" }, "Cancel"),
        go));
    whose.addEventListener("change", update);
    go.addEventListener("click", function () {
      var picks = chosen();
      var body = { whose: whose.value === "named" ? "new" : whose.value };
      if (whose.value === "named") {
        if (!name.value.trim()) {
          name.focus();
          WGSM.toast("Give the new person a name", "triangle-alert");
          return;
        }
        body.name = name.value.trim();
      }
      go.disabled = true;
      var made = 0;
      var chain = Promise.resolve();
      var person = null;
      picks.forEach(function (p) {
        chain = chain.then(function () {
          /* One named person across templates: the first request makes
           * them, the rest add to them */
          var b = Object.assign({}, body, p, person ? { whose: person, name: undefined } : {});
          return api.post("/v1/mock/files", b).then(function (res) {
            if (res.status !== 200) throw new Error(res.body.error ? res.body.error.message : "status " + res.status);
            made += res.body.files.length;
            if (whose.value === "named" && res.body.people[0]) person = res.body.people[0].id;
          });
        });
      });
      chain.then(api.syncGenerated).then(function () {
        WGSM.toast("Added " + made + " generated file" + (made === 1 ? "" : "s") + " to the library");
        WGSM.qs("[data-close]", dialog).click();
      }).catch(function (err) {
        WGSM.toast(String(err.message || err), "triangle-alert");
        update();
      });
    });
    return { el: dialog, load: load };
  }

  function renderAdded(host) {
    var g = mirror();
    var keys = Object.keys(g.files);
    var body = WGSM.qs("[data-v2-gen-body]", host);
    body.replaceChildren();
    WGSM.qs("[data-v2-gen-clear]", host).hidden = !keys.length;
    if (!keys.length) {
      body.appendChild(h("p.v2-small.v2-muted.v2-sheet__pad", "None yet."));
      return;
    }
    body.appendChild(h("div.v2-files", keys.map(function (key) {
      var f = g.files[key];
      var row = fileRow(key, f);
      row.children[0].replaceWith(h("span.v2-small.v2-gen__who", f.person ? h("a.v2-link", { href: personHref(f.person) }, S().first(ownerOf(f))) : "—"));
      return row;
    })));
  }

  function startAdd(host) {
    var d = generateDialog();
    document.body.appendChild(d.el);
    host.replaceChildren(
      h("div.v2-section__head", h("h2", "Generated files"), h("span.v2-tag", "Mockup")),
      h("p.v2-small.v2-muted", "Try tools on more people and kinds of file: long reads, exomes, low-pass genomes, Big Y, raw reads. Edit what can be made in Mockup settings."),
      h("div.v2-row.v2-row--wrap",
        h("button.v2-btn", { type: "button", onclick: function () {
          d.load().then(function () {
            WGSM.ui.open(d.el);
          });
        } }, WGSM.icon("plus"), "Add generated files…"),
        h("button.v2-btn.v2-btn--quiet", { type: "button", dataset: { v2GenClear: "" }, hidden: true, onclick: function () {
          api.del("/v1/mock/files").then(api.syncGenerated).then(function () {
            WGSM.toast("Removed every generated file and made-up person");
          });
        } }, "Remove them all")),
      h("div.v2-sheet", { dataset: { v2GenBody: "" } }));
    renderAdded(host);
    WGSM.on("v2-generated", function () {
      renderAdded(host);
    });
  }

  /* --- A made-up person's page ---------------------------------------- */

  function startPerson(host) {
    var id = WGSM.params.get("id") || "";
    function render() {
      var g = mirror();
      var keys = Object.keys(g.files).filter(function (k) {
        return g.files[k].person === id;
      });
      var known = g.people[id];
      host.replaceChildren();
      if (!known && !keys.length) {
        host.appendChild(h("div.v2-empty", h("h2", "Nobody here"), h("p.v2-muted", "This made-up person was removed, or the demo was reset."), h("a.v2-btn", { href: "add.html" }, "Add generated files")));
        return;
      }
      var p = S().person(id);
      document.title = p.name + " · v2 · WGS Extract mockups";
      var kinds = {};
      keys.forEach(function (k) {
        kinds[g.files[k].kind] = k;
      });
      var T = WGSM.v2tools;
      var sex = known && known.sex ? (known.sex === "male" ? "male" : "female") : null;
      var tools = T.order.filter(function (slug) {
        if (T.defs[slug].needsY && sex === "female") return false;
        return T.defs[slug].accepts.some(function (k) {
          return kinds[k];
        });
      });
      host.append(
        h("header.v2-head", { dataset: { annotId: "header" } },
          h("div.v2-head__who", h("span.v2-avatar.v2-avatar--lg", S().initials(p.name)),
            h("div.v2-head__text", h("h1", p.name),
              h("p.v2-head__sub", [known && known.generated ? "Made up for the mockup" : null, sex, keys.length + " file" + (keys.length === 1 ? "" : "s")].filter(Boolean).join(" · "))))),
        h("section.v2-section", { dataset: { annotId: "files" } },
          h("div.v2-section__head", h("h2", "Files")),
          h("div.v2-sheet", h("div.v2-files", keys.map(function (k) {
            return fileRow(k, g.files[k]);
          }))),
          keys.some(function (k) {
            return (g.files[k].issues || []).length;
          }) ? h("ul.v2-small.v2-gen__issues", keys.reduce(function (all, k) {
              return all.concat((g.files[k].issues || []).map(function (i) {
                return h("li", h("strong", g.files[k].name + ": "), i.text);
              }));
            }, [])) : null),
        h("section.v2-section", { dataset: { annotId: "tools" } },
          h("div.v2-section__head", h("h2", "What you can do"), h("a.v2-link.v2-small", { href: "tools.html" }, "All tools")),
          h("div.v2-gen__tools", tools.map(function (slug) {
            var def = T.defs[slug];
            var key = def.accepts.map(function (k) {
              return kinds[k];
            }).filter(Boolean)[0];
            return h("a.v2-gen__tool", { href: T.pageOf(slug) + "?file=" + encodeURIComponent(key) },
              h("strong", def.label), h("span.v2-small.v2-muted", def.group + " · " + WHAT[g.files[key].kind]));
          }))));
      WGSM.v2terms.wrap(host);
    }
    render();
    WGSM.on("v2-generated", render);
  }

  /* --- Mockup settings: the catalog ----------------------------------- */

  function catalogEditor() {
    var draft = null;
    var custom = false;
    var dirty = false;
    var restore = false;
    var problems = h("div.v2-notice", { dataset: { tone: "warn" }, hidden: true }, WGSM.icon("triangle-alert"), h("div.v2-notice__text"));
    var table = h("tbody");
    var count = h("span.v2-small.v2-muted");
    var restoreBtn = h("button.v2-btn.v2-btn--sm.v2-btn--quiet", { type: "button", onclick: function () {
      draft = JSON.parse(JSON.stringify(CAT.DEFAULT));
      restore = true;
      dirty = false;
      render();
    } }, "Restore the default catalog");

    function edited() {
      dirty = true;
      restore = false;
      problems.hidden = true;
      count.textContent = draft.length + " templates" + (custom || dirty ? " · edited" : "");
    }

    function input(value, set, o) {
      return h("input.v2-input" + (o.mono ? ".v2-input--mono" : ""), { type: o.type || "text", value: value, min: o.min, step: o.step, "aria-label": o.label, style: { width: (o.width || 120) + "px" },
        oninput: function (event) {
          var v = event.target.value;
          set(o.type === "number" ? (v === "" ? "" : Number(v)) : v);
          edited();
        } });
    }

    function select(value, options, set, label) {
      return h("select.v2-select", { "aria-label": label, onchange: function (event) {
        set(event.target.value);
        edited();
      } }, options.map(function (o) {
        return h("option", { value: o[0], selected: o[0] === value ? true : null }, o[1]);
      }));
    }

    function row(t, i) {
      return h("tr",
        h("td", input(t.label, function (v) {
          t.label = v;
        }, { label: "Template name", width: 200 })),
        h("td", select(t.format, Object.keys(CAT.FORMATS).map(function (f) {
          return [f, f];
        }), function (v) {
          t.format = v;
        }, "Format")),
        h("td", select(t.build, [["GRCh38", "GRCh38"], ["GRCh37", "GRCh37"], ["T2T", "T2T"], ["", "—"]], function (v) {
          t.build = v;
          t.ref = { GRCh38: "hs38", GRCh37: "hs37d5", T2T: "T2Tv20" }[v] || "";
          render();
        }, "Build")),
        h("td", input(t.ref, function (v) {
          t.ref = v;
        }, { label: "Reference", width: 80 })),
        h("td", input(t.depth, function (v) {
          t.depth = v;
        }, { type: "number", min: 0, label: "Depth", width: 70 })),
        h("td", input(t.size_gb, function (v) {
          t.size_gb = v;
        }, { type: "number", min: 0, step: "any", label: "Size GB", width: 84 })),
        h("td", input(t.source, function (v) {
          t.source = v;
        }, { label: "Source", width: 140 })),
        h("td", select(t.platform, CAT.PLATFORMS.map(function (p) {
          return [p, PLATFORM[p]];
        }), function (v) {
          t.platform = v;
        }, "Platform")),
        h("td", select(t.sex, [["any", "Any"], ["male", "Male"], ["female", "Female"]], function (v) {
          t.sex = v;
        }, "Sex")),
        h("td", h("input.v2-check", { type: "checkbox", checked: t.index ? true : null, "aria-label": "Indexed", onchange: function (event) {
          t.index = event.target.checked;
          edited();
        } })),
        h("td", input(t.name, function (v) {
          t.name = v;
        }, { label: "File name pattern", mono: true, width: 260 })),
        h("td", h("button.v2-btn.v2-btn--sm.v2-btn--quiet", { type: "button", title: "Remove this template", "aria-label": "Remove " + t.label, onclick: function () {
          draft.splice(i, 1);
          edited();
          render();
        } }, WGSM.icon("x", "icon--sm"))));
    }

    function render() {
      table.replaceChildren.apply(table, draft.map(row));
      count.textContent = draft.length + " templates" + (custom || dirty || restore ? " · edited" : "");
      restoreBtn.hidden = !custom && !dirty;
    }

    var el = h("section.v2-gen-cat", { dataset: { annotId: "catalog" } },
      h("h3.v2-mock__h", "Generated files catalog"),
      h("details.v2-mock__more",
        h("summary", "What Add files › Add generated files can make ", count),
        h("div.v2-mock__details",
          h("p.v2-small.v2-muted", "File name patterns can use {first}, {last}, {First}, {Last}, {digits}, {code}, {date} and {depth}. Saved in the mock: this browser, or the shared server."),
          problems,
          h("div.v2-mock__scroll", h("table.v2-mock__volumes.v2-gen-cat__table",
            h("thead", h("tr", ["Name", "Format", "Build", "Ref", "Depth", "Size GB", "Source", "Platform", "Sex", "Index", "File name", ""].map(function (t) {
              return h("th", t);
            }))), table)),
          h("div.v2-row.v2-row--wrap",
            h("button.v2-btn.v2-btn--sm", { type: "button", onclick: function () {
              draft.push({ id: "", label: "New template " + (draft.length + 1), format: "BAM", source: "", build: "GRCh38", ref: "hs38", depth: 30, size_gb: 60, platform: "illumina", sex: "any", name: "{first}-{last}.bam", index: true });
              edited();
              render();
            } }, WGSM.icon("plus", "icon--sm"), "Add a template"),
            restoreBtn))));

    function load() {
      return api.get("/v1/mock/catalog").then(function (res) {
        if (res.status !== 200) return;
        draft = JSON.parse(JSON.stringify(res.body.templates));
        custom = res.body.custom;
        dirty = restore = false;
        render();
      });
    }
    api.ready.then(load);

    return {
      el: el,
      /* Saves an edited catalog; resolves false (and shows why) when the
       * mock refuses it */
      apply: function () {
        if (restore) return api.del("/v1/mock/catalog").then(load).then(function () {
          return true;
        });
        if (!dirty) return Promise.resolve(true);
        return api.request("PUT", "/v1/mock/catalog", { templates: draft }).then(function (res) {
          if (res.status === 200) {
            return load().then(function () {
              return true;
            });
          }
          var err = res.body.error || {};
          WGSM.qs(".v2-notice__text", problems).replaceChildren(h("strong", "Fix the catalog first"), h("span", (err.details || [err.message]).join(" ")));
          problems.hidden = false;
          WGSM.qs("details", el).open = true;
          problems.scrollIntoView({ block: "nearest" });
          return false;
        });
      },
    };
  }

  WGSM.v2generated = {
    catalogEditor: catalogEditor,
    start: function () {
      var add = WGSM.qs("[data-v2-generate]");
      if (add) startAdd(add);
      var lib = WGSM.qs("[data-v2-added]");
      if (lib) {
        renderLibrary(lib);
        WGSM.on("v2-generated", function () {
          renderLibrary(lib);
        });
      }
      var person = WGSM.qs("[data-v2-person-added]");
      if (person) startPerson(person);
      /* Someone else (another tab, the shared server) changed the files
       * the file picker lists */
      if (WGSM.qs("[data-v2-input]")) {
        WGSM.on("v2-generated", function () {
          WGSM.toast("The library changed; reload to pick from its new files", "info");
        });
      }
    },
  };
})();
