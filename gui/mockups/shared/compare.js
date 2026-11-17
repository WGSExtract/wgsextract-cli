/* compare.html: one screen in two versions of the design, side by side.
 * State lives in the URL (?screen=home&a=v1&b=v2&size=desktop&theme=light)
 * so a comparison can be linked. Each pane is the real page in a frame,
 * loaded with embed=1 so it has no review bar of its own. */
(function () {
  "use strict";
  var versions = window.WGSM.versions;
  var params = new URLSearchParams(location.search);
  var last = versions.list[versions.list.length - 1];
  var state = {
    screen: params.get("screen") || "home",
    a: params.get("a") || (versions.list[versions.list.length - 2] || last).id,
    b: params.get("b") || last.id,
    size: params.get("size") || "desktop",
    theme: params.get("theme") || "light",
  };
  var panes = document.getElementById("cmp-panes");

  function option(value, label, selected) {
    var el = document.createElement("option");
    el.value = value;
    el.textContent = label;
    el.selected = selected;
    return el;
  }

  function pane(id) {
    var v = versions.get(id);
    var path = versions.path(id, state.screen);
    var box = document.createElement("section");
    box.className = "mk-compare__pane";
    var label = document.createElement("div");
    label.className = "mk-compare__label";
    var name = document.createElement("strong");
    name.textContent = v.id + " · " + v.name;
    label.append(name, " " + v.date);
    box.appendChild(label);
    if (!path) {
      var none = document.createElement("div");
      none.className = "mk-compare__label";
      none.textContent = "“" + versions.label(state.screen) + "” is not in " + v.id + ".";
      box.appendChild(none);
      return box;
    }
    var open = document.createElement("a");
    open.href = path;
    open.textContent = "Open";
    label.appendChild(open);
    var frame = document.createElement("iframe");
    frame.title = v.id + ": " + versions.label(state.screen);
    frame.src = path + (path.indexOf("?") < 0 ? "?" : "&") + "embed=1&vp=fit&theme=" + state.theme;
    box.appendChild(frame);
    return box;
  }

  function render() {
    panes.dataset.size = state.size;
    panes.replaceChildren(pane(state.a), pane(state.b));
    ["cmp-size", "cmp-theme"].forEach(function (id) {
      var key = id.slice(4);
      document.querySelectorAll("#" + id + " button").forEach(function (b) {
        b.setAttribute("aria-pressed", String(b.value === state[key]));
      });
    });
    history.replaceState(null, "", "?" + new URLSearchParams(state));
  }

  function bind(id, key, options) {
    var el = document.getElementById(id);
    options.forEach(function (o) {
      el.appendChild(option(o[0], o[1], o[0] === state[key]));
    });
    el.addEventListener("change", function () {
      state[key] = el.value;
      render();
    });
  }

  var inAny = versions.screens.filter(function (s) {
    return versions.list.some(function (v) {
      return v.screens[s[0]];
    });
  });
  var names = versions.list.map(function (v) {
    return [v.id, v.id + " · " + v.name];
  });
  bind("cmp-screen", "screen", inAny);
  bind("cmp-a", "a", names);
  bind("cmp-b", "b", names);
  document.querySelector(".mk-bar").addEventListener("click", function (event) {
    var button = event.target.closest(".mk-seg button");
    if (!button) return;
    state[button.parentElement.id.slice(4)] = button.value;
    render();
  });
  render();
})();
