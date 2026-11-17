/* The searchable glossary is drawn from exactly the same entries as tooltips. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;

  WGSM.render.blocks["v2-learn-glossary"] = function (el) {
    var terms = WGSM.v2terms.terms;
    var keys = Object.keys(terms).sort(function (a, b) {
      return terms[a].term.localeCompare(terms[b].term, "en", { sensitivity: "base" });
    });
    var search = h("input.v2-input", { type: "search", placeholder: "Try BAM, Binary Alignment Map, or coverage",
      "aria-label": "Search the full glossary", "aria-controls": "learn-entries" });
    var count = h("p.v2-small.v2-muted", { role: "status", "aria-live": "polite" });
    var letters = h("nav.v2-glossary__letters", { "aria-label": "Glossary letters" });
    var list = h("div", { id: "learn-entries", dataset: { v2NoTerms: "" } });
    var seen = {};
    var rows = keys.map(function (key) {
      var entry = terms[key];
      var letter = entry.term.charAt(0).toUpperCase();
      var first = !seen[letter];
      seen[letter] = true;
      if (first) letters.appendChild(h("a.v2-link", { href: "#term-" + key }, letter));
      var row = h("article", { id: "term-" + key },
        h("h3", h("a.v2-link", { href: "#term-" + key }, entry.term),
          entry.full ? " · " + entry.full : ""),
        h("p", entry.short), h("p", entry.more),
        h("a.v2-link.v2-small", { href: WGSM.keepParams(entry.learn) }, "Read the lesson →"));
      list.appendChild(row);
      return { node: row, search: [entry.term, entry.full || "", entry.short, entry.more].concat(entry.aliases || []).join(" ").toLowerCase() };
    });
    function filter() {
      var query = search.value.trim().toLowerCase();
      var n = 0;
      rows.forEach(function (row) {
        row.node.hidden = !!query && row.search.indexOf(query) < 0;
        if (!row.node.hidden) n++;
      });
      letters.hidden = !!query;
      count.textContent = n + " of " + keys.length + " terms" + (n ? "" : " — try a different word");
    }
    function revealAnchor() {
      var target = document.getElementById(location.hash.slice(1));
      if (!target || !list.contains(target)) return;
      search.value = "";
      filter();
      target.scrollIntoView({ block: "start" });
    }
    search.addEventListener("input", filter);
    window.addEventListener("hashchange", revealAnchor);
    WGSM.append(el, [search, count, letters, list]);
    filter();
    /* After the rest of the page has started (not a frame callback, which
     * a backgrounded or headless window may hold back) */
    setTimeout(revealAnchor);
  };
})();
