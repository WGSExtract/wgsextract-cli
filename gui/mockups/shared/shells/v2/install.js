/* Desktop tool inventory. Install and Uninstall are local CLI actions in
 * the desktop app; the preview only changes its mock machine's state. */
(function () {
  "use strict";
  var W = window.WGSM;
  var h = W.h;

  W.render.blocks["v2-install"] = function (el) {
    var machine = W.v2machine.machine;
    var api = W.v2api;
    var output = h("div.v2-stack", { "aria-live": "polite" });
    el.appendChild(output);
    if (machine.os === "windows") {
      el.appendChild(h("section.v2-section", h("h2", "When Windows blocks an installed tool"),
        h("p", "Smart App Control or App Control for Business can block a tool even when Pixi installed it. A full check starts each tool; checking only one by name just locates it."),
        h("pre.v2-code", "pixi run python -m wgsextract_cli deps check"),
        h("p.v2-small.v2-muted", "Use this command if the Windows launcher is blocked. Use an approved signed build or ask your administrator; no policy changes are needed.")));
    }

    async function changeFlag(set, unset) {
      var response = await api.post("/v1/mock/flags", { set: set, unset: unset });
      if (response.status !== 200) throw new Error("Couldn’t update the mock machine.");
      await api.refresh();
      await refresh();
    }

    function action(tool) {
      if (tool.name === "wgse-bwa") {
        var missing = window.WGSMMock.machineSpec.windowsBwaMissingFlag(machine);
        var found = tool.status === "installed";
        return h("button.v2-btn.v2-btn--sm", { type: "button", onclick: function () {
          changeFlag(found ? [missing] : [], found ? [] : [missing]).catch(failed);
        } }, found ? "Simulate missing build" : "Restore bundled build");
      }
      if (!(tool.can_install || tool.can_uninstall)) return tool.status === "unavailable" ? "Not available" : tool.note ? "Install it yourself" : "";
      var remove = tool.can_uninstall;
      var key = "tool-" + machine.key + "-" + tool.environment;
      return h("button.v2-btn.v2-btn--sm", { type: "button", onclick: function () {
        if (remove && !window.confirm("Remove the " + tool.environment + " environment? This removes " + tool.environment_tools.join(", ") + ".")) return;
        changeFlag([key + (remove ? "-removed" : "-installed")], [key + (remove ? "-installed" : "-removed")]).catch(failed);
      } }, remove ? "Uninstall" : "Install");
    }

    function render(tools) {
      output.replaceChildren(h("section.v2-section",
        h("h2", "Mandatory and optional tools"),
        h("p.v2-small.v2-muted", "Mock inventory. Install and Uninstall only change the preview; they never download or remove anything."),
        h("div.v2-sheet.v2-scroll", h("table.v2-table", { "aria-label": "Tools on this computer" },
          h("thead", h("tr", ["Tool", "Status", "Version or note", "Pixi environment", "Action"].map(function (label) { return h("th", label); }))),
          h("tbody", tools.map(function (tool) {
            return h("tr", h("td.v2-table__main", tool.name + (tool.group === "mandatory" ? " · mandatory" : "")),
              h("td", tool.status), h("td", tool.version || tool.note || ""),
              h("td", tool.environment || (tool.group === "mandatory" ? "default" : "")), h("td", action(tool)));
          })))),
        h("code.v2-cmd.v2-dim.v2-small", "deps list")));
    }

    function failed(error) {
      output.textContent = error.message;
    }

    async function refresh() {
      output.textContent = "Checking tools…";
      var res = await api.post("/v1/ops/deps/list?wait=2", {});
      var result = res.body && res.body.result;
      if (res.status !== 200 || !result || !result.details) throw new Error("Couldn’t read the tool inventory.");
      render(result.details.tools);
    }

    document.getElementById("v2-tools-refresh").onclick = function () { refresh().catch(failed); };
    refresh().catch(failed);
  };
})();
