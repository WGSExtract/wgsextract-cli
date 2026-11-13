/* Cross-page review, including the static-site/localStorage workflow. */
(function start() {
  "use strict";
  if (!window.WGSM || !window.WGSM.notes) { setTimeout(start, 20); return; }
  var W = window.WGSM, h = W.h, docs = [];
  function status(text) { W.qs("#notes-status").textContent = text; }
  function fallbackPath(page) {
    if (/^[abc]-[a-z0-9-]+$/.test(page)) return "directions/" + page[0] + "/" + page.slice(2) + ".html";
    if (page === "icons") return "icons/icons.html";
    if (page === "design-system") return "design-system/design-system.html";
    return "index.html";
  }
  function render() {
    var list = W.qs("#all-notes"), count = 0;
    list.replaceChildren();
    docs.forEach(function (doc) {
      Object.keys(doc.notes || {}).forEach(function (id) {
        var note = doc.notes[id], idea = doc.idea || W.notes.idea;
        var fields = { idea: idea, page: doc.page, status: note.status, author: note.author };
        if (Object.keys(fields).some(function (key) {
          var filter = W.qs("#filter-" + key).value.toLowerCase();
          return filter && String(fields[key]).toLowerCase().indexOf(filter) < 0;
        })) return;
        var url;
        if (W.notes.mode === "server") {
          url = new URL(doc.url || ((idea === "main" ? "/" : "/preview/" + encodeURIComponent(idea) + "/") + fallbackPath(doc.page)), location.href);
        } else {
          url = new URL(W.link(fallbackPath(doc.page)), location.href);
        }
        url.searchParams.set("note", id);
        var item = h("li.card.stack",
          h("a", { href: url.href }, idea + " · " + (doc.title || doc.page) + " · " + note.author + " · " + note.status),
          h("p", { style: { whiteSpace: "pre-wrap" } }, note.text));
        (note.replies || []).forEach(function (reply) {
          item.appendChild(h("p.small", { style: { whiteSpace: "pre-wrap" } }, reply.author + ": " + reply.text));
        });
        list.appendChild(item);
        count++;
      });
    });
    status(count + " notes · " + (W.notes.mode === "server" ? "shared server" : "this browser"));
  }
  function load() {
    return W.notes.allDocs().then(function (pages) { docs = pages; render(); }).catch(function (err) { status(err.message); });
  }
  function importLocal(incoming) {
    if (!incoming || !Array.isArray(incoming.pages)) throw new Error("Expected an all-notes export");
    incoming.pages.forEach(function (doc) {
      if (!doc.page || !doc.notes) throw new Error("Invalid page");
      if ((doc.idea || W.notes.idea) !== W.notes.idea) throw new Error("Import each idea on its own static preview, or use the shared server");
    });
    incoming.pages.forEach(function (doc) {
      var key = "wgsm.notes." + doc.page;
      var old = JSON.parse(localStorage.getItem(key)) || { notes: {} };
      Object.keys(doc.notes).forEach(function (id) {
        if (!old.notes[id] || String(doc.notes[id].updated) > String(old.notes[id].updated)) old.notes[id] = doc.notes[id];
      });
      localStorage.setItem(key, JSON.stringify(Object.assign({}, doc, { notes: old.notes })));
    });
    return Promise.resolve();
  }
  function init() {
    ["idea", "page", "status", "author"].forEach(function (key) { W.qs("#filter-" + key).addEventListener("input", render); });
    W.qs("#refresh-notes").addEventListener("click", load);
    W.qs("#export-notes").addEventListener("click", function () {
      W.notes.allDocs().then(function (pages) {
        var blob = new Blob([JSON.stringify({ schema: 1, about: W.notes.about(), pages: pages }, null, 2) + "\n"], { type: "application/json" });
        var url = URL.createObjectURL(blob);
        var a = h("a", { href: url, download: "all-review-notes.json" });
        document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      }).catch(function (err) { status(err.message); });
    });
    W.qs("#import-notes").addEventListener("change", function (event) {
      var file = event.target.files[0];
      if (!file) return;
      file.text().then(JSON.parse).then(function (doc) {
        return W.notes.mode === "server" ? W.notes.api("import", doc) : importLocal(doc);
      }).then(load).catch(function (err) { status(err.message); });
      event.target.value = "";
    });
    load();
    W.on("notes-connection", function (available) {
      if (available) load();
      else status(W.notes.error);
    });
  }
  if (W.notes.ready) init();
  else W.on("notes-ready", init);
})();
