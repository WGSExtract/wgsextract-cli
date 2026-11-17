/* Jargon explained where it appears: <abbr class="v2-term" data-term="vcf">VCF</abbr>
 * shows the term's entry (glossary-data.js, loaded first) on hover, focus,
 * click or tap.
 *   wrap(scope)  marks the first plain-text use of each known term in scope,
 *                for text the tool engine renders; safe to call again.
 *   start()      once per page: wires the explanation popover for every
 *                [data-term], present or added later.
 * The popover lives in the app viewport (WGSM.chrome.vp), like dialogs, so
 * it stays inside the phone frame. It is a tooltip while previewed (hover,
 * focus) and a small non-modal dialog once opened by click, tap or Enter;
 * Tab then moves into its "Learn more" link. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var api = (WGSM.v2terms = WGSM.v2terms || {});
  api.terms = api.terms || {};

  var SKIP = "code, pre, kbd, samp, a, button, label, input, select, option, textarea, summary, " +
    "script, style, svg, nav, h1, abbr, [aria-hidden='true'], .v2-cmd, .v2-mono, .v2-file__name, .v2-tag, " +
    ".v2-term, [data-v2-no-terms]";
  var WORD = "A-Za-z0-9_\\-";
  var FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select, textarea, summary, [tabindex]:not([tabindex='-1'])";
  var HOVER_IN = 120;
  var HOVER_OUT = 180;

  /* One regular expression for every term and alias, longest first. A word
   * with a capital letter must match exactly (VCF, not vcf); others match
   * in any case (haplogroup, Haplogroup). */
  var matcher = null;
  function getMatcher() {
    if (matcher) return matcher;
    var exact = {};
    var folded = {};
    var words = [];
    Object.keys(api.terms).forEach(function (key) {
      var entry = api.terms[key];
      [entry.term].concat(entry.aliases || []).forEach(function (word) {
        if (/[A-Z]/.test(word)) exact[word] = key;
        else folded[word] = key;
        words.push(word);
      });
    });
    words.sort(function (a, b) {
      return b.length - a.length;
    });
    var alternatives = words.map(function (w) {
      return w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    });
    matcher = {
      re: new RegExp("(?<![" + WORD + "])(?:" + alternatives.join("|") + ")(?![" + WORD + "])", "gi"),
      key: function (text) {
        return exact[text] || folded[text.toLowerCase()] || null;
      },
    };
    return matcher;
  }

  function prepare(el) {
    if (!api.terms[el.dataset.term] || el.dataset.termReady) return;
    el.dataset.termReady = "";
    if (!el.hasAttribute("tabindex")) el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.setAttribute("aria-expanded", "false");
  }

  /* In a flex or grid box each piece of text becomes its own item, so
   * wrapping part of it would break the line apart */
  function inFlow(node) {
    return !/flex|grid/.test(getComputedStyle(node.parentElement).display);
  }

  function textNodes(scope) {
    var nodes = [];
    var walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var parent = node.parentElement;
        return node.nodeValue.trim() && parent && !parent.closest(SKIP) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    while (walker.nextNode()) nodes.push(walker.currentNode);
    return nodes;
  }

  /* Name the site an external reference opens, so a reader knows where it goes */
  var SOURCES = { "h600.org": "Read more on the H600 wiki", "samtools.github.io": "Format specification", "www.ncbi.nlm.nih.gov": "NCBI reference" };
  api.source = function (url) {
    if (!url) return "";
    var host = new URL(url).hostname.replace(/^wiki\.(?=h600\.org$)/, "");
    return SOURCES[host] || "External reference";
  };

  api.wrap = function (scope) {
    if (!scope || !Object.keys(api.terms).length) return;
    var m = getMatcher();
    var used = {};
    WGSM.qsa("[data-term]", scope).forEach(function (el) {
      used[el.dataset.term] = true;
      prepare(el);
    });
    textNodes(scope).forEach(function (node) {
      var match;
      var flows = null;
      m.re.lastIndex = 0;
      while ((match = m.re.exec(node.nodeValue))) {
        var key = m.key(match[0]);
        if (!key || used[key]) continue;
        if (flows === null) flows = inFlow(node);
        if (!flows) break;
        used[key] = true;
        var word = node.splitText(match.index);
        node = word.splitText(match[0].length);
        var abbr = h("abbr.v2-term", { dataset: { term: key } }, match[0]);
        word.replaceWith(abbr);
        prepare(abbr);
        m.re.lastIndex = 0;
      }
    });
  };

  /* The popover: one element, reused for every term */
  var pop = null;
  var current = null;
  var pinned = false;
  var timer = 0;

  function host() {
    return (WGSM.chrome && WGSM.chrome.vp) || document.body;
  }

  function popover() {
    if (pop) return pop;
    pop = h("div.v2-termpop", { id: "v2-termpop", hidden: true, dataset: { annotIgnore: "" } },
      h("strong.v2-termpop__term", { id: "v2-termpop-term" }),
      h("p.v2-termpop__short"),
      h("p.v2-termpop__more"),
      h("a.v2-termpop__link", "Learn more"),
      h("a.v2-termpop__reference", { target: "_blank", rel: "noopener noreferrer" },
        h("span.v2-termpop__source"), WGSM.icon("external-link", "icon--sm"))
    );
    pop.addEventListener("pointerenter", function () {
      clearTimeout(timer);
    });
    pop.addEventListener("pointerleave", function (event) {
      if (event.pointerType === "mouse" && !pinned) later(hide, HOVER_OUT);
    });
    pop.addEventListener("click", function (event) {
      if (event.target.closest("a")) hide();
    });
    pop.addEventListener("keydown", function (event) {
      if (event.key !== "Tab" || !current) return;
      var links = WGSM.qsa("a:not([hidden])", pop);
      var at = links.indexOf(document.activeElement);
      if ((!event.shiftKey && at < links.length - 1) || (event.shiftKey && at > 0)) return;
      event.preventDefault();
      var back = current;
      if (event.shiftKey) {
        back.focus();
        return;
      }
      hide();
      focusAfter(back);
    });
    return pop;
  }

  function later(fn, ms) {
    clearTimeout(timer);
    timer = setTimeout(fn, ms);
  }

  function show(term, pin) {
    var entry = api.terms[term.dataset.term];
    if (!entry) return;
    clearTimeout(timer);
    var el = popover();
    if (current && current !== term) release(current);
    current = term;
    pinned = !!pin;
    WGSM.qs(".v2-termpop__term", el).textContent = entry.term + (entry.full ? " · " + entry.full : "");
    WGSM.qs(".v2-termpop__short", el).textContent = entry.short;
    var more = WGSM.qs(".v2-termpop__more", el);
    more.textContent = entry.more || "";
    more.hidden = !entry.more;
    var link = WGSM.qs(".v2-termpop__link", el);
    link.href = WGSM.keepParams(entry.learn || "learn.html#term-" + term.dataset.term);
    link.setAttribute("aria-label", "Learn more about " + entry.term);
    var reference = WGSM.qs(".v2-termpop__reference", el);
    reference.hidden = !entry.link;
    if (entry.link) reference.href = entry.link;
    else reference.removeAttribute("href");
    var source = api.source(entry.link);
    WGSM.qs(".v2-termpop__source", el).textContent = source;
    reference.setAttribute("aria-label", source + ": " + entry.term + " (opens in a new tab)");
    /* A preview is a tooltip; once opened it is a dialog you can Tab into */
    if (pinned) {
      el.setAttribute("role", "dialog");
      el.setAttribute("aria-labelledby", "v2-termpop-term");
    } else {
      el.setAttribute("role", "tooltip");
      el.removeAttribute("aria-labelledby");
    }
    el.dataset.pinned = pinned ? "true" : "false";
    if (el.parentNode !== host()) host().appendChild(el);
    el.hidden = false;
    term.setAttribute("aria-describedby", "v2-termpop");
    term.setAttribute("aria-expanded", String(pinned));
    term.setAttribute("aria-controls", "v2-termpop");
    place();
  }

  function release(term) {
    term.removeAttribute("aria-describedby");
    term.removeAttribute("aria-controls");
    term.setAttribute("aria-expanded", "false");
  }

  function hide() {
    clearTimeout(timer);
    if (!current) return;
    release(current);
    current = null;
    pinned = false;
    if (pop) pop.hidden = true;
  }

  /* Whether any of the term shows, inside every scrolling box around it */
  function visible(r) {
    if (!current.checkVisibility() || (!r.width && !r.height)) return false;
    for (var el = current.parentElement; el && el !== host().parentElement; el = el.parentElement) {
      var style = getComputedStyle(el);
      if (style.overflowX === "visible" && style.overflowY === "visible") continue;
      var clip = el.getBoundingClientRect();
      if (r.bottom <= clip.top || r.top >= clip.bottom || r.right <= clip.left || r.left >= clip.right) return false;
    }
    return true;
  }

  /* Below the term, or above it when there's more room there, inside the viewport */
  function place() {
    if (!current || !pop) return;
    var box = host().getBoundingClientRect();
    var rects = current.getClientRects();
    var r = rects.length ? rects[0] : current.getBoundingClientRect();
    if (!visible(r)) {
      hide();
      return;
    }
    var gap = 6;
    var edge = 8;
    pop.style.maxWidth = Math.max(160, Math.min(340, box.width - 2 * edge)) + "px";
    pop.style.left = "0px";
    pop.style.top = "0px";
    var w = pop.offsetWidth;
    var ht = pop.offsetHeight;
    var left = r.left - box.left + r.width / 2 - w / 2;
    left = Math.max(edge, Math.min(left, box.width - w - edge));
    var below = r.bottom - box.top + gap;
    var above = r.top - box.top - gap - ht;
    var fitsBelow = below + ht <= box.height - edge;
    var top = fitsBelow || above < edge ? below : above;
    if (!fitsBelow && above < edge) top = Math.max(edge, box.height - ht - edge);
    pop.dataset.side = top === below ? "below" : "above";
    pop.style.left = Math.round(left + host().scrollLeft) + "px";
    pop.style.top = Math.round(top + host().scrollTop) + "px";
  }

  /* Tab out of the popover continues after the term, as if it were inline */
  function focusAfter(term) {
    var all = WGSM.qsa(FOCUSABLE, host()).filter(function (el) {
      return el.checkVisibility() && !pop.contains(el);
    });
    var next = all[all.indexOf(term) + 1];
    (next || term).focus();
  }

  function termOf(event) {
    var el = event.target.closest && event.target.closest("[data-term]");
    if (!el || !api.terms[el.dataset.term]) return null;
    prepare(el);
    return el;
  }

  function annotating() {
    return !!(WGSM.annotate && WGSM.annotate.on);
  }

  function toggle(term) {
    if (current === term && pinned) hide();
    else show(term, true);
  }

  var started = false;
  api.start = function () {
    if (started) return;
    started = true;
    WGSM.qsa("[data-term]").forEach(prepare);

    document.addEventListener("pointerover", function (event) {
      var term = termOf(event);
      if (!term || event.pointerType !== "mouse" || annotating() || pinned) return;
      if (current === term) clearTimeout(timer);
      else later(function () { show(term, false); }, HOVER_IN);
    });
    document.addEventListener("pointerout", function (event) {
      var term = termOf(event);
      if (!term || event.pointerType !== "mouse" || term.contains(event.relatedTarget)) return;
      if (current === term && !pinned && document.activeElement !== term) later(hide, HOVER_OUT);
      else if (current !== term) clearTimeout(timer);
    });
    document.addEventListener("focusin", function (event) {
      var term = termOf(event);
      if (term && current !== term && !annotating()) show(term, false);
    });
    document.addEventListener("focusout", function (event) {
      if (!current || (event.target !== current && !(pop && pop.contains(event.target)))) return;
      var to = event.relatedTarget;
      if (to && (to === current || (pop && pop.contains(to)))) return;
      if (!pinned || to) hide();
    });
    document.addEventListener("click", function (event) {
      var term = termOf(event);
      if (!term || annotating()) return;
      event.preventDefault();
      toggle(term);
    });
    document.addEventListener("keydown", function (event) {
      var term = termOf(event);
      if (term && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        toggle(term);
      } else if (term && event.key === "Tab" && !event.shiftKey && pinned && current === term) {
        var link = pop && WGSM.qs(".v2-termpop__link:not([hidden])", pop);
        if (link) {
          event.preventDefault();
          link.focus();
        }
      }
    });
    /* Escape closes the explanation before anything else (a dialog under it) */
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !current) return;
      var back = current;
      var inside = pop.contains(document.activeElement);
      event.stopPropagation();
      event.preventDefault();
      if (inside) back.focus();
      hide();
    }, true);
    document.addEventListener("pointerdown", function (event) {
      if (!current || current.contains(event.target) || (pop && pop.contains(event.target))) return;
      hide();
    }, true);
    document.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    WGSM.on("viewport", hide);
  };
})();
