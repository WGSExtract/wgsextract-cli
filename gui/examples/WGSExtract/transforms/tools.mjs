// Maps `wgsextract --json deps list` (every mandatory and optional tool, with
// its status and what `deps install` / `deps uninstall` can do about it) to the
// Tools page's table. The CLI decides what is installed, installable or
// unavailable on this computer; only how the app shows it is here.
//
// `values.action` picks the row's button in pages/tools.json: Install,
// Uninstall, or a disabled "Install it yourself" / "Not available" in their
// place, whose tooltip is the CLI's note. `deps list` probes every tool's
// version, so the page gives its data source 300 seconds (half a minute on
// Windows, more on a first start).

/** One row per tool: mandatory tools first, then the optional ones, those this computer can't have last. */
export function items(document) {
    const tools = document?.details?.tools;
    if (!Array.isArray(tools)) {
        throw new Error("wgsextract deps list printed no tool list.");
    }
    return { items: tools.map(row) };
}

function row(tool) {
    const installed = tool.status === "installed";
    return {
        id: tool.name,
        title: tool.name,
        status: tool.status,
        tags: [{ id: tool.group, title: tool.group, style: tool.group === "mandatory" ? "primary" : "secondary" }],
        tooltip: tool.path ?? tool.note ?? "",
        values: {
            name: tool.name,
            details: installed ? tool.version ?? "" : tool.note ?? "",
            environment: source(tool),
            environmentTools: (tool.environment_tools ?? []).join(", "),
            action: action(tool),
            note: tool.note ?? "",
        },
    };
}

/** Which button the row offers: install, uninstall, manual (install it yourself), unavailable, or none. */
function action(tool) {
    if (tool.can_uninstall) {
        return "uninstall";
    }
    if (tool.can_install) {
        return "install";
    }
    if (tool.status === "unavailable") {
        return "unavailable";
    }
    if (tool.status === "missing" && tool.group === "optional" && tool.note) {
        return "manual";
    }
    return "";
}

/** The Pixi environment a tool comes from (its own, or the CLI's "default"), else where it was found. */
function source(tool) {
    if (tool.environment) {
        return tool.environment;
    }
    if (tool.status !== "installed") {
        return "";
    }
    return tool.runtime === "pixi" ? "default" : tool.runtime ?? "";
}
