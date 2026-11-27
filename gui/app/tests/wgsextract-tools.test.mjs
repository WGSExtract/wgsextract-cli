import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { disabledReason, hydrateRows, isActionVisible, rowContext, rowMatchesFilter } from "../dist/shared/rendering.js";
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
        environment: null, environment_tools: [], can_install: false, can_uninstall: false, preinstalled: false, note: null, ...fields,
    };
}

const depsList = {
    schema: "wgsextract.result/1",
    details: {
        tools: [
            tool({ name: "samtools", group: "mandatory", status: "installed", path: "/env/bin/samtools", runtime: "pixi", version: "samtools 1.24" }),
            tool({ name: "fastp", status: "installed", path: "/env/bin/fastp", runtime: "pixi", version: "fastp 1.4.0", preinstalled: true }),
            tool({ name: "gatk", environment: "gatk", environment_tools: ["gatk"], can_install: true }),
            tool({
                name: "sniffles", status: "installed", path: "pixi run -e pacbio sniffles", runtime: "pixi", alt_env: "pacbio",
                version: "Sniffles2 2.6", environment: "pacbio", environment_tools: ["pbmm2", "pbsv", "sniffles"], can_uninstall: true,
            }),
            tool({ name: "plink2", note: "Download PLINK 2 for your system." }),
            tool({ name: "sambamba", status: "unavailable", note: "sambamba isn't available on Windows: only Linux uses it." }),
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
        sambamba: [["tool-unavailable", "sambamba isn't available on Windows: only Linux uses it."]],
    });
});

test("each Tools row shows its status, whether it is mandatory, and where it comes from", () => {
    const rows = Object.fromEntries(items(depsList).items.map((row) => [row.id, row]));

    assert.equal(rows.samtools.status, "installed");
    assert.deepEqual(rows.samtools.tags.map((tag) => tag.id), ["mandatory"]);
    // Shipped with the CLI and not removable on its own.
    assert.deepEqual(rows.fastp.tags.map((tag) => tag.title), ["pre-installed"]);
    assert.deepEqual(rows.gatk.tags.map((tag) => tag.title), ["optional"]);
    assert.equal(rows.samtools.values.details, "samtools 1.24");
    assert.equal(rows.fastp.values.environment, "default");
    assert.equal(rows.sambamba.values.details, "sambamba isn't available on Windows: only Linux uses it.");
    // The uninstall confirmation names every tool the environment takes with it.
    assert.equal(rows.sniffles.values.environmentTools, "pbmm2, pbsv, sniffles");
    for (const row of Object.values(rows)) {
        assert.match(row.tooltip, /Source: https?:\/\//, `${row.id} tooltip missing Source URL`);
        assert.match(row.tooltip, /Organization: .+/, `${row.id} tooltip missing Organization`);
        assert.match(row.tooltip, /License: .+/, `${row.id} tooltip missing License`);
    }
    assert.match(rows.samtools.tooltip, /Installed path: \/env\/bin\/samtools/);
    // The note already shows in the row's details column, so the tooltip leaves it out.
    assert.doesNotMatch(rows.plink2.tooltip, /Note:/);
    assert.equal(rows.plink2.values.details, "Download PLINK 2 for your system.");
});

test("Tools rows list installed tools first (mandatory, pre-installed, the rest), then missing, then unavailable", () => {
    const order = items({ details: { tools: [
        tool({ name: "sambamba", status: "unavailable" }),
        tool({ name: "plink2" }),
        tool({ name: "Gatk", status: "installed", path: "pixi run -e gatk gatk", environment: "gatk" }),
        tool({ name: "minimap2", status: "installed", path: "/env/bin/minimap2", preinstalled: true }),
        tool({ name: "bcftools", group: "mandatory", status: "installed", path: "/env/bin/bcftools" }),
        tool({ name: "delly" }),
        tool({ name: "abc", status: "installed", path: "pixi run -e abc abc", environment: "abc" }),
        tool({ name: "bwa-mem3", status: "unavailable" }),
    ] } }).items.map((row) => row.id);

    assert.deepEqual(order, ["bcftools", "minimap2", "abc", "Gatk", "delly", "plink2", "bwa-mem3", "sambamba"]);
});

test("a table's keyword filter keeps rows holding every typed word, in any case", () => {
    const text = "java17 openjdk 17.0.21 optional installed";
    assert.ok(rowMatchesFilter(text, ""));
    assert.ok(rowMatchesFilter(text, "  OpenJDK   installed "));
    assert.ok(!rowMatchesFilter(text, "openjdk missing"));
});

test("Tools rows carry the commands that use each tool, and the page hides mandatory and pre-installed by default", () => {
    const rows = items({ details: { tools: [tool({ name: "discvrseq", used_by: ["vcf variantqc"] }), tool({ name: "tar" })] } }).items;
    assert.deepEqual(rows.map((row) => row.values.usedBy), ["vcf variantqc", ""]);
    assert.equal(control.rowCommandsValue, "usedBy");
    assert.equal(control.rowTemplate.values.usedBy, "{{usedBy}}");
    assert.deepEqual(control.hideRows.tags, ["mandatory", "pre-installed"]);
    assert.equal(control.hideRows.checked, true);
});

test("the Tools page shows the computer the CLI runs on, from deps list's host", () => {
    const host = {
        os: "Windows 11 (10.0.26300)", architecture: "x64", cpu_threads: 16, cpu_cores: 8,
        memory_total: 64 * 1024 ** 3, memory_available: 40 * 1024 ** 3,
        disks: [{ used_for: ["workspace", "temporary files"], free: 2.5 * 1024 ** 4, total: 4 * 1024 ** 4 }],
    };
    assert.deepEqual(items({ details: { tools: [], host } }).facts, [
        { label: "Operating system", value: "Windows 11 (10.0.26300)" },
        { label: "Architecture", value: "x64" },
        { label: "CPU", value: "8 cores, 16 threads" },
        { label: "Memory", value: "64.0 GiB total, 40.0 GiB available" },
        { label: "Free disk", value: "2.50 TiB of 4.00 TiB (workspace, temporary files)" },
    ]);
    assert.deepEqual(items({ details: { tools: [] } }).facts, []);
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

test("the published page schema allows every key the Tools page's data source uses", () => {
    const schema = JSON.parse(readFileSync(path.join(bundleRoot, "..", "..", "devdocs", "schema", "page.schema.json"), "utf8"));
    const allowed = Object.keys(schema.$defs.dataSource.properties);

    assert.deepEqual(Object.keys(control.dataSource).filter((key) => !allowed.includes(key)), []);
    assert.deepEqual(schema.$defs.dataSource.properties.timeoutSeconds, {
        type: "number", exclusiveMinimum: 0, description: schema.$defs.dataSource.properties.timeoutSeconds.description,
    });
});
