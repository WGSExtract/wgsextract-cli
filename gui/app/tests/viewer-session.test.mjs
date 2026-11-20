import assert from "node:assert/strict";
import { copyFile, mkdir, mkdtemp, open as openFile, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import test from "node:test";
import { gzipSync } from "node:zlib";
import { createRequestHandler } from "../dist/web/src/server/routes.js";
import { parseConfigToml } from "../dist/web/src/server/config-toml.js";
import { registerUsersWorkspace } from "../dist/web/src/server/users-workspace.js";

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
    for (const [file, problem] of [[missing, "indexMissing"], [gzip, "trackCompression"], [invalid, "trackInvalid"], [large, "trackTooLarge"]]) {
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
    for (const tracks of ["panel.bed", [1], Array(17).fill(outside)]) {
        assert.equal((await f.open(f.input, "", tracks)).status, 400);
    }
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
