import assert from "node:assert/strict";
import { copyFile, mkdir, mkdtemp, open as openFile, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import test from "node:test";
import { gunzipSync, gzipSync } from "node:zlib";
import { createRequestHandler } from "../dist/web/src/server/routes.js";
import { parseConfigToml } from "../dist/web/src/server/config-toml.js";
import { registerUsersWorkspace } from "../dist/web/src/server/users-workspace.js";
import { validatedBedGraphTrack } from "../dist/shared/viewer-track-records.js";
import { viewerGeneLookup, viewerNavigationLocus } from "../dist/shared/viewer-genes.js";
import { cytobandChromosomeLengths } from "../dist/shared/viewer-cytobands.js";
import { shellQuote } from "../dist/shared/rendering.js";
import { installedViewerCytobands } from "../dist/web/src/server/viewer-cytobands.js";

async function fixture(t) {
    const output = path.resolve("../out/tests");
    await mkdir(output, { recursive: true });
    const root = await mkdtemp(path.join(output, "viewer-session-"));
    t.after(() => rm(root, { force: true, recursive: true }));
    const users = path.join(root, "users");
    const library = path.join(users, "mygenomes");
    const reference = path.join(root, "reference");
    await mkdir(library, { recursive: true });
    await mkdir(reference);
    await mkdir(path.join(root, "scripts"));
    await writeFile(path.join(root, "scripts", "viewer-reference.sh"), "");
    registerUsersWorkspace(root, users);
    const input = path.join(library, "sample.bam");
    const fasta = path.join(reference, "reference.fa");
    await writeFile(input, "reads");
    await writeFile(`${input}.bai`, "index");
    await writeFile(fasta, ">chr1\nACGT\n");
    await writeFile(`${fasta}.fai`, "chr1\t4\t6\t4\t5\n");
    const config = path.join(root, "selected.toml");
    await writeFile(config, `mygenomes_library = ${JSON.stringify(library)}\nreference_library = ${JSON.stringify(reference)}\n`);
    await writeFile(path.join(users, "config.toml"), `mygenomes_library = ${JSON.stringify(path.join(root, "wrong-library"))}\n`);
    await writeFile(path.join(root, "state.json"), JSON.stringify({ configFilePaths: { wgs_settings: config } }));
    const context = {
        bundleRoot: root,
        apiToken: "viewer-test",
        manifest: {
            id: "wgs-extract",
            pages: [{ id: "settings", sections: [{ id: "settings", controls: [{
                id: "wgs_settings", kind: "configEditor", configFile: { path: "{{usersWorkspace}}/config.toml" },
            }] }] }],
        },
        maxBodyBytes: 1048576,
        // The resolver subprocess is a tool boundary. Its result depends on the settings
        // it actually receives, not on a pre-recorded invocation.
        async runProcess(_script, [file, settings]) {
            assert.equal(await readFile(file, "utf8"), "reads");
            const values = parseConfigToml(await readFile(settings, "utf8"));
            const result = values.reference_library
                ? { fasta: path.join(values.reference_library, "reference.fa"), locus: "chr1:1-4" }
                : { problem: "referenceMissing" };
            return { exitCode: 0, stdout: JSON.stringify(result), stderr: "" };
        },
    };
    const server = createServer(createRequestHandler(context));
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    t.after(async () => {
        server.closeAllConnections();
        await new Promise((resolve) => server.close(resolve));
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    const open = (file = input, locus = "", tracks = []) => fetch(`${base}/api/viewer/open`, {
        method: "POST", headers: { "x-gfc-token": "viewer-test", "content-type": "application/json" },
        body: JSON.stringify({ path: file, locus, tracks }),
    });
    return { root, users, config, library, input, fasta, base, open };
}

test("#1090: opening and revoking viewer sessions require the app credential, not a byte grant", async (t) => {
    const f = await fixture(t);
    for (const route of ["/api/viewer/open", "/api/viewer/close"]) {
        for (const headers of [{}, { "x-gfc-token": "wrong" }, { "x-gfc-viewer-token": "wrong" }]) {
            const response = await fetch(`${f.base}${route}`, {
                method: "POST", headers: { "content-type": "application/json", ...headers },
                body: JSON.stringify({ path: f.input, id: "unknown" }),
            });
            assert.equal(response.status, 401);
        }
    }
});

test("#1112: selected settings TOML controls roots, resolver and existing grant validity", async (t) => {
    const f = await fixture(t);
    const response = await f.open();
    assert.equal(response.status, 200);
    const session = await response.json();
    assert.equal(session.files.tracks[0].name, "sample.bam");
    const track = session.files.tracks[0];
    assert.equal(await (await fetch(`${f.base}${track.url}`, { headers: track.headers })).text(), "reads");
    const otherConfig = path.join(f.root, "other.toml");
    await writeFile(otherConfig, await readFile(f.config, "utf8"));
    await writeFile(path.join(f.root, "state.json"), JSON.stringify({ configFilePaths: { wgs_settings: otherConfig } }));
    assert.equal((await fetch(`${f.base}${track.url}`, { headers: track.headers })).status, 401);
    await writeFile(path.join(f.root, "state.json"), JSON.stringify({ configFilePaths: { wgs_settings: path.join(f.root, "settings.sh") } }));
    assert.equal((await f.open()).status, 400);
});

test("#1112: workspace-token paths open indexed files but never normalize traversal", async (t) => {
    const f = await fixture(t);
    for (const file of ["{{usersWorkspace}}/mygenomes/sample.bam", "{{bundleWorkspace}}/users/mygenomes/sample.bam"]) {
        const response = await f.open(file);
        assert.equal(response.status, 200);
        const session = await response.json();
        assert.equal(session.files.tracks[0].name, "sample.bam");
        const track = session.files.tracks[0];
        assert.equal(await (await fetch(`${f.base}${track.url}`, { headers: track.headers })).text(), "reads");
        assert.equal((await fetch(`${f.base}${track.indexURL}`, { headers: track.headers })).status, 200);
    }
    for (const file of ["{{usersWorkspace}}/mygenomes/../mygenomes/sample.bam", "{{bundleWorkspace}}/users/../users/mygenomes/sample.bam",
        "{{usersWorkspace}}\\mygenomes\\..\\mygenomes\\sample.bam"]) {
        assert.equal((await f.open(file)).status, 403);
    }
});

test("#1112: invalid viewer positions return actionable invalidLocus, not a server error", async (t) => {
    const f = await fixture(t);
    for (const locus of ["chr1:0-100", "chr1:2-1", "rs123", "chr1:1-9007199254740992"]) {
        const response = await f.open(f.input, locus);
        assert.equal(response.status, 200);
        assert.deepEqual(await response.json(), { problem: "invalidLocus" });
    }
    const response = await f.open(f.input, "chr1:1-4");
    assert.equal((await response.json()).locus, "chr1:1-4");
});

async function geneFixture(t, contig = "chr1", length = 248956422) {
    const f = await fixture(t);
    await writeFile(`${f.fasta}.fai`, `${contig}\t${length}\t6\t4\t5\n`);
    const directory = path.join(path.dirname(f.fasta), "ref");
    await mkdir(directory);
    const table = path.join(directory, `genes_${length === 249250621 ? "hg19" : "hg38"}.tsv`);
    return { ...f, table };
}

function cytobandTable(length = 248956422) {
    return `chr1\t0\t100\tp36.33\tgneg\nchr1\t100\t${length}\tq44\tgpos100\n`
        + [...cytobandChromosomeLengths(length === 249250621 ? "hg19" : "hg38")]
            .filter(([chr]) => chr !== "chr1").map(([chr, size]) => `${chr}\t0\t${size}\t${chr === "chrY" ? "q11.221" : "p11"}\tgneg\n`).join("");
}

test("#1148: installed cytobands reach the viewer with reference aliases and zero-based edge coordinates", async (t) => {
    for (const [contig, length, build] of [["chr1", 248956422, "hg38"], ["1", 249250621, "hg19"]]) {
        const f = await geneFixture(t, contig, length);
        const table = path.join(path.dirname(f.table), `cytobands_${build}.tsv`);
        await writeFile(table, cytobandTable(length));
        const session = await (await f.open()).json();
        assert.deepEqual(session.files.cytobands.features, [
            { chr: contig, start: 0, end: 100, name: "p36.33", gieStain: "gneg" },
            { chr: contig, start: 100, end: length, name: "q44", gieStain: "gpos100" },
        ]);
        assert.equal(session.files.cytobands.notices[0].code, "cytobandContigsSkipped");
        assert.equal(session.files.tracks[0].name, "sample.bam");
        await rm(table);
        const missing = await (await f.open()).json();
        assert.deepEqual(missing.files.cytobands, { features: [], notices: [{
            code: "cytobandMissing", fileName: `cytobands_${build}.tsv`,
            command: `wgsextract ref cytobands --build ${build} --ref ${shellQuote(path.dirname(f.fasta))} --download`,
        }] });
        assert.equal(missing.files.tracks[0].name, "sample.bam");
    }
});

test("#1153: cytoband repair names the selected library with shell-safe quoting", async (t) => {
    const f = await geneFixture(t);
    const referenceRoot = path.join(f.root, "a library with 'quotes' and $characters");
    const result = await installedViewerCytobands(f.fasta, referenceRoot);
    const command = result.notices[0].command;
    assert.equal(command, `wgsextract ref cytobands --build hg38 --ref '${referenceRoot.replaceAll("'", "'\\''")}' --download`);
    assert.equal(result.notices[0].code, "cytobandMissing");
});

test("#1148: malformed cytobands and ambiguous reference names disable only the ideogram", async (t) => {
    const f = await geneFixture(t);
    const table = path.join(path.dirname(f.table), "cytobands_hg38.tsv");
    for (const body of [
        cytobandTable().slice(0, -1),
        cytobandTable().replace("gneg", "bad"),
        cytobandTable().replace("chr1\t100", "chr1\t99"),
        cytobandTable().replace("chr1\t100", "chr1\t101"),
        cytobandTable().replace("\t0\t100", "\t0\t0"),
        cytobandTable().replace(/chrY[^\n]+\n/, ""),
        cytobandTable().replace("57227415", "57227414"),
        cytobandTable().replace("p36.33", "<script>"),
        Buffer.from([0xff, 0x0a]),
        "x".repeat(1024 * 1024 + 1),
    ]) {
        await writeFile(table, body);
        const session = await (await f.open()).json();
        assert.equal(session.files.cytobands.notices[0].code, "cytobandInvalid");
        assert.deepEqual(session.files.cytobands.features, []);
        assert.equal(session.files.tracks[0].name, "sample.bam");
    }
    await writeFile(table, cytobandTable());
    await writeFile(`${f.fasta}.fai`, "chr1\t248956422\t6\t4\t5\nchr2\t101\t6\t4\t5\n");
    assert.equal((await (await f.open()).json()).files.cytobands.notices[0].code, "cytobandMismatch");
    await writeFile(`${f.fasta}.fai`, "chr1\t248956422\t6\t4\t5\nchr2\t242193529\t6\t4\t5\n2\t242193529\t6\t4\t5\n");
    let session = await (await f.open()).json();
    assert.equal(session.files.cytobands.notices[0].code, "cytobandContigsSkipped");
    assert.equal(session.files.cytobands.features.filter((band) => band.chr === "chr2" || band.chr === "2").length, 0);
    await writeFile(`${f.fasta}.fai`, "chr1\t3000\t6\t4\t5\n");
    session = await (await f.open()).json();
    assert.equal(session.files.cytobands.notices[0].code, "cytobandBuildUnsupported");
    assert.equal(session.files.tracks[0].name, "sample.bam");
});

test("#1148: cytobands never follow escaping installed-resource symlinks", async (t) => {
    const f = await geneFixture(t);
    const outside = path.join(f.root, "outside-cytobands.tsv");
    await writeFile(outside, cytobandTable());
    try {
        await symlink(outside, path.join(path.dirname(f.table), "cytobands_hg38.tsv"));
    } catch (error) {
        if (["EPERM", "EACCES", "ENOTSUP"].includes(error.code)) return t.skip("Symlinks unavailable.");
        throw error;
    }
    const session = await (await f.open()).json();
    assert.equal(session.files.cytobands.notices[0].code, "cytobandInvalid");
    assert.deepEqual(session.files.cytobands.features, []);
});

test("#1148: installed gene spans navigate case-insensitively with correct inclusive endpoints and reference naming", async (t) => {
    for (const [contig, length] of [["chr1", 248956422], ["1", 249250621]]) {
        const f = await geneFixture(t, contig, length);
        await writeFile(f.table, `symbol\tchrom\tstart\tend\nFIRST\tchr1\t1\t1\nTHRA1/BTR\tchr1\t2\t4\nLAST\tchr1\t${length - 1}\t${length}\n`);
        const session = await (await f.open()).json();
        assert.deepEqual(session.files.genes.notices, []);
        assert.deepEqual(session.files.genes.features, [
            { name: "FIRST", chr: contig, start: 0, end: 1 },
            { name: "THRA1/BTR", chr: contig, start: 1, end: 4 },
            { name: "LAST", chr: contig, start: length - 2, end: length },
        ]);
        const lookup = viewerGeneLookup(session.files.genes.features);
        assert.equal(viewerNavigationLocus(" first ", lookup), `${contig}:1-1`);
        assert.equal(viewerNavigationLocus("thra1/btr", lookup), `${contig}:2-4`);
        assert.equal(viewerNavigationLocus("last", lookup), `${contig}:${length - 1}-${length}`);
        assert.equal(viewerNavigationLocus(`${contig}:1-4`, lookup), `${contig}:1-4`);
        assert.throws(() => viewerNavigationLocus("UNKNOWN", lookup), { code: "geneNotFound" });
        assert.throws(() => viewerNavigationLocus("rs123", lookup), { code: "invalidLocus" });
        // The prebuilt symbol lookup remains usable after the installed table disappears.
        await rm(f.table);
        assert.equal(viewerNavigationLocus("FIRST", lookup), `${contig}:1-1`);
    }
});

test("#1148: missing or unknown-build gene maps do not hide reads or select the wrong assembly", async (t) => {
    const f = await geneFixture(t);
    await writeFile(path.join(path.dirname(f.table), "genes_hg19.tsv"), "symbol\tchrom\tstart\tend\nWRONG\tchr1\t1\t4\n");
    let session = await (await f.open()).json();
    assert.deepEqual(session.files.genes, { features: [], notices: [{ code: "geneMapMissing", fileName: "genes_hg38.tsv" }] });
    assert.equal(session.files.tracks[0].name, "sample.bam");
    assert.throws(() => viewerNavigationLocus("BRCA1", new Map()), { code: "geneSearchUnavailable" });
    await writeFile(`${f.fasta}.fai`, "chr1\t3000\t6\t4\t5\n");
    session = await (await f.open()).json();
    assert.equal(session.files.genes.notices[0].code, "geneBuildUnsupported");
    assert.deepEqual(session.files.genes.features, []);
});

test("#1148: truncated, oversized and malformed gene maps never draw or navigate invalid spans", async (t) => {
    const f = await geneFixture(t);
    for (const body of [
        "symbol\tchrom\tstart\tend\nGENE\tchr1\t1\t4",
        "wrong\theader\nGENE\tchr1\t1\t4\n",
        "symbol\tchrom\tstart\tend\nGENE\tchr1\t0\t4\n",
        "symbol\tchrom\tstart\tend\nGENE\tchr1\t4\t3\n",
        "symbol\tchrom\tstart\tend\nGENE\tchr1\t1.5\t4\n",
        "symbol\tchrom\tstart\tend\nGENE\tchr1\t1\t9007199254740992\n",
        "symbol\tchrom\tstart\tend\nGENE\tchr1\t1\t4\nGene\tchr1\t2\t4\n",
        "symbol\tchrom\tstart\tend\nGENE\tchr1\t1\t4\textra\n",
        "symbol\tchrom\tstart\tend\n",
    ]) {
        await writeFile(f.table, body);
        const session = await (await f.open()).json();
        assert.deepEqual(session.files.genes.features, []);
        assert.ok(["geneMapInvalid", "geneMapMismatch"].includes(session.files.genes.notices[0].code));
        assert.equal(session.files.tracks[0].name, "sample.bam");
    }
    await writeFile(f.table, "symbol\tchrom\tstart\tend\nGENE\tchr1\t248956420\t248956423\n");
    assert.equal((await (await f.open()).json()).files.genes.notices[0].code, "geneMapMismatch");
    const handle = await openFile(f.table, "r+");
    try { await handle.truncate(8 * 1024 * 1024 + 1); } finally { await handle.close(); }
    assert.equal((await (await f.open()).json()).files.genes.notices[0].code, "geneMapInvalid");
});

test("#1148: absent or ambiguous gene contigs are reported and excluded from navigation", async (t) => {
    const f = await geneFixture(t);
    await writeFile(f.table, "symbol\tchrom\tstart\tend\nGOOD\tchr1\t1\t4\nALT\tchr1_KI270762v1_alt\t1\t4\n");
    let session = await (await f.open()).json();
    assert.equal(session.files.genes.notices[0].code, "geneContigsSkipped");
    assert.equal(viewerNavigationLocus("GOOD", viewerGeneLookup(session.files.genes.features)), "chr1:1-4");
    assert.throws(() => viewerNavigationLocus("ALT", viewerGeneLookup(session.files.genes.features)), { code: "geneNotFound" });
    await writeFile(`${f.fasta}.fai`, "chr1\t248956422\t6\t4\t5\n1\t248956422\t6\t4\t5\n");
    session = await (await f.open()).json();
    assert.equal(session.files.genes.notices[0].code, "geneBuildUnsupported");
});

test("#1148: gene maps honor the existing microarray location and reject escaping symlinks", async (t) => {
    const f = await geneFixture(t);
    const fallback = path.join(path.dirname(f.fasta), "microarray");
    await mkdir(fallback);
    const body = "symbol\tchrom\tstart\tend\r\nGENE\tchr1\t1\t4\r\n";
    await writeFile(path.join(fallback, "genes_hg38.tsv"), body);
    assert.equal((await (await f.open()).json()).files.genes.features[0].name, "GENE");
    const outside = path.join(f.root, "outside-genes.tsv");
    await writeFile(outside, body);
    try {
        await symlink(outside, f.table);
    } catch (error) {
        if (process.platform === "win32" && error.code === "EPERM") return;
        throw error;
    }
    const session = await (await f.open()).json();
    assert.deepEqual(session.files.genes.features, []);
    assert.equal(session.files.genes.notices[0].code, "geneMapInvalid");
});

const supplementalFormats = ["panels.bed", "coverage.bedgraph", "panels.bed.gz", "coverage.bedgraph.gz",
    "csi-panels.bed.gz", "csi-coverage.bedgraph.gz", "coverage.bw"];

async function trackFixtures(t) {
    const f = await fixture(t);
    const source = path.resolve("../../tests/fixtures/viewer_tracks");
    for (const name of await readdir(source)) await copyFile(path.join(source, name), path.join(f.library, name));
    return f;
}

test("#1148: BED, bedGraph, TBI/CSI and bigWig use revocable scoped byte reads", async (t) => {
    const f = await trackFixtures(t);
    await writeFile(`${f.fasta}.fai`, "1\t3000\t3\t60\t61\n");
    const response = await f.open(f.input, "", supplementalFormats.map((name) => path.join(f.library, name)));
    assert.equal(response.status, 200);
    const session = await response.json();
    assert.equal(session.files.tracks.length, 8);
    for (const track of session.files.tracks.slice(1)) {
        const file = path.join(f.library, track.name);
        const data = await readFile(file);
        const ranged = await fetch(`${f.base}${track.url}`, { headers: { ...track.headers, range: "bytes=0-15" } });
        assert.equal(ranged.status, 206);
        assert.deepEqual(Buffer.from(await ranged.arrayBuffer()), data.subarray(0, 16));
        assert.equal((await fetch(`${f.base}${track.url}`)).status, 401);
        if (track.indexURL) assert.equal((await fetch(`${f.base}${track.indexURL}`, { headers: track.headers })).status, 200);
    }
    await fetch(`${f.base}/api/viewer/close`, {
        method: "POST", headers: { "x-gfc-token": "viewer-test", "content-type": "application/json" },
        body: JSON.stringify({ id: session.id }),
    });
    const track = session.files.tracks[1];
    assert.equal((await fetch(`${f.base}${track.url}`, { headers: track.headers })).status, 401);
});

test("#1148: every supplemental format reports a reference contig mismatch before rendering", async (t) => {
    const f = await trackFixtures(t);
    for (const name of supplementalFormats) {
        const response = await f.open(f.input, "", [path.join(f.library, name)]);
        assert.equal(response.status, 200);
        assert.deepEqual(await response.json(), { problem: "trackContigs", fileName: name }, "chr1 must not silently display tracks from contig 1.");
    }
});

test("#1148: missing tabix, unsupported gzip, corrupt data and oversized text give actionable errors", async (t) => {
    const f = await trackFixtures(t);
    await writeFile(`${f.fasta}.fai`, "1\t3000\t3\t60\t61\n");
    const missing = path.join(f.library, "missing.bed.gz");
    await copyFile(path.join(f.library, "panels.bed.gz"), missing);
    const gzip = path.join(f.library, "gzip.bed.gz");
    await writeFile(gzip, gzipSync("1\t200\t230\tpanel\n"));
    await copyFile(path.join(f.library, "panels.bed.gz.tbi"), `${gzip}.tbi`);
    const invalid = path.join(f.library, "invalid.bedgraph");
    await writeFile(invalid, "1\t200\t230\tnot-a-number\n");
    const large = path.join(f.library, "large.bed");
    const handle = await openFile(large, "w");
    await handle.truncate(32 * 1024 * 1024 + 1);
    await handle.close();
    for (const [file, problem] of [[missing, "trackIndexMissing"], [gzip, "trackCompression"], [invalid, "trackInvalid"], [large, "trackTooLarge"]]) {
        assert.deepEqual(await (await f.open(f.input, "", [file])).json(), { problem, fileName: path.basename(file) });
    }
});

test("#1148: extra tracks cannot widen library roots or smuggle traversal", async (t) => {
    const f = await trackFixtures(t);
    const outside = path.join(f.root, "outside.bed");
    await writeFile(outside, "1\t200\t230\n");
    for (const file of [outside, `${f.library}/unused/../panels.bed`, "https://example.com/panel.bed"]) {
        assert.equal((await f.open(f.input, "", [file])).status, 403);
    }
    for (const tracks of ["panel.bed", [1]]) {
        assert.equal((await f.open(f.input, "", tracks)).status, 400);
    }
});

test("#1150: excess supplemental paths return the localized track-limit problem", async (t) => {
    const f = await fixture(t);
    assert.deepEqual(await (await f.open(f.input, "", Array(17).fill(path.join(f.library, "panels.bed")))).json(), { problem: "trackLimit" });
});

test("#1150: missing supplemental files name the bad line; missing outside paths remain forbidden", async (t) => {
    const f = await fixture(t);
    for (const file of [path.join(f.library, "missing.bed"), path.join(f.input, "mistyped.bed")]) {
        const response = await f.open(f.input, "", [file]);
        assert.equal(response.status, 200);
        assert.deepEqual(await response.json(), { problem: "trackMissing", fileName: path.basename(file) });
    }
    assert.equal((await f.open(f.input, "", [path.join(f.root, "missing.bed")])).status, 403);
});

test("#1150: supplemental type and adjacent tabix recovery differ from manual read/index selection", async (t) => {
    const f = await trackFixtures(t);
    for (const [name, problem] of [["sample.bam", "trackUnsupported"], ["missing.bed.gz", "trackIndexMissing"]]) {
        if (name.startsWith("missing")) await copyFile(path.join(f.library, "panels.bed.gz"), path.join(f.library, name));
        assert.deepEqual(await (await f.open(f.input, "", [path.join(f.library, name)])).json(), { problem, fileName: name });
    }
    const english = await readFile(path.resolve("../resources/BuiltinStrings/strings.en.toml"), "utf8");
    const supported = english.split("\n").find((line) => line.startsWith('"viewer.trackUnsupported"'));
    assert.match(supported, /BED.*bedGraph.*bigWig/);
    const index = english.split("\n").find((line) => line.startsWith('"viewer.trackIndexMissing"'));
    assert.match(index, /adjacent.*\.tbi.*\.csi/);
    assert.match(index, /tabix -p bed/);
});

test("#1150: BED insertion points open while zero-width bedGraph is rejected", async (t) => {
    const f = await fixture(t);
    for (const [name, content, problem] of [
        ["insertion.bed", "chr1\t2\t2\tinsertion\n", undefined],
        ["zero.bedgraph", "chr1\t2\t2\t0.5\n", "trackInvalid"],
    ]) {
        await writeFile(path.join(f.library, name), content);
        const result = await (await f.open(f.input, "", [path.join(f.library, name)])).json();
        if (problem) assert.deepEqual(result, { problem, fileName: name });
        else assert.equal(result.files.tracks[1].name, name);
    }
});

test("#1150: BED12 blocks must have complete numeric lists within the parent interval", async (t) => {
    const f = await trackFixtures(t);
    await writeFile(`${f.fasta}.fai`, "1\t3000\t3\t60\t61\n");
    const file = path.join(f.library, "blocks.bed");
    for (const blocks of ["1\t2900\t0", "2\t20\t0", "1\tXX\t0", "1\t20\t-1", "1\t0\t0", "1\t20\t70", "1"]) {
        await writeFile(file, `1\t200\t280\tpanel\t0\t+\t200\t280\t0\t${blocks}\n`);
        assert.deepEqual(await (await f.open(f.input, "", [file])).json(), { problem: "trackInvalid", fileName: "blocks.bed" });
    }
    await writeFile(file, "1\t200\t280\tpanel\t0\t+\t200\t280\t0\t2\t20,20,\t0,60,\n");
    const result = await (await f.open(f.input, "", [file])).json();
    assert.equal(result.files.tracks[1].name, "blocks.bed");
});

test("#1150: real tabix BED-preset bedGraph reports invalid values and reference overflow during decoding", async (t) => {
    const f = await trackFixtures(t);
    await writeFile(`${f.fasta}.fai`, "1\t3000\t3\t60\t61\n");
    for (const name of ["malformed.bedgraph.gz", "csi-malformed.bedgraph.gz"]) {
        const result = await (await f.open(f.input, "", [path.join(f.library, name)])).json();
        assert.ok(result.files, "Preflight uses the index; malformed records are checked only when decoded.");
        const problems = [];
        const track = validatedBedGraphTrack(result.files.tracks[1], (error) => problems.push({ problem: error.code, fileName: error.fileName }));
        const content = gunzipSync(await readFile(path.join(f.library, name))).toString("utf8").trim().split("\n");
        assert.deepEqual(track.decode(content[0].split("\t")), { chr: "1", start: 200, end: 230, value: 0.5 });
        for (const line of content.slice(1)) assert.equal(track.decode(line.split("\t")), undefined, "Malformed coverage must not reach rendering.");
        assert.deepEqual(problems, Array(2).fill({ problem: "trackInvalid", fileName: name }));
    }
});

test("#1150: TBI/CSI truncated within declared reference, bin, chunk or linear structures fails preflight", async (t) => {
    const f = await trackFixtures(t);
    await writeFile(`${f.fasta}.fai`, "1\t3000\t3\t60\t61\n");
    for (const [name, suffix] of [["panels.bed.gz", ".tbi"], ["csi-panels.bed.gz", ".csi"]]) {
        const index = path.join(f.library, `${name}${suffix}`);
        const original = gunzipSync(await readFile(index));
        const namesEnd = suffix === ".tbi" ? 36 + original.readInt32LE(32) : 16 + original.readInt32LE(12);
        for (const length of [namesEnd, namesEnd + 4, namesEnd + 9, original.length - 9]) {
            await writeFile(index, gzipSync(original.subarray(0, length)));
            assert.deepEqual(await (await f.open(f.input, "", [path.join(f.library, name)])).json(), { problem: "trackInvalid", fileName: name });
        }
        const invalidChunk = Buffer.from(original);
        const firstChunk = namesEnd + (suffix === ".tbi" ? 12 : 24);
        invalidChunk.writeBigUInt64LE(2n ** 63n, firstChunk);
        await writeFile(index, gzipSync(invalidChunk));
        assert.deepEqual(await (await f.open(f.input, "", [path.join(f.library, name)])).json(), { problem: "trackInvalid", fileName: name });
    }
});

test("#1150: bigWig data and index offsets and required headers must be inside a complete file", async (t) => {
    const f = await trackFixtures(t);
    await writeFile(`${f.fasta}.fai`, "1\t3000\t3\t60\t61\n");
    const file = path.join(f.library, "coverage.bw");
    const original = await readFile(file);
    const dataOffset = Number(original.readBigUInt64LE(16));
    const indexOffset = Number(original.readBigUInt64LE(24));
    for (const length of [dataOffset, dataOffset + 4, indexOffset, indexOffset + 47, indexOffset + 51]) {
        await writeFile(file, original.subarray(0, length));
        assert.deepEqual(await (await f.open(f.input, "", [file])).json(), { problem: "trackInvalid", fileName: "coverage.bw" });
    }
    for (const field of [16, 24]) {
        const invalid = Buffer.from(original);
        invalid.writeBigUInt64LE(BigInt(original.length), field);
        await writeFile(file, invalid);
        assert.deepEqual(await (await f.open(f.input, "", [file])).json(), { problem: "trackInvalid", fileName: "coverage.bw" });
    }
    const invalid = Buffer.from(original);
    invalid.writeUInt32LE(0, indexOffset);
    await writeFile(file, invalid);
    assert.deepEqual(await (await f.open(f.input, "", [file])).json(), { problem: "trackInvalid", fileName: "coverage.bw" });
});

test("#1148: supplemental symlinks cannot escape the configured library", async (t) => {
    const f = await trackFixtures(t);
    const outside = path.join(f.root, "outside.bed");
    const escape = path.join(f.library, "escape.bed");
    await writeFile(outside, "1\t200\t230\n");
    try {
        await symlink(outside, escape);
    } catch (error) {
        if (process.platform === "win32" && error.code === "EPERM") {
            t.skip("Windows symlink creation needs elevation or Developer Mode.");
            return;
        }
        throw error;
    }
    assert.equal((await f.open(f.input, "", [escape])).status, 403);
});
