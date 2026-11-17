import assert from "node:assert/strict";
import test from "node:test";

test("1087 folder move focuses its dialog, traps Tab, cancels with Escape and restores the opener", async () => {
  const keys = ["document", "HTMLElement", "HTMLInputElement", "HTMLTextAreaElement", "CSS", "localStorage", "window"];
  const previous = Object.fromEntries(keys.map((key) => [key, globalThis[key]]));
  class Element {
    tagName = "BUTTON";
    attributes = [];
    listeners = {};
    children = [];
    focus() { globalThis.document.activeElement = this; }
    contains(element) { return element === this || this.children.includes(element); }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    querySelector() { return this.children[0] ?? null; }
    querySelectorAll() { return this.children; }
  }
  try {
    globalThis.HTMLElement = Element;
    globalThis.HTMLInputElement = class extends Element {};
    globalThis.HTMLTextAreaElement = class extends Element {};
    globalThis.CSS = { escape: (text) => text };
    globalThis.localStorage = { getItem() { return null; }, setItem() {} };
    globalThis.window = { innerHeight: 900 };
    const opener = new globalThis.HTMLInputElement();
    opener.tagName = "INPUT";
    opener.type = "text";
    opener.value = "/requested";
    opener.attributes = [{ name: "data-field-id", value: "library" }];
    const [move, settingOnly, cancel] = [new Element(), new Element(), new Element()];
    const dialog = new Element();
    dialog.children = [move, settingOnly, cancel];
    let visible = true;
    const app = new Element();
    app.children = [opener, dialog, move, settingOnly, cancel];
    app.querySelector = (selector) => selector === "[data-setting-move-dialog]"
      ? visible ? dialog : null
      : selector === 'input[data-field-id="library"]' ? opener : null;
    app.querySelectorAll = () => [];
    globalThis.document = { activeElement: opener };
    const { state, createInitialState } = await import("../dist/web/src/client/state.js");
    const { bindWorkspaceLocationEvents } = await import("../dist/web/src/client/workspace-location.js");
    Object.assign(state, createInitialState());
    state.workspaceMove = { folder: "/new", setting: { controlID: "settings", key: "library", from: "/old" }, running: false, percent: 0 };
    bindWorkspaceLocationEvents(app);
    assert.equal(globalThis.document.activeElement, move);
    cancel.focus();
    dialog.listeners.keydown({ key: "Tab", shiftKey: false, preventDefault() {} });
    assert.equal(globalThis.document.activeElement, move);
    dialog.listeners.keydown({ key: "Tab", shiftKey: true, preventDefault() {} });
    assert.equal(globalThis.document.activeElement, cancel);
    dialog.listeners.keydown({ key: "Escape", preventDefault() {} });
    assert.equal(state.workspaceMove, null);
    visible = false;
    opener.value = "/original";
    bindWorkspaceLocationEvents(app);
    assert.equal(globalThis.document.activeElement, opener);
    assert.equal(opener.value, "/original", "restoring focus must not restore a cancelled path edit");
  } finally {
    for (const key of keys) {
      if (previous[key] === undefined) delete globalThis[key];
      else globalThis[key] = previous[key];
    }
  }
});
