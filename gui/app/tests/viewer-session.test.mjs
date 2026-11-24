import assert from "node:assert/strict";
import { copyFile, mkdir, mkdtemp, open as openFile, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
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
    // What the resolver's sequence check says the mitochondrion is, when asked (#1161).
    const resolver = { mitoOrigin: "rCRS", mitoAsked: 0 };
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
        async runProcess(_script, [file, settings, ...flags]) {
            assert.equal(await readFile(file, "utf8"), "reads");
            const values = parseConfigToml(await readFile(settings, "utf8"));
            const mito = flags.includes("--mito-origin");
            resolver.mitoAsked += mito ? 1 : 0;
            const result = values.reference_library
                ? { fasta: path.join(values.reference_library, "reference.fa"), locus: "chr1:1-4", ...(mito ? { mitoOrigin: resolver.mitoOrigin } : {}) }
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
    return { root, users, config, library, input, fasta, base, open, resolver };
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

test("#1161: result-row loci open in the matched reference's contig spelling, and rCRS only on an rCRS chrM", async (t) => {
    const f = await fixture(t);
    await writeFile(`${f.fasta}.fai`, "1\t1000\t6\t60\t61\nMT\t16569\t1100\t60\t61\n");
    const opened = async (locus) => (await (await f.open(f.input, locus)).json());
    assert.equal((await opened("chr1:200-300")).locus, "1:200-300");
    assert.equal((await opened("chrM:73-73")).locus, "MT:73-73");
    assert.equal((await opened("rCRS:263-263")).locus, "MT:263-263");
    assert.equal((await opened("chr1:990-1200")).locus, "1:990-1000", "A span past the contig end is clipped, not refused.");
    assert.deepEqual(await opened("chr1:1001-1001"), { problem: "locusNotFound" });
    assert.deepEqual(await opened("chr2:1-10"), { problem: "locusNotFound" });
    assert.equal(f.resolver.mitoAsked, 1, "Only rCRS rows ask the resolver to read the mitochondrion.");
    // Aliases never convert coordinates: a finding on another build is refused, not shown elsewhere.
    const onBuild = async (build) => (await fetch(`${f.base}/api/viewer/open`, {
        method: "POST", headers: { "x-gfc-token": "viewer-test", "content-type": "application/json" },
        body: JSON.stringify({ path: f.input, locus: "chr1:200-300", build }),
    }));
    assert.deepEqual(await (await onBuild("hg38")).json(), { problem: "buildMismatch" }, "A reference of unknown build can't confirm hg38.");
    assert.equal((await onBuild("T2T")).status, 400);
    // CHM13v2.0's chrM is as long as the rCRS but numbered from elsewhere round the circle.
    f.resolver.mitoOrigin = "CHM13v2.0's chrM";
    assert.deepEqual(await opened("rCRS:263-263"), { problem: "mitoNotRcrs" });
    // hg19's Yoruba chrM can start like the rCRS but is 16,571 bp and numbered differently.
    f.resolver.mitoOrigin = "rCRS";
    await writeFile(`${f.fasta}.fai`, "chr1\t1000\t6\t60\t61\nchrM\t16571\t1100\t60\t61\n");
    assert.deepEqual(await opened("rCRS:263-263"), { problem: "mitoNotRcrs" });
    assert.equal((await opened("MT:263-263")).locus, "chrM:263-263", "Positions from the sample's own calls keep its chrM numbering.");
    await writeFile(`${f.fasta}.fai`, "chr1\t249250621\t6\t60\t61\n");
    assert.deepEqual(await (await onBuild("hg38")).json(), { problem: "buildMismatch" }, "An hg38 finding never opens on hg19.");
    assert.equal((await (await onBuild("hg19")).json()).locus, "chr1:200-300");
});

test("#1161: result-row checks report each sample file's viewer problem without granting bytes", async (t) => {
    const f = await fixture(t);
    const outside = path.join(f.root, "outside.bam");
    await writeFile(outside, "reads");
    const unindexed = path.join(f.library, "unindexed.cram");
    await writeFile(unindexed, "reads");
    const missing = path.join(f.library, "gone.bam");
    const check = (paths, headers = { "x-gfc-token": "viewer-test" }) => fetch(`${f.base}/api/viewer/check`, {
        method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify({ paths }),
    });
    const response = await check([f.input, "{{usersWorkspace}}/mygenomes/sample.bam", outside, unindexed, missing, `${f.library}/../outside.bam`]);
    assert.equal(response.status, 200);
    assert.deepEqual((await response.json()).tracks, {
        [f.input]: { ok: true },
        "{{usersWorkspace}}/mygenomes/sample.bam": { ok: true },
        [outside]: { problem: "rowOutsideLibrary", fileName: "outside.bam" },
        [unindexed]: { problem: "rowIndexMissing", fileName: "unindexed.cram" },
        [missing]: { problem: "rowTrackMissing", fileName: "gone.bam" },
        [`${f.library}/../outside.bam`]: { problem: "rowOutsideLibrary", fileName: "outside.bam" },
    });
    assert.equal((await check([f.input], {})).status, 401);
    assert.equal((await check(Array(17).fill(f.input))).status, 400);
});

async function geneFixture(t, contig = "chr1", length = 248956422) {
    const f = await fixture(t);
    await writeFile(`${f.fasta}.fai`, `${contig}\t${length}\t6\t4\t5\n`);
    const directory = path.join(path.dirname(f.fasta), "downloads");
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

test("#1166: installed transcript and exon models reach the viewer as a revocable indexed track", async (t) => {
    const f = await geneFixture(t);
    const missing = await (await f.open()).json();
    assert.deepEqual(missing.files.transcripts, { notices: [{
        code: "transcriptsMissing", fileName: "transcripts_hg38.bed.gz",
        command: `wgsextract ref gene-map --ref ${shellQuote(path.dirname(f.fasta))}`,
    }] }, "Missing models name the explicit download; the viewer fetches nothing.");
    const source = path.resolve("../../tests/fixtures/viewer_transcripts/transcripts_hg38.bed.gz");
    await copyFile(source, path.join(path.dirname(f.table), "transcripts_hg38.bed.gz"));
    await copyFile(`${source}.tbi`, path.join(path.dirname(f.table), "transcripts_hg38.bed.gz.tbi"));
    const session = await (await f.open()).json();
    const { transcripts } = session.files;
    assert.deepEqual(transcripts.notices, []);
    const data = await fetch(`${f.base}${transcripts.url}`, { headers: { ...transcripts.headers, range: "bytes=0-1" } });
    assert.equal(data.status, 206);
    assert.deepEqual(Buffer.from(await data.arrayBuffer()), Buffer.from([31, 139]));
    assert.equal((await fetch(`${f.base}${transcripts.indexURL}`, { headers: transcripts.headers })).status, 200);
    await writeFile(`${f.fasta}.fai`, "chrZ\t248956422\t6\t4\t5\n");
    assert.equal((await (await f.open()).json()).files.transcripts.notices[0].code, "transcriptsBuildUnsupported");
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

test("viewer opens indexed alignments and supplemental tracks outside My Genomes while rejecting traversal and remote URLs", async (t) => {
    const f = await trackFixtures(t);
    await writeFile(`${f.fasta}.fai`, "1\t3000\t3\t60\t61\n");
    const externalDir = path.join(f.root, "external-drive");
    await mkdir(externalDir);
    const externalBam = path.join(externalDir, "outside.bam");
    await writeFile(externalBam, "reads");
    await writeFile(`${externalBam}.bai`, "index");
    const outsideBed = path.join(externalDir, "outside.bed");
    await writeFile(outsideBed, "1\t200\t230\n");

    const ok = await f.open(externalBam, "", [outsideBed]);
    assert.equal(ok.status, 200);
    const session = await ok.json();
    assert.deepEqual(session.files.tracks.map((track) => track.name), ["outside.bam", "outside.bed"]);
    assert.equal(await (await fetch(`${f.base}${session.files.tracks[0].url}`, { headers: session.files.tracks[0].headers })).text(), "reads");
    assert.equal(await (await fetch(`${f.base}${session.files.tracks[1].url}`, { headers: session.files.tracks[1].headers })).text(), "1\t200\t230\n");

    for (const file of [`${f.library}/unused/../panels.bed`, "https://example.com/panel.bed", "relative.bed"]) {
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

test("#1150: missing supplemental files return trackMissing regardless of folder", async (t) => {
    const f = await fixture(t);
    for (const file of [path.join(f.library, "missing.bed"), path.join(f.input, "mistyped.bed"), path.join(f.root, "missing.bed")]) {
        const response = await f.open(f.input, "", [file]);
        assert.equal(response.status, 200);
        assert.deepEqual(await response.json(), { problem: "trackMissing", fileName: path.basename(file) });
    }
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

test("#1148: reference library annotations never follow escaping symlinks", async (t) => {
    const f = await geneFixture(t, "chr1", 248956422);
    const downloads = path.dirname(f.table);
    const outside = path.join(f.root, "outside_clinvar.vcf.gz");
    await writeFile(outside, minimalBgzf("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\nchr1\t100\trs1\tA\tG\t.\t.\t.\n"));
    await writeFile(`${outside}.tbi`, minimalTabix(["chr1"], "vcf"));
    const escape = path.join(downloads, "clinvar_hg38.vcf.gz");
    try {
        await symlink(outside, escape);
        await symlink(`${outside}.tbi`, `${escape}.tbi`);
    } catch (error) {
        if (process.platform === "win32" && error.code === "EPERM") {
            t.skip("Windows symlink creation needs elevation or Developer Mode.");
            return;
        }
        throw error;
    }
    const session = await (await f.open()).json();
    assert.deepEqual(session.files.tracks.map((track) => track.name), ["sample.bam"]);
});

test("viewer auto-discovers ref pharmgkb TSV.gz and decodes its PHARMGKB annotations", async (t) => {
    const f = await geneFixture(t, "chr1", 248956422);
    const downloads = path.dirname(f.table);
    const tsv = path.join(downloads, "pharmgkb_hg38.tsv.gz");
    await writeFile(tsv, minimalBgzf("##source=ClinPGx\n#CHROM\tPOS\tREF\tALT\tPHARMGKB\nchr1\t1000\tA\tG\t1A:warfarin\n"));
    await writeFile(`${tsv}.tbi`, minimalTabix(["chr1"], "rsid-tab"));
    const session = await (await f.open()).json();
    const pharmgkb = session.files.tracks.find((track) => track.name === "PharmGKB");
    assert.ok(pharmgkb, "PharmGKB TSV track discovered");
    assert.equal(pharmgkb.format, "pharmgkb-tsv");
    const decoded = validatedBedGraphTrack(pharmgkb, () => {});
    assert.deepEqual(decoded.decode(["chr1", "1000", "A", "G", "1A:warfarin"]), {
        chr: "chr1",
        start: 999,
        end: 1000,
        name: "1A:warfarin",
    });
    assert.equal(decoded.decode(["#CHROM", "POS", "REF", "ALT", "PHARMGKB"]), undefined);
});

function minimalBgzf(text) {
    const gz = gzipSync(Buffer.from(text, "utf8"));
    // Insert a valid BGZF BC subfield (6 bytes) into the gzip header and update FLG=FEXTRA.
    const extra = Buffer.from([6, 0, 66, 67, 2, 0, 0, 0]);
    const block = Buffer.concat([gz.subarray(0, 3), Buffer.from([gz[3] | 4]), gz.subarray(4, 10), extra, gz.subarray(10)]);
    block.writeUInt16LE(block.length - 1, 16);
    return block;
}

function minimalTabix(contigs, preset) {
    const names = Buffer.from(`${contigs.join("\0")}\0`, "utf8");
    const header = Buffer.alloc(36);
    header.write("TBI\x01", 0, "ascii");
    header.writeInt32LE(contigs.length, 4);
    header.writeInt32LE(preset === "vcf" ? 2 : 0, 8);
    header.writeInt32LE(1, 12);
    header.writeInt32LE(2, 16);
    header.writeInt32LE(preset === "vcf" ? 0 : 2, 20);
    header.writeInt32LE("#".charCodeAt(0), 24);
    header.writeInt32LE(0, 28);
    header.writeInt32LE(names.length, 32);
    // One empty reference entry (0 bins, 0 intervals) per contig.
    const body = Buffer.alloc(contigs.length * 8);
    return gzipSync(Buffer.concat([header, names, body]));
}

test("installed rsID and variant annotation files auto-load for the matching build and decode safely", async (t) => {
    const f = await geneFixture(t, "chr1", 248956422);
    const downloads = path.dirname(f.table);
    const microarray = path.join(path.dirname(f.fasta), "microarray");
    await mkdir(microarray, { recursive: true });

    const rsidPath = path.join(microarray, "All_SNPs_hg38_ref.tab.gz");
    await writeFile(rsidPath, minimalBgzf("#CHROM\tPOS\tID\nchr1\t1000\trs12345\n"));
    await writeFile(`${rsidPath}.tbi`, minimalTabix(["chr1"], "rsid-tab"));

    const clinvarPath = path.join(downloads, "clinvar_hg38.vcf.gz");
    await writeFile(clinvarPath, minimalBgzf("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n1\t1000\trs12345\tA\tG\t.\t.\t.\n"));
    await writeFile(`${clinvarPath}.tbi`, minimalTabix(["1"], "vcf"));

    // Wrong-build and unindexed files must never be loaded.
    const wrongBuild = path.join(microarray, "All_SNPs_hg19_ref.tab.gz");
    await writeFile(wrongBuild, minimalBgzf("#CHROM\tPOS\tID\nchr1\t1000\trs99999\n"));
    await writeFile(`${wrongBuild}.tbi`, minimalTabix(["chr1"], "rsid-tab"));
    await writeFile(path.join(downloads, "dbsnp_hg38.vcf.gz"), minimalBgzf("##fileformat=VCFv4.2\n"));

    const session = await (await f.open()).json();
    const names = session.files.tracks.map((track) => track.name);
    assert.deepEqual(names, ["sample.bam", "rsIDs", "ClinVar"]);

    const rsidTrack = session.files.tracks.find((track) => track.name === "rsIDs");
    assert.equal(rsidTrack.format, "rsid-tab");
    assert.equal(rsidTrack.type, "annotation");
    assert.equal((await fetch(`${f.base}${rsidTrack.url}`, { headers: rsidTrack.headers })).status, 200);
    const decodedTrack = validatedBedGraphTrack(rsidTrack, () => {});
    assert.equal(decodedTrack.format, "bed");
    assert.deepEqual(decodedTrack.decode(["chr1", "1000", "rs12345"]), { chr: "chr1", start: 999, end: 1000, name: "rs12345" });
    assert.equal(decodedTrack.decode(["#CHROM", "POS", "ID"]), undefined);

    const clinvarTrack = session.files.tracks.find((track) => track.name === "ClinVar");
    assert.equal(clinvarTrack.format, "vcf");
    assert.equal(clinvarTrack.type, "variant");
});

test("legacy ref/ directory is searched for installed genes and cytobands", async (t) => {
    const f = await geneFixture(t, "chr1", 248956422);
    const legacyRef = path.join(path.dirname(f.fasta), "ref");
    await mkdir(legacyRef, { recursive: true });
    await writeFile(path.join(legacyRef, "genes_hg38.tsv"), "symbol\tchrom\tstart\tend\nLEGACY_GENE\tchr1\t100\t200\n");
    await writeFile(path.join(legacyRef, "cytobands_hg38.tsv"), cytobandTable(248956422));
    const session = await (await f.open()).json();
    assert.equal(session.files.genes.features[0].name, "LEGACY_GENE");
    assert.equal(session.files.cytobands.features.length, 2);
});

function writeRsidSqlite(file, build, rows) {
    const db = new DatabaseSync(file);
    try {
        db.exec("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;");
        db.exec("CREATE TABLE chroms (cid INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);");
        db.exec("CREATE TABLE rsids (rsid INTEGER PRIMARY KEY, cid INTEGER NOT NULL, pos INTEGER NOT NULL);");
        const chroms = new Map();
        const insertChrom = db.prepare("INSERT INTO chroms (cid, name) VALUES (?, ?)");
        const insertRsid = db.prepare("INSERT INTO rsids (rsid, cid, pos) VALUES (?, ?, ?)");
        for (const [rsid, chrom, pos] of rows) {
            let cid = chroms.get(chrom);
            if (!cid) {
                cid = chroms.size + 1;
                chroms.set(chrom, cid);
                insertChrom.run(cid, chrom);
            }
            insertRsid.run(rsid, cid, pos);
        }
        const insertMeta = db.prepare("INSERT INTO meta (key, value) VALUES (?, ?)");
        insertMeta.run("schema_version", "1");
        insertMeta.run("build", build);
        insertMeta.run("source", "dbSNP_b151_common");
        insertMeta.run("count", String(rows.length));
    } finally {
        db.close();
    }
}

test("installed dbSNP common rsIDs auto-load as a track and resolve offline 100 bp rsID searches", async (t) => {
    const f = await geneFixture(t, "chr1", 248956422);
    await writeFile(`${f.fasta}.fai`, "chr1\t248956422\t6\t4\t5\nchr19\t58617616\t6\t4\t5\nchrX\t156040895\t6\t4\t5\n");
    const downloads = path.dirname(f.table);
    const dbsnpVcf = path.join(downloads, "dbsnp_common_hg38.vcf.gz");
    await writeFile(dbsnpVcf, minimalBgzf("##fileformat=VCFv4.0\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n19\t44908684\trs429358\tT\tC\t.\t.\tCOMMON=1\n"));
    await writeFile(`${dbsnpVcf}.tbi`, minimalTabix(["1", "19", "X"], "vcf"));
    const sqlitePath = path.join(downloads, "dbsnp_common_hg38.rsid.sqlite");
    writeRsidSqlite(sqlitePath, "hg38", [
        [429358, "19", 44908684],
        [1001, "1", 10],
        [2002, "X", 154032200],
    ]);

    const session = await (await f.open()).json();
    assert.deepEqual(session.files.tracks.map((track) => track.name), ["sample.bam", "dbSNP"]);
    assert.deepEqual(session.files.rsids, { available: true, fileName: "dbsnp_common_hg38.rsid.sqlite", build: "hg38", notices: [] });

    // Opening directly with an initial rsID resolves when dbSNP is installed.
    const directRsid = await (await f.open(f.input, "rs429358")).json();
    assert.equal(directRsid.locus, "chr19:44908635-44908734");
    assert.deepEqual(await (await f.open(f.input, "rs404")).json(), { problem: "rsidNotFound" });

    const lookup = (body, headers = { "x-gfc-token": "viewer-test" }) => fetch(`${f.base}/api/viewer/rsid`, {
        method: "POST",
        headers: { "content-type": "application/json", ...headers },
        body: JSON.stringify(body),
    });

    assert.equal((await lookup({ id: session.id, rsid: "rs429358" }, {})).status, 401);
    assert.deepEqual(await (await lookup({ id: session.id, rsid: "RS429358" })).json(), {
        locus: "chr19:44908635-44908734",
        rsid: "rs429358",
    });
    assert.deepEqual(await (await lookup({ id: session.id, rsid: "rs1001" })).json(), {
        locus: "chr1:1-100",
        rsid: "rs1001",
    });
    assert.deepEqual(await (await lookup({ id: session.id, rsid: "rs2002" })).json(), {
        locus: "chrX:154032151-154032250",
        rsid: "rs2002",
    });
    assert.deepEqual(await (await lookup({ id: session.id, rsid: "rs404" })).json(), { problem: "rsidNotFound" });
    assert.deepEqual(await (await lookup({ id: session.id, rsid: "APOE" })).json(), { problem: "invalidLocus" });

    await fetch(`${f.base}/api/viewer/close`, {
        method: "POST",
        headers: { "x-gfc-token": "viewer-test", "content-type": "application/json" },
        body: JSON.stringify({ id: session.id }),
    });
    assert.deepEqual(await (await lookup({ id: session.id, rsid: "rs429358" })).json(), { problem: "rsidSearchUnavailable" });
});

test("dbSNP rsID search rejects wrong-build databases and escaping symlinks and reports missing notice", async (t) => {
    const f = await geneFixture(t, "chr1", 248956422);
    const missing = await (await f.open()).json();
    assert.equal(missing.files.rsids.available, false);
    assert.deepEqual(missing.files.rsids.notices, [{ code: "rsidMissing", fileName: "dbsnp_common_hg38.rsid.sqlite", build: "GRCh38" }]);

    const downloads = path.dirname(f.table);
    const sqlitePath = path.join(downloads, "dbsnp_common_hg38.rsid.sqlite");
    writeRsidSqlite(sqlitePath, "hg19", [[429358, "1", 1000]]);
    const wrongBuild = await (await f.open()).json();
    assert.equal(wrongBuild.files.rsids.available, false);
    assert.equal(wrongBuild.files.rsids.notices[0].code, "rsidIndexInvalid");
    await rm(sqlitePath);

    const outside = path.join(f.root, "outside-rsids.sqlite");
    writeRsidSqlite(outside, "hg38", [[429358, "1", 1000]]);
    try {
        await symlink(outside, sqlitePath);
    } catch (error) {
        if (["EPERM", "EACCES", "ENOTSUP"].includes(error.code)) return t.skip("Symlinks unavailable.");
        throw error;
    }
    const escaped = await (await f.open()).json();
    assert.equal(escaped.files.rsids.available, false);
    assert.equal(escaped.files.rsids.notices[0].code, "rsidIndexInvalid");
});
