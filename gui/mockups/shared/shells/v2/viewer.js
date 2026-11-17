/* The genome viewer page (viewer.html): igv.js over small simulated windows
 * of GRCh38 (shared/data/igv/, made by build_igv_demo.py). The page's
 * markup holds the spot notes so glossary terms hydrate as usual; this
 * module holds the coordinates, the tracks and what gates them.
 *
 *   ?spot=apoe        open at a spot (see SPOTS)
 *   ?locus=chr19:44908684  open at a position (gene, rsID or chr:pos)
 *   ?person=gordon    turn on a person's track as well as Reginald's
 *   ?demo=noref       the hs38 reference is missing, so CRAMs can't open
 *   ?demo=error       a file fails to load
 *
 * igv.js never reaches the network here: the page's CSP limits connections
 * to its own origin and loopback (igv.js otherwise fetches igv.org URL
 * mappings after any 404), BLAT menu items are removed, and the remote
 * feature search is off. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var DATA = "shared/data/igv/";

  /* 1-based windows that hold reads; anything else is outside the demo */
  var WINDOWS = [
    ["chr1", 11791322, 11801321], ["chr2", 135846077, 135856076], ["chr5", 70940001, 70950000],
    ["chr6", 26087914, 26097913], ["chr17", 43066078, 43076077], ["chr19", 44903685, 44913684],
    ["chr20", 32000001, 32010000], ["chr20", 45000001, 45010000], ["chr20", 52000001, 52010000],
    ["chrY", 20582968, 20592967], ["chrM", 1, 16569],
  ];

  var SPOTS = {
    intro: { locus: "chr19:44908634-44908734" },
    apoe: { locus: "chr19:44908634-44908734", people: ["helen"] },
    hfe: { locus: "chr6:26092863-26092963", people: ["gordon"] },
    mthfr: { locus: "chr1:11796271-11796371", people: ["alex"] },
    brca1: { locus: "chr17:43071027-43071127" },
    lct: { locus: "chr2:135851026-135851126", people: ["gordon"] },
    y: { locus: "chrY:20587917-20588017", people: ["gordon"] },
    mt: { locus: "chrM:16200-16330", people: ["gordon", "helen"] },
    denovo: { locus: "chr20:32005073-32005173", people: ["gordon", "helen"] },
    smalldel: { locus: "chr20:52007440-52007520", people: ["gordon", "helen"] },
    bigdel: { locus: "chr20:52002400-52004800", people: ["gordon"] },
    /* Full-height rows, so the few reverse-strand T reads stand out */
    artifact: { locus: "chr20:45004296-45004346", view: { colorBy: "strand", displayMode: "EXPANDED", height: 470 } },
    smn: { locus: "chr5:70942600-70946600" },
  };

  /* What the search box knows besides chr:pos */
  var NAMES = {
    APOE: "apoe", RS429358: "apoe", HFE: "hfe", C282Y: "hfe", RS1800562: "hfe",
    MTHFR: "mthfr", RS1801133: "mthfr", BRCA1: "brca1", RS1799966: "brca1",
    LCT: "lct", MCM6: "lct", RS4988235: "lct", M304: "y", SMN1: "smn", SMN: "smn",
  };

  var PEOPLE = {
    reginald: { file: "reginald-mcdonald.GRCh38.30x.cram", index: ".crai", format: "cram" },
    gordon: { file: "60820188479016_aligned.cram", index: ".crai", format: "cram", gate: "realign-done" },
    helen: { file: "helen-mcdonald.deepvariant.vcf.gz", index: ".tbi", format: "vcf" },
    alex: { file: "NG1A2B3C4D.bam", index: ".csi", format: "bam", gate: "alex-fixed" },
  };
  var ORDER = ["reginald", "gordon", "helen", "alex"];

  var VIEW = { colorBy: "unexpectedPair", displayMode: "SQUISHED", height: 200 };
  var root, browser, view = VIEW, spot = "intro", loading = null;
  var on = { reginald: true };

  function demo() {
    return WGSM.params.get("demo") || "";
  }

  function noRef() {
    return demo() === "noref" || WGSM.v2state.doc.flags["ref-hs38"] === false;
  }

  function first(id) {
    var p = WGSM.v2state.person(id);
    return p ? WGSM.v2state.first(p.name) : id;
  }

  /* Why a person's track can't be drawn right now, or null */
  function blocked(id) {
    var p = PEOPLE[id];
    if (p.gate && !WGSM.v2state.flag(p.gate)) return id;
    if (p.format === "cram" && noRef()) return "noref";
    return null;
  }

  function trackName(id) {
    return first(id) + " · " + PEOPLE[id].file;
  }

  function trackConfig(id) {
    var p = PEOPLE[id];
    var url = WGSM.link(DATA + p.file);
    if (demo() === "error" && id === "reginald") url = WGSM.link(DATA + "missing-" + p.file);
    var base = { name: trackName(id), url: url, indexURL: WGSM.link(DATA + p.file + p.index), format: p.format, order: ORDER.indexOf(id) + 10 };
    if (p.format === "vcf") return Object.assign(base, { type: "variant", displayMode: "EXPANDED", height: 50, visibilityWindow: 100000 });
    return Object.assign(base, {
      type: "alignment",
      /* The demo reference is a set of slices, so its MD5s can't match */
      checkSequenceMD5: false,
      colorBy: view.colorBy,
      showSoftClips: true,
      displayMode: view.displayMode,
      squishedRowHeight: 5,
      height: view.height,
      coverageTrackHeight: 40,
      visibilityWindow: 30000,
    });
  }

  function gates() {
    var reasons = {};
    ORDER.forEach(function (id) {
      if (!on[id]) return;
      var why = blocked(id);
      if (why) reasons[why] = true;
      if (id === "helen") reasons.helen = true;
    });
    WGSM.qsa("[data-v2-viewer-gate]", root).forEach(function (el) {
      if (el.dataset.v2ViewerGate !== "error") el.hidden = !reasons[el.dataset.v2ViewerGate];
    });
  }

  function message(text) {
    var el = WGSM.qs("[data-v2-viewer-msg]", root);
    el.textContent = text || "";
    el.hidden = !text;
  }

  function showError(text) {
    var el = WGSM.qs('[data-v2-viewer-gate="error"]', root);
    WGSM.qs("[data-v2-viewer-error]", el).textContent = text;
    el.hidden = !text;
  }

  /* Add, remove or reload tracks so the browser matches `on` */
  function sync(reload) {
    gates();
    if (!browser) return Promise.resolve();
    var jobs = ORDER.map(function (id) {
      var name = trackName(id);
      var have = browser.findTracks(function (t) { return t.name === name; }).length > 0;
      var want = on[id] && !blocked(id);
      if (have && (!want || (reload && PEOPLE[id].format !== "vcf"))) {
        browser.removeTrackByName(name);
        have = false;
      }
      if (want && !have) {
        var config = trackConfig(id);
        return reachable([config.url, config.indexURL])
          .then(function () {
            return browser.loadTrack(config);
          })
          .catch(function (err) {
            showError(err && err.missing
              ? first(id) + "’s " + PEOPLE[id].file + " isn’t where it was added from. If you moved it, add it again from its new place."
              : first(id) + "’s file: " + (err && err.message ? err.message : err));
          });
      }
      return null;
    });
    return Promise.all(jobs);
  }

  /* igv.js reports a failed load only in its own dialog, and after a 404
   * looks up a URL-mapping list on igv.org; check the files first instead */
  function reachable(urls) {
    return Promise.all(
      urls.map(function (url) {
        /* HEAD and uncached: a cached partial response can be stitched onto
         * igv.js's later full request, which hosts like GitHub Pages
         * gzip on the fly, and corrupt it */
        return fetch(url, { method: "HEAD", cache: "no-store" }).then(function (res) {
          if (!res.ok) throw Object.assign(new Error(url.split("/").pop() + " wasn’t found (" + res.status + ")."), { missing: true });
        });
      }),
    );
  }

  function inWindow(chrom, start, end) {
    return WINDOWS.some(function (w) {
      return w[0] === chrom && start >= w[1] && end <= w[2];
    });
  }

  function note(id) {
    WGSM.qsa("[data-v2-spot-note]", root).forEach(function (el) {
      el.hidden = el.dataset.v2SpotNote !== id;
    });
  }

  function select(id, opts) {
    var s = SPOTS[id];
    if (!s) return;
    spot = id;
    WGSM.qsa("[data-v2-spot]", root).forEach(function (b) {
      b.setAttribute("aria-current", b.dataset.v2Spot === id ? "true" : "false");
    });
    (s.people || []).forEach(function (p) {
      if (opts && opts.keepPeople) return;
      on[p] = true;
    });
    checkboxes();
    note(id);
    message("");
    var want = s.view || VIEW;
    var restyle = want !== view;
    view = want;
    if (!browser) return;
    sync(restyle).then(function () {
      return browser.search(s.locus);
    });
    if (!opts || !opts.quiet) updateUrl({ spot: id, locus: null });
  }

  function checkboxes() {
    WGSM.qsa("[data-v2-track]", root).forEach(function (box) {
      box.checked = !!on[box.dataset.v2Track];
    });
  }

  function updateUrl(params) {
    var url = new URL(location.href);
    Object.keys(params).forEach(function (k) {
      if (params[k] === null) url.searchParams.delete(k);
      else url.searchParams.set(k, params[k]);
    });
    history.replaceState(null, "", url.href);
  }

  /* chr19:44908684, chr19:44,908,600-44,908,800, 19:44908684, APOE, rs429358 */
  function parse(text) {
    var q = text.trim().replace(/,/g, "");
    if (!q) return null;
    var name = NAMES[q.toUpperCase()];
    if (name) return { spot: name };
    var m = /^(chr)?([0-9]{1,2}|X|Y|M|MT)[: ]([0-9]+)(?:-([0-9]+))?$/i.exec(q);
    if (!m) return { unknown: q };
    var chrom = "chr" + m[2].toUpperCase().replace(/^MT$/, "M");
    var start = Number(m[3]);
    var end = m[4] ? Number(m[4]) : start;
    if (end < start) return { unknown: q };
    if (!m[4]) {
      start -= 50;
      end += 50;
    }
    return { chrom: chrom, start: Math.max(1, start), end: end };
  }

  function search(text) {
    var hit = parse(text);
    if (!hit) return;
    if (hit.spot) return select(hit.spot);
    if (hit.unknown) {
      message("“" + hit.unknown + "” isn’t a gene or position the demo knows. Try APOE, rs429358 or chr19:44908684.");
      return;
    }
    var locus = hit.chrom + ":" + hit.start + "-" + hit.end;
    if (!inWindow(hit.chrom, hit.start, hit.end)) {
      note("outside");
      message(locus + " is outside the demo data.");
      return;
    }
    message("");
    note(spot);
    if (browser) browser.search(locus);
    updateUrl({ locus: locus, spot: null });
  }

  /* igv.js adds BLAT items to read and sequence menus unconditionally; BLAT
   * would send sequence to UCSC, so they never appear here */
  function stripBlat(host) {
    function strip() {
      WGSM.qsa(".igv-menu-popup-content > div, .igv-popover div", host).forEach(function (el) {
        if (/^\s*BLAT\b/i.test(el.textContent) && el.children.length === 0) el.remove();
      });
    }
    new MutationObserver(strip).observe(host, { childList: true, subtree: true });
    new MutationObserver(strip).observe(document.body, { childList: true });
  }

  function loadScript() {
    if (window.igv) return Promise.resolve(window.igv);
    if (loading) return loading;
    loading = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = WGSM.link("shared/vendor/igv/igv.min.js");
      s.onload = function () { resolve(window.igv); };
      s.onerror = function () { reject(new Error("igv.min.js didn’t load")); };
      document.head.appendChild(s);
    });
    return loading;
  }

  function startLocus() {
    var hit = WGSM.params.get("locus") && parse(WGSM.params.get("locus"));
    if (hit && hit.chrom && inWindow(hit.chrom, hit.start, hit.end)) return hit.chrom + ":" + hit.start + "-" + hit.end;
    return SPOTS[spot].locus;
  }

  function create() {
    var host = WGSM.qs("[data-v2-viewer-igv]", root);
    showError("");
    /* Browsers don't let a file:// page read other files, so the demo data needs a server */
    if (location.protocol === "file:") {
      showError("The demo reads need the mockups served over HTTP: run just mockups and open the page from there.");
      return Promise.resolve();
    }
    return loadScript().then(function (igv) {
      var wait = WGSM.qs("[data-v2-viewer-loading]", host);
      return igv.createBrowser(host, {
        reference: {
          id: "GRCh38-demo",
          name: "GRCh38 (demo stretches)",
          fastaURL: WGSM.link(DATA + "GRCh38-demo.fa"),
          indexed: false,
          tracks: [{ name: "Genes", url: WGSM.link(DATA + "genes.bed"), format: "bed", type: "annotation", displayMode: "EXPANDED", height: 36, order: 1 }],
        },
        locus: startLocus(),
        loadDefaultGenomes: false,
        search: false,
        blatServerURL: WGSM.link(DATA + "no-blat"),
        showCytobandIdeogram: false,
        showChromosomeWidget: false,
        showSampleNames: false,
      }).then(function (b) {
        if (wait) wait.remove();
        browser = b;
        stripBlat(host);
        return sync();
      });
    }).catch(function (err) {
      showError(err && err.message ? err.message : String(err));
    });
  }

  function names() {
    WGSM.qsa("[data-v2-name]", root).forEach(function (el) {
      el.textContent = first(el.dataset.v2Name);
    });
    var alex = WGSM.v2state.person("alex");
    WGSM.qsa('[data-v2-track="alex"]', root).forEach(function (box) {
      box.closest("label").hidden = !!(alex && alex.removed);
    });
  }

  function start() {
    root = WGSM.qs("[data-v2-viewer]");
    if (!root) return;
    var asked = WGSM.params.get("spot");
    if (asked && SPOTS[asked]) spot = asked;
    var person = WGSM.params.get("person");
    if (person && PEOPLE[person]) on[person] = true;
    (SPOTS[spot].people || []).forEach(function (p) {
      if (spot !== "intro") on[p] = true;
    });
    view = SPOTS[spot].view || VIEW;
    names();
    checkboxes();
    note(spot);
    WGSM.qsa("[data-v2-spot]", root).forEach(function (b) {
      b.setAttribute("aria-current", b.dataset.v2Spot === spot ? "true" : "false");
    });

    root.addEventListener("click", function (event) {
      var b = event.target.closest("[data-v2-spot]");
      if (b) select(b.dataset.v2Spot);
      if (event.target.closest("[data-v2-viewer-retry]")) {
        if (browser) sync(true);
        else create();
      }
    });
    root.addEventListener("change", function (event) {
      var box = event.target.closest("[data-v2-track]");
      if (!box) return;
      on[box.dataset.v2Track] = box.checked;
      sync();
    });
    WGSM.qs("[data-v2-viewer-search]", root).addEventListener("submit", function (event) {
      event.preventDefault();
      search(WGSM.qs("input", event.target).value);
    });
    WGSM.on("v2-flags", function () {
      names();
      sync();
    });
    create();
  }

  WGSM.v2viewer = {
    start: start,
    parse: parse,
    inWindow: inWindow,
    spots: SPOTS,
    browser: function () {
      return browser;
    },
  };
})();
