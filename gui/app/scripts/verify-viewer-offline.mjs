import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { cp, mkdir, readdir, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import { parseArgs } from "node:util";
import { loadLocalizedBundle } from "../dist/web/src/server/bundle-loader.js";
import { createProcessManager } from "../dist/web/src/server/process-runner.js";
import { createRequestHandler } from "../dist/web/src/server/routes.js";
import { registerUsersWorkspace } from "../dist/web/src/server/users-workspace.js";
import { prepareViewerFixtures } from "./prepare-viewer-fixtures.mjs";
import { cytobandChromosomeLengths } from "../dist/shared/viewer-cytobands.js";

const root = path.resolve(import.meta.dirname, "../../..");
const { values } = parseArgs({ options: {
    out: { type: "string" }, "cli-root": { type: "string", default: root },
    chromium: { type: "string" },
    "read-only": { type: "boolean", default: false },
    genes: { type: "boolean", default: false },
    cytobands: { type: "boolean", default: false },
    memory: { type: "boolean", default: false },
} });
if (!values.out) throw new Error("Usage: node scripts/verify-viewer-offline.mjs --out REPO/out/verification [--cli-root PATH] [--chromium PATH]");
const output = path.resolve(values.out);
assert.ok(output.startsWith(`${path.join(root, "out")}${path.sep}`), "Evidence and fixtures must stay under this checkout's out/.");
await mkdir(output, { recursive: true });
const cli = path.resolve(values["cli-root"]);
const fixtures = await prepareViewerFixtures(root, output, path.join(cli, ".pixi/envs/default", process.platform === "win32" ? "Library/bin" : "bin"), values.genes || values.cytobands, values.memory);
if (values.cytobands) {
    await writeFile(path.join(output, "reference/ref/cytobands_hg38.tsv"),
        "chr1\t0\t121700000\tp11.1\tgneg\nchr1\t121700000\t123400000\tp11.2\tacen\n"
        + "chr1\t123400000\t125100000\tq11.1\tacen\n"
        + [...cytobandChromosomeLengths("hg38")].filter(([chr]) => chr !== "chr1")
            .map(([chr, size]) => `${chr}\t0\t${size}\tp11\tgneg\n`).join("")
        + "chr1\t125100000\t248956422\tq44\tgpos100\n");
}
const originalFiles = values["read-only"] ? (await readdir(fixtures.library, { recursive: true })).sort() : [];
const bundleRoot = path.join(output, "bundle");
await cp(path.join(root, "gui/examples/WGSExtract"), bundleRoot, { recursive: true });
const users = path.join(output, "users");
await mkdir(users, { recursive: true });
registerUsersWorkspace(bundleRoot, users);
const config = path.join(output, "config.toml");
await writeFile(config, `mygenomes_library = ${JSON.stringify(fixtures.library)}\nreference_library = ${JSON.stringify(path.dirname(fixtures.reference))}\nreference_genome_library = ${JSON.stringify(fixtures.reference)}\n`);
await writeFile(path.join(bundleRoot, "state.json"), JSON.stringify({
    configFilePaths: { wgs_settings: config }, fieldValues: { bam_path: fixtures.cram },
    uiMode: values["read-only"] ? "basic" : "advanced", setupExpanded: false, setupRun: { status: "succeeded", results: [] },
}));
const manager = createProcessManager({ maxOutputBytes: 1048576, maxErrorBytes: 65536 });
const load = () => loadLocalizedBundle("en", path.join(root, "gui"), bundleRoot, bundleRoot);
const loaded = await load();
const handler = createRequestHandler({
    ...loaded, apiToken: "offline-verification-only", bundleRoot, repoRoot: path.join(root, "gui"),
    distRoot: path.join(root, "gui/app/dist"), webRoot: path.join(root, "gui/app/web"),
    maxBodyBytes: 1048576, dynamicActions: new Map(),
    runProcess: (script, args, options) => manager.runProcess(script, args, {
        ...options, env: { ...options.env, WGSEXTRACT_APP_DIR: cli },
    }),
    localizedBundleLoader: { load }, enableDevReload: false, addDevReloadClient() {},
});
const server = createServer(handler);
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let child;
let cancelled = false;
const cancel = () => {
    cancelled = true;
    child?.kill("SIGTERM");
};
process.on("SIGTERM", cancel);
process.on("SIGINT", cancel);
try {
    child = spawn(process.execPath, [
        path.join(import.meta.dirname, values.memory ? "verify-viewer-memory.mjs" : "verify-viewer-library.mjs"),
        "--url", `http://127.0.0.1:${server.address().port}`, "--fixtures", fixtures.library,
        "--large-bam", fixtures.large, "--vcf", fixtures.vcf, "--out", output,
        ...(!values["read-only"] ? ["--config", config] : []),
        "--reference-name", "reference.fa", "--locus", "1:200-400", "--next-locus", "1:240-300",
        ...(!values.genes && !values.cytobands ? ["--tracks", "--track-regressions"] : []),
        ...(values.genes ? ["--gene-map", path.join(output, "reference/ref/genes_hg38.tsv")] : []),
        ...(values.cytobands ? ["--cytobands", path.join(output, "reference/ref/cytobands_hg38.tsv")] : []),
        ...(values.chromium ? ["--chromium", values.chromium] : []),
    ], { stdio: "inherit", windowsHide: true });
    const code = await new Promise((resolve, reject) => {
        child.once("error", reject);
        child.once("close", resolve);
    });
    if (cancelled) throw new Error("Offline verification cancelled.");
    assert.equal(code, 0, "Offline real-browser verification must pass.");
    if (values["read-only"]) {
        assert.deepEqual((await readdir(fixtures.library, { recursive: true })).sort(), originalFiles, "Read-only verification must not create extraction outputs.");
    }
} finally {
    process.off("SIGTERM", cancel);
    process.off("SIGINT", cancel);
    await manager.terminateAllProcesses();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
}
