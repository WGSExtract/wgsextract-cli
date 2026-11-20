import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { disabledReason, hydrateRows, isActionVisible, rowContext } from "../dist/shared/rendering.js";
import { runDataSource } from "../dist/web/src/server/action-runner.js";
import { validateDataSource } from "../dist/web/src/server/manifest-validation.js";

// The WGSExtract bundle's Tools page: `wgsextract --json deps list` mapped to a table
// whose rows offer Install, Uninstall, or "Not available" in their place.
const { items } = await import("../../examples/WGSExtract/transforms/tools.mjs");

const bundleRoot = path.resolve(fileURLToPath(new URL("../../examples/WGSExtract", import.meta.url)));
const page = JSON.parse(readFileSync(path.join(bundleRoot, "pages", "tools.json"), "utf8"));
const control = page.sections[0].controls[0];

function tool(fields) {
    return {
        path: null, runtime: null, version: null, alt_env: null, group: "optional", status: "missing",
        environment: null, environment_tools: [], can_install: false, can_uninstall: false, note: null, ...fields,
    };
}

const depsList = {
    schema: "wgsextract.result/1",
    details: {
        tools: [
            tool({ name: "samtools", group: "mandatory", status: "installed", path: "/env/bin/samtools", runtime: "pixi", version: "samtools 1.24" }),
            tool({ name: "fastp", status: "installed", path: "/env/bin/fastp", runtime: "pixi", version: "fastp 1.4.0" }),
            tool({ name: "gatk", environment: "gatk", environment_tools: ["gatk"], can_install: true }),
            tool({
                name: "sniffles", status: "installed", path: "pixi run -e pacbio sniffles", runtime: "pixi", alt_env: "pacbio",
                version: "Sniffles2 2.6", environment: "pacbio", environment_tools: ["pbmm2", "pbsv", "sniffles"], can_uninstall: true,
            }),
            tool({ name: "plink2", note: "Download PLINK 2 for your system." }),
            tool({ name: "sambamba", status: "unavailable", note: "sambamba isn't built for win32 AMD64." }),
        ],
    },
};

/** Each row's offered row actions, as [action id, why it's disabled or ""]. */
function offered() {
    const rows = hydrateRows({ ...control, items: items(depsList).items });
    return Object.fromEntries(rows.map((row) => {
        const context = rowContext({}, row);
        const actions = control.rowActions
            .filter((action) => isActionVisible(action, context))
            .map((action) => [action.id, disabledReason(action, context) ?? ""]);
        return [row.id, actions];
    }));
}

test("the Tools table offers Install, Uninstall or Not available as the CLI says", () => {
    assert.deepEqual(offered(), {
        samtools: [],
        fastp: [],
        gatk: [["tool-install", ""]],
        sniffles: [["tool-uninstall", ""]],
        plink2: [["tool-manual", "Download PLINK 2 for your system."]],
        sambamba: [["tool-unavailable", "sambamba isn't built for win32 AMD64."]],
    });
});

test("each Tools row shows its status, whether it is mandatory, and where it comes from", () => {
    const rows = Object.fromEntries(items(depsList).items.map((row) => [row.id, row]));

    assert.equal(rows.samtools.status, "installed");
    assert.deepEqual(rows.samtools.tags.map((tag) => tag.id), ["mandatory"]);
    assert.deepEqual(rows.fastp.tags.map((tag) => tag.id), ["optional"]);
    assert.equal(rows.samtools.values.details, "samtools 1.24");
    assert.equal(rows.fastp.values.environment, "default");
    assert.equal(rows.sambamba.values.details, "sambamba isn't built for win32 AMD64.");
    // The uninstall confirmation names every tool the environment takes with it.
    assert.equal(rows.sniffles.values.environmentTools, "pbmm2, pbsv, sniffles");
});

test("the Tools table needs deps list's tool rows", () => {
    assert.throws(() => items({ details: {} }), /no tool list/);
});

test("a data source runs within its declared timeoutSeconds, else 15 seconds", async () => {
    const timeouts = [];
    const runProcess = async (_executable, _args, options) => {
        timeouts.push(options.timeoutMs);
        return { exitCode: 0, stdout: JSON.stringify(depsList), stderr: "" };
    };

    await runDataSource(control.dataSource, {}, bundleRoot, runProcess);
    await runDataSource({ ...control.dataSource, timeoutSeconds: undefined }, {}, bundleRoot, runProcess);

    assert.deepEqual(timeouts, [300_000, 15_000]);
});

test("a bundle's data source timeoutSeconds must be a positive number", () => {
    for (const timeoutSeconds of [0, -5, "300"]) {
        assert.throws(() => validateDataSource({ path: "scripts/list.sh", timeoutSeconds }, "dataSource"), /timeoutSeconds/);
    }
    assert.doesNotThrow(() => validateDataSource({ path: "scripts/list.sh", timeoutSeconds: 300 }, "dataSource"));
});
