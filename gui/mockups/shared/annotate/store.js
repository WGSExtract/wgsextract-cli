/* Notes storage. With serve.py running, notes are read from and written to
 * feedback/<page-id>.json through its API, so they can be committed. From
 * file:// and Pages they live in this browser's
 * localStorage, and Export/Import move them to and from that JSON file.
 *
 * A change is an operation ({op: "add" | "reply" | "status" | "edit" |
 * "delete" | "merge", ...}); serve.py applies the same operations. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var version = window.WGSM_VERSION || {};
  var marker = document.querySelector('meta[name="wgsm-review-server"]');
  var hosted = location.protocol !== "file:" && (!!marker || version.hosted || version.source === "hosted");
  var preview = location.pathname.match(/\/(preview|local)\/([^/]+)\//);
  var idea = (marker && marker.getAttribute("data-idea")) || version.idea ||
    (preview ? (version.branch || (preview[1] === "local" ? "local/" : "mockups/") + preview[2]) : "main");
  var LOCAL_PREFIX = "wgsm.notes.v2.";
  function localKey(identity, page) {
    return LOCAL_PREFIX + encodeURIComponent(identity) + "." + page;
  }
  var LOCAL_KEY = localKey(idea, WGSM.page.id);
  var retryTimer = null, retryDelay = 500;
  var chosenAuthor = null;
  var unavailableText = "Review server unavailable, retrying; notes can't be saved yet.";

  function now() {
    return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  function emptyDoc() {
    return { idea: idea, notes: {}, page: WGSM.page.id, title: WGSM.page.title, site: currentSite() };
  }

  function currentSite() {
    return /^https?:$/.test(location.protocol) ? new URL(WGSM.link(""), location.href).pathname : null;
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
        mergeNotes(notes, change.notes || {});
        break;
      default:
        throw new Error("Unknown operation: " + change.op);
    }
    doc.title = WGSM.page.title;
    doc.site = currentSite();
    return doc;
  }

  function mergeNotes(notes, incoming) {
    Object.keys(incoming).forEach(function (id) {
      var mine = notes[id], note = incoming[id];
      if (!mine || String(note.updated || "") > String(mine.updated || "")) notes[id] = note;
    });
  }

  function saveLocalDoc(doc) {
    if (store && store.mode === "server") throw new Error("Hosted review notes cannot be saved in browser storage");
    var identity = doc.idea || idea;
    var key = localKey(identity, doc.page);
    var mine = JSON.parse(localStorage.getItem(key)) || { notes: {} };
    mergeNotes(mine.notes, doc.notes || {});
    var merged = Object.assign({}, mine, doc, { idea: identity, notes: mine.notes });
    if (!merged.site && identity === idea) merged.site = currentSite();
    localStorage.setItem(key, JSON.stringify(merged));
    return merged;
  }

  function migrateLocalDocs() {
    var keys = [];
    for (var i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
    keys.forEach(function (key) {
      if (key.indexOf("wgsm.notes.") !== 0 || key.indexOf(LOCAL_PREFIX) === 0) return;
      try {
        var doc = JSON.parse(localStorage.getItem(key));
        if (!doc || !doc.notes) return;
        saveLocalDoc(Object.assign({}, doc, { idea: doc.idea || "main", page: doc.page || key.slice("wgsm.notes.".length) }));
        localStorage.removeItem(key);
      } catch (err) {
        /* Leave an unreadable document intact for manual recovery. */
      }
    });
  }

  function api(path, body) {
    if (body && store.mode === "server" && !store.available) return Promise.reject(new Error(unavailableText));
    var url = new URL(WGSM.link("api/" + path), location.href);
    if (preview) url = new URL("/api/" + path, location.href);
    url.searchParams.set("idea", idea);
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    return fetch(url.href, body ? {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    } : { cache: "no-store", signal: controller.signal }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          var err = new Error((data.error && data.error.message) || response.statusText);
          err.apiResponse = true;
          err.retryable = response.status >= 500;
          throw err;
        }
        return data;
      });
    }).catch(function (err) {
      if (store.mode === "server" && (!err.apiResponse || err.retryable)) disconnected(err);
      throw err;
    }).finally(function () {
      clearTimeout(timeout);
    });
  }

  function disconnected(err) {
    store.available = false;
    store.error = unavailableText;
    WGSM.emit("notes-connection", false);
    WGSM.emit("notes", store.doc);
    if (retryTimer === null) {
      retryTimer = setTimeout(function () {
        retryTimer = null;
        return store.reconnect();
      }, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 15000);
    }
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
    idea: idea,
    hosted: hosted,
    shared: false,
    saveLocalDoc: saveLocalDoc,
    api: api,
    mode: location.protocol === "file:" ? "local" : "server",
    available: false,
    doc: emptyDoc(),
    error: null,

    /* The copy on GitHub Pages has no notes API; it reads the committed
     * notes listed in feedback/_pages.json (made by the Pages workflow). */
    published: (window.WGSM_VERSION || {}).source === "pages" ||
      (window.WGSM_VERSION || {}).source === "preview" ||
      /\.github\.io$/.test(location.hostname) || WGSM.params.has("published"),

    init: function () {
      store.mode = !hosted && (location.protocol === "file:" || store.published) ? "local" : "server";
      if (store.mode === "server") {
        store.error = unavailableText;
        WGSM.emit("notes-connection", false);
      }
      return Promise.resolve().then(function () {
        if (store.mode === "local") migrateLocalDocs();
        return store.mode === "local" && store.published ? store.loadCommitted() : null;
      }).then(function () {
        return store.load();
      }).then(function (doc) {
        store.ready = true;
        WGSM.emit("notes-ready", store.mode);
        return doc;
      });
    },

    /* Merge the committed notes into this browser's copy; notes changed
     * here since keep the newer version (as Import does) */
    loadCommitted: function () {
      if (store.mode === "server") return Promise.resolve();
      return fetchJson("feedback/_pages.json").then(function (pages) {
        if (!pages || pages.indexOf(WGSM.page.id) < 0) return null;
        return fetchJson("feedback/" + WGSM.page.id + ".json").then(function (doc) {
          if (!doc || !doc.notes) return null;
          saveLocalDoc(Object.assign({}, doc, { idea: doc.idea || idea, page: WGSM.page.id }));
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
      return store.reconnect();
    },

    reconnect: function () {
      return api("health").then(function (health) {
        if (!health.ok) throw new Error(unavailableText);
        store.shared = health.shared === true;
        return api("feedback/" + WGSM.page.id);
      }).then(function (doc) {
        store.doc = doc;
        store.available = true;
        store.error = null;
        clearTimeout(retryTimer);
        retryTimer = null;
        retryDelay = 500;
        WGSM.emit("notes-connection", true);
        WGSM.emit("notes", doc);
        return doc;
      }, function (err) {
        disconnected(err);
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
        store.error = store.mode === "server" && !store.available ? unavailableText : err.message;
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

    /* Every page's notes this browser can see: its own, plus (when
     * published) the committed ones. Not used with serve.py. */
    allDocs: function () {
      if (store.mode === "server") return api("export").then(function (data) { return data.pages; });
      var docs = {};
      for (var i = 0; i < localStorage.length; i++) {
        var key = localStorage.key(i);
        if (key.indexOf(LOCAL_PREFIX) !== 0) continue;
        try {
          var doc = JSON.parse(localStorage.getItem(key));
          docs[(doc.idea || idea) + "\0" + doc.page] = Object.assign({}, doc, { idea: doc.idea || idea });
        } catch (err) {
          continue;
        }
      }
      var committed = store.published ? committedDocs() : Promise.resolve([]);
      return committed.then(function (list) {
        list.forEach(function (doc) {
          var identity = doc.idea || idea;
          var key = identity + "\0" + doc.page;
          var mine = docs[key] || { notes: {}, page: doc.page, idea: identity, title: doc.title };
          mergeNotes(mine.notes, doc.notes || {});
          mine.title = doc.title || mine.title;
          docs[key] = mine;
        });
        return Object.keys(docs).sort().map(function (page) {
          return docs[page];
        });
      });
    },

    /* Open/resolved counts per page */
    summary: function () {
      if (store.mode === "server") return api("feedback").catch(function () {
        return { pages: [], error: store.error };
      });
      return store.allDocs().then(function (docs) {
        return {
          pages: docs.filter(function (doc) { return doc.idea === idea; }).map(function (doc) {
            var notes = Object.keys(doc.notes || {}).map(function (id) {
              return doc.notes[id];
            });
            var resolved = notes.filter(function (n) {
              return n.status === "resolved";
            }).length;
            return { page: doc.page, title: doc.title, open: notes.length - resolved, resolved: resolved };
          }),
        };
      });
    },

    exportFile: function () {
      var doc = Object.assign({}, store.doc, { about: store.about() });
      var blob = new Blob([JSON.stringify(sortKeys(doc), null, 2) + "\n"], { type: "application/json" });
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

    /* The branch a preview was built from, else "" (main or a checkout) */
    branch: function () {
      return idea === "main" ? "" : idea;
    },

    about: function () {
      var branch = store.branch();
      var text = branch
        ? window.WGSM_ABOUT.replace("on branch main.", "on branch " + branch + " (a preview; it is merged into main by pull request).")
        : window.WGSM_ABOUT;
      return text + ' An all-notes export wraps these documents in "pages"; each page has an "idea" (main, a mockups/* GitHub branch or a local/* SSH-pushed branch) and may have a "url" relative to the review server. Local pushes are not on GitHub. Apply each document to its own idea. Importing the export merges by note id and updated timestamp.';
    },

    author: function (name) {
      if (arguments.length) {
        chosenAuthor = name;
        WGSM.pref("author", name);
      }
      return chosenAuthor === null ? WGSM.pref("author") || "" : chosenAuthor;
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
