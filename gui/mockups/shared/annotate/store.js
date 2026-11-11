/* Notes storage. With serve.py running, notes are read from and written to
 * feedback/<page-id>.json through its API, so they can be committed. From
 * file:// (or any host without the API) they live in this browser's
 * localStorage, and Export/Import move them to and from that JSON file.
 *
 * A change is an operation ({op: "add" | "reply" | "status" | "edit" |
 * "delete" | "merge", ...}); serve.py applies the same operations. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var LOCAL_KEY = "wgsm.notes." + WGSM.page.id;

  function now() {
    return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  function emptyDoc() {
    return { notes: {}, page: WGSM.page.id, title: WGSM.page.title };
  }

  /* Mirrors apply_op() in serve.py */
  function applyOp(doc, change) {
    var notes = doc.notes;
    var note = change.id ? notes[change.id] : null;
    var stamp = now();
    if (change.op !== "add" && change.op !== "merge" && !note) throw new Error("No such note: " + change.id);
    switch (change.op) {
      case "add":
        notes[change.note.id] = Object.assign({ replies: [], status: "open" }, change.note, { created: stamp, updated: stamp });
        break;
      case "reply":
        note.replies.push(Object.assign({}, change.reply, { created: stamp }));
        note.updated = stamp;
        break;
      case "status":
        note.status = change.status;
        note.updated = stamp;
        break;
      case "edit":
        note.text = change.text;
        note.updated = stamp;
        break;
      case "delete":
        delete notes[change.id];
        break;
      case "merge":
        Object.keys(change.notes || {}).forEach(function (id) {
          var incoming = change.notes[id];
          var mine = notes[id];
          if (!mine || String(incoming.updated) > String(mine.updated)) notes[id] = incoming;
        });
        break;
      default:
        throw new Error("Unknown operation: " + change.op);
    }
    doc.title = WGSM.page.title;
    return doc;
  }

  function api(path, body) {
    return fetch(WGSM.link("api/" + path), body ? {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    } : { cache: "no-store" }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error((data.error && data.error.message) || response.statusText);
        return data;
      });
    });
  }

  function fetchJson(path) {
    return fetch(WGSM.link(path), { cache: "no-store" }).then(function (r) {
      return r.ok ? r.json() : null;
    });
  }

  /* Every committed feedback file listed in feedback/_pages.json */
  function committedDocs() {
    return fetchJson("feedback/_pages.json").then(function (pages) {
      return Promise.all((pages || []).map(function (page) {
        return fetchJson("feedback/" + page + ".json");
      }));
    }).then(function (docs) {
      return docs.filter(function (doc) {
        return doc && doc.notes;
      });
    }, function () {
      return [];
    });
  }

  function readLocal() {
    try {
      return JSON.parse(localStorage.getItem(LOCAL_KEY)) || emptyDoc();
    } catch (err) {
      return emptyDoc();
    }
  }

  var store = (WGSM.notes = {
    mode: "local",
    doc: emptyDoc(),
    error: null,

    /* The copy on GitHub Pages has no notes API; it reads the committed
     * notes listed in feedback/_pages.json (made by the Pages workflow). */
    published: /\.github\.io$/.test(location.hostname) || WGSM.params.has("published"),

    init: function () {
      var probe = store.published
        ? Promise.resolve("local")
        : /^https?:$/.test(location.protocol)
        ? api("health").then(function (health) {
            return health.ok ? "server" : "local";
          }, function () {
            return "local";
          })
        : Promise.resolve("local");
      return probe.then(function (mode) {
        store.mode = mode;
        return store.published ? store.loadCommitted() : null;
      }).then(function () {
        return store.load();
      }).then(function (doc) {
        WGSM.emit("notes-ready", store.mode);
        return doc;
      });
    },

    /* Merge the committed notes into this browser's copy; notes changed
     * here since keep the newer version (as Import does) */
    loadCommitted: function () {
      return fetchJson("feedback/_pages.json").then(function (pages) {
        if (!pages || pages.indexOf(WGSM.page.id) < 0) return null;
        return fetchJson("feedback/" + WGSM.page.id + ".json").then(function (doc) {
          if (!doc || !doc.notes) return null;
          localStorage.setItem(LOCAL_KEY, JSON.stringify(applyOp(readLocal(), { op: "merge", notes: doc.notes })));
          return null;
        });
      }).catch(function () {
        return null;
      });
    },

    load: function () {
      if (store.mode === "local") {
        store.doc = readLocal();
        return Promise.resolve(store.doc);
      }
      return api("feedback/" + WGSM.page.id).then(function (doc) {
        store.doc = doc;
        return doc;
      }, function (err) {
        /* e.g. a feedback file left broken by a merge: show why, keep going */
        store.error = err.message;
        store.doc = emptyDoc();
        return store.doc;
      });
    },

    apply: function (change) {
      var done;
      if (store.mode === "server") {
        done = api("feedback/" + WGSM.page.id, Object.assign({ title: WGSM.page.title }, change));
      } else {
        done = new Promise(function (resolve) {
          var doc = applyOp(readLocal(), change);
          localStorage.setItem(LOCAL_KEY, JSON.stringify(doc));
          resolve(doc);
        });
      }
      return done.then(function (doc) {
        store.doc = doc;
        store.error = null;
        WGSM.emit("notes", doc);
        return doc;
      }, function (err) {
        store.error = err.message;
        WGSM.emit("notes", store.doc);
        throw err;
      });
    },

    /* Notes in display order (oldest first) with their number */
    list: function () {
      var notes = store.doc.notes || {};
      return Object.keys(notes).map(function (id) {
        return notes[id];
      }).sort(function (a, b) {
        return a.created < b.created ? -1 : a.created > b.created ? 1 : a.id < b.id ? -1 : 1;
      }).map(function (note, i) {
        return Object.assign({ number: i + 1 }, note);
      });
    },

    /* Open/resolved counts per page: from serve.py, else from this
     * browser's notes plus (when published) the committed ones */
    summary: function () {
      if (store.mode === "server") return api("feedback");
      var docs = {};
      for (var i = 0; i < localStorage.length; i++) {
        var key = localStorage.key(i);
        if (key.indexOf("wgsm.notes.") !== 0) continue;
        try {
          var doc = JSON.parse(localStorage.getItem(key));
          docs[doc.page] = doc;
        } catch (err) {
          continue;
        }
      }
      var committed = store.published ? committedDocs() : Promise.resolve([]);
      return committed.then(function (list) {
        list.forEach(function (doc) {
          docs[doc.page] = applyOp(docs[doc.page] || { notes: {}, page: doc.page, title: doc.title }, { op: "merge", notes: doc.notes });
          docs[doc.page].title = docs[doc.page].title || doc.title;
        });
        return {
          pages: Object.keys(docs).sort().map(function (page) {
            var notes = Object.keys(docs[page].notes || {}).map(function (id) {
              return docs[page].notes[id];
            });
            var resolved = notes.filter(function (n) {
              return n.status === "resolved";
            }).length;
            return { page: page, title: docs[page].title, open: notes.length - resolved, resolved: resolved };
          }),
        };
      });
    },

    exportFile: function () {
      var blob = new Blob([JSON.stringify(sortKeys(store.doc), null, 2) + "\n"], { type: "application/json" });
      var a = WGSM.h("a", { href: URL.createObjectURL(blob), download: WGSM.page.id + ".json" });
      document.body.appendChild(a);
      a.click();
      a.remove();
    },

    importFile: function (file) {
      return file.text().then(function (text) {
        var doc = JSON.parse(text);
        if (!doc || typeof doc.notes !== "object") throw new Error("Not a feedback file");
        return store.apply({ op: "merge", notes: doc.notes });
      });
    },

    author: function (name) {
      if (arguments.length) WGSM.pref("author", name);
      return WGSM.pref("author") || "";
    },
  });

  function sortKeys(value) {
    if (Array.isArray(value)) return value.map(sortKeys);
    if (value && typeof value === "object") {
      return Object.keys(value).sort().reduce(function (out, key) {
        out[key] = sortKeys(value[key]);
        return out;
      }, {});
    }
    return value;
  }
})();
