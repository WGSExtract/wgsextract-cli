/* WGSM: the mockup runtime namespace and small DOM helpers shared by all
 * other scripts. */
(function () {
  "use strict";

  var WGSM = (window.WGSM = window.WGSM || {});
  WGSM.root = window.WGSM_ROOT || "../";
  WGSM.page = {
    id: document.body.dataset.pageId || "unknown",
    title: document.body.dataset.title || document.title,
    direction: document.body.dataset.direction || "",
    shell: document.body.dataset.shell || "none",
    nav: document.body.dataset.nav || "",
  };
  WGSM.params = new URLSearchParams(location.search);

  /* h("button.btn.btn--primary", {type: "button", onclick: fn}, "Text", child) */
  WGSM.h = function (spec, attrs) {
    var parts = spec.split(".");
    var el = document.createElement(parts[0] || "div");
    if (parts.length > 1) el.className = parts.slice(1).join(" ");
    var children = Array.prototype.slice.call(arguments, 2);
    if (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs)) {
      children.unshift(attrs);
      attrs = null;
    }
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === undefined || value === null || value === false) return;
      if (key.slice(0, 2) === "on") el.addEventListener(key.slice(2), value);
      else if (key === "dataset") Object.assign(el.dataset, value);
      else if (key === "style" && typeof value === "object") setStyle(el, value);
      else el.setAttribute(key, value === true ? "" : value);
    });
    WGSM.append(el, children);
    return el;
  };

  /* Custom properties (--v) need setProperty; the rest are style keys */
  function setStyle(el, styles) {
    Object.keys(styles).forEach(function (key) {
      if (key.slice(0, 2) === "--") el.style.setProperty(key, styles[key]);
      else el.style[key] = styles[key];
    });
  }

  WGSM.append = function (el, children) {
    children.forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      if (Array.isArray(child)) WGSM.append(el, child);
      else if (typeof child === "string") el.appendChild(document.createTextNode(child));
      else if (child instanceof Node) el.appendChild(child);
      else el.appendChild(document.createTextNode(String(child)));
    });
    return el;
  };

  WGSM.qs = function (selector, scope) {
    return (scope || document).querySelector(selector);
  };

  WGSM.qsa = function (selector, scope) {
    return Array.prototype.slice.call((scope || document).querySelectorAll(selector));
  };

  /* Local settings shared by every mockup page (theme, viewport, author) */
  WGSM.pref = function (key, value) {
    var storageKey = "wgsm." + key;
    try {
      if (arguments.length > 1) localStorage.setItem(storageKey, value);
      return localStorage.getItem(storageKey);
    } catch (err) {
      return null;
    }
  };

  WGSM.link = function (path) {
    return WGSM.root + path;
  };

  /* Links between mockup pages carry the review settings along */
  WGSM.keepParams = function (href) {
    var url = new URL(href, location.href);
    ["vp", "theme"].forEach(function (key) {
      if (WGSM.params.has(key) && !url.searchParams.has(key)) {
        url.searchParams.set(key, WGSM.params.get(key));
      }
    });
    return url.href;
  };

  WGSM.isTyping = function (event) {
    var el = event.target;
    return el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  };

  WGSM.relTime = function (iso) {
    var seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (!isFinite(seconds)) return "";
    if (seconds < 60) return "just now";
    var minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + " min ago";
    var hours = Math.round(minutes / 60);
    if (hours < 24) return hours + " h ago";
    var days = Math.round(hours / 24);
    if (days < 30) return days + " d ago";
    return new Date(iso).toLocaleDateString();
  };

  WGSM.uid = function (prefix) {
    var bytes = new Uint8Array(5);
    crypto.getRandomValues(bytes);
    return prefix + Array.prototype.map.call(bytes, function (b) {
      return ("0" + b.toString(16)).slice(-2);
    }).join("");
  };

  WGSM.emit = function (name, detail) {
    document.dispatchEvent(new CustomEvent("wgsm:" + name, { detail: detail }));
  };

  WGSM.on = function (name, handler) {
    document.addEventListener("wgsm:" + name, function (event) {
      handler(event.detail);
    });
  };
})();
