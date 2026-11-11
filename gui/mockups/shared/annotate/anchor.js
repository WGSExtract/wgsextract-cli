/* Anchors tie a note to a place on the page that survives reloads, resizes,
 * viewport switches and small markup edits:
 *   annot  the nearest [data-annot-id] ancestor (stable ids in the mockups)
 *   path   CSS child path from that ancestor to the clicked element
 *   x, y   the click as a fraction of the element's box
 *   w, h   for a dragged region, its size as a fraction of the box
 * If the path no longer matches, the note falls back to the ancestor. */
(function () {
  "use strict";
  var WGSM = window.WGSM;

  function annotRoot(el) {
    return el.closest("[data-annot-id]");
  }

  function usable(el) {
    var svg = el.closest("svg");
    if (svg && svg.parentElement) el = svg.parentElement;
    return el;
  }

  function childPath(base, el) {
    var parts = [];
    while (el && el !== base) {
      var tag = el.tagName.toLowerCase();
      var index = 1;
      var sib = el;
      while ((sib = sib.previousElementSibling)) {
        if (sib.tagName === el.tagName) index++;
      }
      parts.unshift(tag + ":nth-of-type(" + index + ")");
      el = el.parentElement;
    }
    return parts.length ? ":scope > " + parts.join(" > ") : "";
  }

  function label(el) {
    var aria = el.getAttribute("aria-label");
    var text = (aria || el.textContent || "").replace(/\s+/g, " ").trim();
    var tag = el.tagName.toLowerCase();
    var kind = el.matches("button, .btn, [role=button]") ? "button"
      : el.matches("a") ? "link"
      : el.matches("input, select, textarea") ? "field"
      : /^h[1-6]$/.test(tag) ? "heading"
      : el.matches("tr") ? "row"
      : el.dataset.annotId ? el.dataset.annotId
      : tag;
    if (text.length > 48) text = text.slice(0, 46) + "…";
    return text ? kind + " “" + text + "”" : kind;
  }

  /* The visible part of an element: its box cut by scrolling/clipping
   * ancestors up to the app viewport. Null when hidden or scrolled away. */
  function visibleRect(el) {
    if (!el.getClientRects().length) return null;
    var r = el.getBoundingClientRect();
    var box = { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    var node = el.parentElement;
    while (node && node !== document.body) {
      var style = getComputedStyle(node);
      if (style.overflowX !== "visible" || style.overflowY !== "visible") {
        var c = node.getBoundingClientRect();
        box.left = Math.max(box.left, c.left);
        box.top = Math.max(box.top, c.top);
        box.right = Math.min(box.right, c.right);
        box.bottom = Math.min(box.bottom, c.bottom);
      }
      if (node.classList.contains("mk-stage")) break;
      node = node.parentElement;
    }
    return box.right > box.left && box.bottom > box.top ? box : null;
  }

  WGSM.anchor = {
    target: usable,

    describe: function (el, point, region) {
      el = usable(el);
      var base = annotRoot(el);
      if (region) {
        while (base && base.parentElement) {
          var b = base.getBoundingClientRect();
          if (b.left <= region.left && b.top <= region.top && b.right >= region.left + region.width && b.bottom >= region.top + region.height) break;
          var up = annotRoot(base.parentElement);
          if (!up) break;
          base = up;
        }
        el = base;
      }
      var rect = el.getBoundingClientRect();
      var w = rect.width || 1;
      var hgt = rect.height || 1;
      var anchor = {
        annot: base ? base.dataset.annotId : "",
        path: base ? childPath(base, el) : "",
        label: label(el),
      };
      var px = region ? region.left : point.x;
      var py = region ? region.top : point.y;
      anchor.x = round((px - rect.left) / w);
      anchor.y = round((py - rect.top) / hgt);
      if (region) {
        anchor.w = round(region.width / w);
        anchor.h = round(region.height / hgt);
      }
      return anchor;
    },

    resolve: function (anchor) {
      if (!anchor || !anchor.annot) return null;
      var base = document.querySelector('[data-annot-id="' + CSS.escape(anchor.annot) + '"]');
      if (!base) return null;
      if (!anchor.path) return base;
      try {
        return base.querySelector(anchor.path) || base;
      } catch (err) {
        return base;
      }
    },

    /* Client position of the anchor point (and region box) or null */
    place: function (anchor) {
      var el = this.resolve(anchor);
      if (!el) return { missing: true };
      var visible = visibleRect(el);
      if (!visible) return { hidden: true, el: el };
      var r = el.getBoundingClientRect();
      var x = r.left + anchor.x * r.width;
      var y = r.top + anchor.y * r.height;
      var placed = { el: el, x: x, y: y };
      if (anchor.w) {
        placed.box = { left: x, top: y, width: anchor.w * r.width, height: anchor.h * r.height };
      }
      placed.x = Math.max(visible.left, Math.min(visible.right, x));
      placed.y = Math.max(visible.top, Math.min(visible.bottom, y));
      return placed;
    },

    visibleRect: visibleRect,
  };

  function round(n) {
    return Math.round(Math.max(-1, Math.min(2, n)) * 10000) / 10000;
  }
})();
