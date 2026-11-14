/* Real renderers; only the notes API, icons and pin placement are fixtures. */
(function () {
  "use strict";
  var W = window.WGSM;
  var payload = "<img src=x onerror=window.mockupXssExecuted=true>";
  window.mockupXssExecuted = false;
  var input = document.createElement("textarea");
  input.textContent = payload;
  document.body.appendChild(input);
  var untrusted = input.textContent;
  var message = new Error("Review failed: " + untrusted).message;
  var note = {
    id: "nmarkup", number: 1, author: untrusted, text: untrusted, status: "open",
    anchor: null, context: {}, created: "2026-11-12T00:00:00Z",
    replies: [{ id: "rmarkup", author: untrusted, text: untrusted, created: "2026-11-12T00:00:00Z" }],
  };
  W.page.title = untrusted;
  W.icon = function () { return document.createElement("span"); };
  W.chrome = { vp: document.getElementById("fixture-vp"), notesBtn: document.getElementById("fixture-notes-button") };
  W.pins = { placement: function () { return "shown"; }, schedule: function () {} };
  W.annotate = { filter: "all" };
  W.notes = {
    ready: true, mode: "local", idea: untrusted, hosted: false, available: true, shared: true, error: message,
    doc: { notes: { nmarkup: note } },
    list: function () { return [note]; },
    author: function () { return untrusted; },
    branch: function () { return untrusted; },
    apply: function () { return Promise.reject(new Error(message)); },
    summary: function () { return Promise.resolve({ pages: [] }); },
    allDocs: function () {
      return Promise.resolve([{ idea: untrusted, page: untrusted, title: untrusted, url: location.href, notes: { nmarkup: note } }]);
    },
  };
  W.panel.init();
  W.panel.toggle(true);
  var localPanelText = document.querySelector(".an-panel").textContent;
  W.notes.mode = "server";
  W.panel.render();
  W.render.blocks["feedback-summary"](document.getElementById("fixture-summary"));
  Array.from(document.querySelectorAll(".an-actions button")).find(function (button) {
    return button.textContent === "Resolve";
  }).click();
  document.body.appendChild(W.h("div", { html: payload }));
  document.addEventListener("DOMContentLoaded", function () {
    var panel = document.querySelector(".an-panel");
    var allNotes = document.getElementById("all-notes");
    var toast = document.querySelector(".toast__body");
    var passed = localPanelText.includes(untrusted) &&
      panel.textContent.includes(untrusted) && panel.textContent.includes(message) &&
      allNotes.textContent.includes(untrusted) &&
      document.getElementById("fixture-summary").textContent.includes("shared review server") &&
      toast && toast.textContent.includes(message) &&
      !document.querySelector("img") && !window.mockupXssExecuted;
    document.getElementById("xss-result").textContent = passed
      ? "PASS: panel, All notes and error toast render markup inert; no images or execution"
      : "FAIL: renderer interpreted markup or did not display fixture text";
    document.body.dataset.xssResult = passed ? "passed" : "failed";
  });
})();
