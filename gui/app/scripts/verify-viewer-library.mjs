import assert from "node:assert/strict";
import { mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";
import { chromium } from "playwright";

const { values } = parseArgs({ options: {
    url: { type: "string" }, fixtures: { type: "string" }, out: { type: "string" },
    "large-bam": { type: "string" }, vcf: { type: "string" }, chromium: { type: "string" },
    config: { type: "string" },
    "gene-map": { type: "string" },
    cytobands: { type: "string" },
    tracks: { type: "boolean", default: false },
    "track-regressions": { type: "boolean", default: false },
    "reference-name": { type: "string", default: "hs38DH.fa.gz" },
    locus: { type: "string", default: "chr20:10001200-10001450" },
    "next-locus": { type: "string", default: "chr20:10002400-10002700" },
} });
if (!values.url || !values.fixtures || !values.out || !values["large-bam"] || !values.vcf) {
    throw new Error("Usage: --url URL --fixtures DIR --large-bam PATH --vcf PATH --out DIR [--chromium PATH]");
}
const url = new URL(values.url);
assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(url.hostname));
const out = path.resolve(values.out);
const largeInfo = await stat(values["large-bam"]);
assert.ok(largeInfo.size >= 1024 ** 3, "--large-bam must name a file at least 1 GiB long.");
const largeFile = { sizeBytes: largeInfo.size, allocatedBytes: largeInfo.blocks == null ? null : largeInfo.blocks * 512 };
if (values.config) assert.ok(path.resolve(values.config).startsWith(`${out}${path.sep}`), "Only an isolated output-directory config may be changed.");
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ executablePath: values.chromium, headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, serviceWorkers: "block" });
const page = await context.newPage();
const errors = [];
const external = [];
const byteLog = [];
const byteReads = [];
const actionRequests = [];
const proofs = [];
let current = "";
let lastGrant;
page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/run" || pathname.startsWith("/api/run/")) actionRequests.push(request.url());
});
await context.route("**/*", (route) => {
    if (new URL(route.request().url()).origin !== url.origin) {
        external.push(route.request().url());
        return route.abort();
    }
    return route.continue();
});
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
await page.addInitScript(() => {
    window.policyViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => window.policyViolations.push(event.blockedURI));
});
page.on("response", async (response) => {
    const pathname = new URL(response.url()).pathname;
    if (pathname === "/api/viewer/open" && response.ok()) {
        const result = await response.json();
        if (result.files) lastGrant = result;
    }
    if (pathname.startsWith("/viewer-data/")) {
        const headers = response.headers();
        const read = { case: current, fileID: pathname, status: response.status(), range: headers["content-range"] ?? "", bytes: 0 };
        byteLog.push(read);
        byteReads.push(response.body().then((body) => {
            read.bytes = body.byteLength;
            assert.equal(read.bytes, Number(headers["content-length"] ?? 0));
        }).catch((error) => { errors.push(String(error)); }));
    }
});
async function checkDrawing(target, name) {
    await target.locator("[data-viewer-status]").filter({ hasText: `Matched local reference: ${values["reference-name"]}.` }).waitFor({ timeout: 60_000 });
    await target.locator(".igv-track-label").filter({ hasText: name }).waitFor();
    const canvases = await target.locator("[data-igv] canvas").evaluateAll((items) => items.filter((canvas) => {
        if (!canvas.width || !canvas.height || !canvas.getBoundingClientRect().width) return false;
        return canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data.some((value, i) => i % 4 === 3 && value > 0);
    }).length);
    assert.ok(canvases >= 2, "Actual indexed track and sequence/ruler must draw.");
}
/**
 * #1161: a personal report's findings and an mtDNA haplogroup's variants each open the viewer at
 * their row. Only the CLI run is replaced, by a report-shaped result document naming the real BAM;
 * the table, the track check, the viewer and its byte grants are the real app's.
 */
async function verifyResultRows() {
    current = "result-rows";
    const bam = path.resolve(values.fixtures, "reads.bam");
    const [contig, span] = values.locus.split(":");
    const position = Number(span.split("-")[0]) + 50;
    const raw = {
        schema: "wgsextract.result/1", command: "report", status: "ok", exit_code: 0, error: null,
        inputs: [{ path: bam, kind: "bam", role: "input", exists: true }],
        outputs: [], metrics: { findings: 2 }, warnings: [], manifest: null,
        details: {
            haplogrep: { Haplogroup: "H1a", Found_Polys: "73G 263G" },
            schema: "wgsextract.report/1",
            sections: {
                lineage: { title: "Lineage", findings: [{ label: "mtDNA haplogroup", value: "H1a", variant: null }] },
                // An evidence VCF on hg38: the fixture reference's build can't be confirmed as hg38.
                acmg_research: { title: "ACMG", build: "hg38", findings: [{ label: "DEMO2", value: "Likely pathogenic",
                    variant: { chrom: contig, pos: position, ref: "C", alt: "T" } }] },
                clinvar: { title: "ClinVar", findings: [{ label: "DEMO1", value: "Pathogenic",
                    // The other spelling of the reference's contig.
                    variant: { chrom: contig.startsWith("chr") ? contig.slice(3) : `chr${contig}`, pos: position, ref: "A", alt: "G" } }] },
            },
        },
    };
    const { parseResultDocument } = await import("../dist/shared/result-document.js");
    await page.route("**/api/run/stream", (route) => route.fulfill({
        status: 200, contentType: "application/x-ndjson; charset=utf-8",
        body: [{ type: "start", command: "wgsextract report" },
            { type: "complete", result: { exitCode: 0, command: "wgsextract report", stderr: "", resultDocument: parseResultDocument(raw, "wgsextract.result/1") } }]
            .map((event) => JSON.stringify(event)).join("\n") + "\n",
    }));
    try {
        await page.locator('[data-page-id="workflows"]').click();
        const section = page.locator('[data-section-toggle="workflows/personal-report"]');
        if (await section.getAttribute("aria-expanded") === "false") await section.click();
        await page.locator('[data-action-id="report"]').click();
        const finding = page.locator(".result-viewer-rows [data-result-viewer]:not([data-viewer-build])").first();
        await finding.waitFor({ timeout: 30_000 });
        assert.equal(await finding.getAttribute("data-result-viewer"), bam);
        const disabled = page.locator(".result-viewer-rows button[disabled]");
        assert.equal(await disabled.count(), 1, "Only the finding without a position is disabled.");
        assert.match(await disabled.getAttribute("title"), /No single genome position/);
        await finding.scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(out, "library-result-rows.png"), fullPage: true });
        await page.locator(".result-viewer-rows").first().screenshot({ path: path.join(out, "library-result-rows-table.png") });
        await finding.click();
        const viewer = page.frameLocator(".genome-viewer-shell iframe");
        await checkDrawing(viewer, "reads.bam");
        assert.equal(await viewer.locator('[data-locus] [name="locus"]').inputValue(), `${contig}:${position}-${position}`);
        await page.screenshot({ path: path.join(out, "library-result-row-locus.png"), fullPage: true });
        await page.getByRole("button", { name: "Back to WGS Extract", exact: true }).click();
        await page.locator(".result-viewer-rows [data-viewer-build='hg38']").click();
        await viewer.getByRole("alert").filter({ hasText: "different genome build" }).waitFor();
        assert.equal(await viewer.locator("[data-igv]").isVisible(), false, "A finding on another build never opens at the same numbers.");
        await page.getByRole("button", { name: "Back to WGS Extract", exact: true }).click();
        // rCRS positions never land on a reference without an rCRS mitochondrion: an explanation, not a view.
        await page.locator(".result-viewer-rows [data-viewer-locus='rCRS:263-263']").click();
        await viewer.getByRole("alert").filter({ hasText: /Position not found|rCRS/ }).waitFor();
        assert.equal(await viewer.locator("[data-igv]").isVisible(), false);
        await page.screenshot({ path: path.join(out, "library-result-row-mito.png"), fullPage: true });
        await page.getByRole("button", { name: "Back to WGS Extract", exact: true }).click();
        proofs.push({ case: "result-rows", finding: `${raw.details.sections.clinvar.findings[0].variant.chrom}:${position}`, opened: `${contig}:${position}-${position}` });
    } finally {
        await page.unroute("**/api/run/stream");
    }
}

try {
    await page.goto(new URL("/viewer", url).href);
    const cases = [
        ["bam", path.join(values.fixtures, "reads.bam")],
        ["cram", path.join(values.fixtures, "reads.cram")],
        ["vcf", values.vcf],
        ["large-bam", values["large-bam"]],
    ];
    for (const [label, file] of cases) {
        current = label;
        await page.locator('[name="library-path"]').fill(path.resolve(file));
        await page.locator('[data-library] [name="locus"]').fill(values.locus);
        await page.getByRole("button", { name: "Open library file", exact: true }).click();
        await checkDrawing(page, path.basename(file));
        await page.screenshot({ path: path.join(out, `library-${label}.png`), fullPage: true });
        await page.locator('[data-locus] [name="locus"]').fill(values["next-locus"]);
        await page.getByRole("button", { name: "Go", exact: true }).click();
        await page.locator("[data-viewer-status]").filter({ hasText: "Local tracks loaded" }).waitFor();
        await Promise.all(byteReads);
        proofs.push({ case: label, reference: lastGrant.files.reference.name, totalBytes: byteLog.filter((read) => read.case === label).reduce((sum, read) => sum + read.bytes, 0) });
    }
    // #1161: a result row may spell its contig as chr1 or 1; it opens in the reference's own spelling.
    current = "contig-alias";
    const aliased = values.locus.startsWith("chr") ? values.locus.slice(3) : `chr${values.locus}`;
    await page.locator('[name="library-path"]').fill(path.resolve(values.fixtures, "reads.bam"));
    await page.locator('[data-library] [name="locus"]').fill(aliased);
    await page.getByRole("button", { name: "Open library file", exact: true }).click();
    await checkDrawing(page, "reads.bam");
    assert.equal(await page.locator('[data-locus] [name="locus"]').inputValue(), values.locus);
    proofs.push({ case: "contig-alias", requested: aliased, opened: values.locus });
    assert.ok(proofs.find((proof) => proof.case === "large-bam").totalBytes < 16 * 1024 * 1024, "A large BAM locus must read bounded blocks and indexes.");
    assert.ok(byteLog.some((read) => read.status === 206), "Actual igv range requests must receive 206.");
    assert.ok(byteLog.every((read) => [200, 206].includes(read.status)));
    assert.deepEqual(await page.evaluate(() => window.policyViolations), []);
    if (values.tracks) {
        const supplemental = ["panels.bed", "panels.bed.gz", "csi-panels.bed.gz",
            "coverage.bedgraph", "coverage.bedgraph.gz", "csi-coverage.bedgraph.gz", "coverage.bw"];
        for (const name of supplemental) {
            current = name;
            const opened = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/viewer/open");
            await page.locator('[name="library-path"]').fill(path.resolve(values.fixtures, "reads.bam"));
            await page.locator('[name="extra-tracks"]').fill(path.resolve(values.fixtures, name));
            await page.locator('[data-library] [name="locus"]').fill(values.locus);
            await page.getByRole("button", { name: "Open library file", exact: true }).click();
            const session = await (await opened).json();
            assert.ok(session.files, `Track preflight failed: ${session.problem} (${session.fileName ?? ""})`);
            assert.equal(session.files.tracks.length, 2);
            await checkDrawing(page, name);
            const track = page.locator(".igv-viewport").filter({ has: page.locator(".igv-track-label", { hasText: name }) });
            await track.waitFor();
            // Check pixels in the data viewport, not the axis or a label.
            assert.ok(await track.locator("canvas").evaluateAll((canvases) => canvases.some((canvas) => {
                const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
                return pixels.some((value, i) => i % 4 === 3 && value > 0 && (pixels[i - 3] < 220 || pixels[i - 2] < 220 || pixels[i - 1] < 220));
            })), `${name} must draw its intervals or values.`);
            await page.locator('[data-locus] [name="locus"]').fill(values["next-locus"]);
            await page.getByRole("button", { name: "Go", exact: true }).click();
            await page.screenshot({ path: path.join(out, `library-${name}.png`), fullPage: true });
            await Promise.all(byteReads);
            const urls = session.files.tracks.slice(1).flatMap((track) => [track.url, ...(track.indexURL ? [track.indexURL] : [])]);
            const reads = byteLog.filter((read) => read.case === name && urls.includes(read.fileID));
            assert.ok(reads.length, "Supplemental tracks use the scoped endpoint.");
            if (name.endsWith(".gz") || name.endsWith(".bw")) assert.ok(reads.some((read) => read.status === 206), "Indexed supplemental tracks use ranges.");
            proofs.push({ case: name, totalBytes: reads.reduce((sum, read) => sum + read.bytes, 0) });
        }
        await page.locator('[name="extra-tracks"]').fill("");
        assert.deepEqual(await page.evaluate(() => window.policyViolations), []);
    }
    if (values.config) {
        const original = await readFile(values.config, "utf8");
        const missing = path.join(out, "missing-reference-library");
        await mkdir(missing, { recursive: true });
        try {
            await writeFile(values.config, original
                .replace(/^reference_library\s*=.*$/m, `reference_library = ${JSON.stringify(missing)}`)
                .replace(/^reference_genome_library\s*=.*$/m, `reference_genome_library = ${JSON.stringify(missing)}`));
            await page.locator('[name="library-path"]').fill(path.resolve(values.fixtures, "reads.cram"));
            await page.getByRole("button", { name: "Open library file", exact: true }).click();
            await page.getByRole("alert").filter({ hasText: "Reference not installed" }).waitFor();
            assert.equal(await page.locator("[data-igv]").isVisible(), false, "A failed new file must not display the previous file's reads.");
            await page.screenshot({ path: path.join(out, "library-missing-reference.png"), fullPage: true });
            proofs.push({ case: "missing-reference", actionable: true });
        } finally {
            await writeFile(values.config, original);
        }
    }
    if (values["track-regressions"]) {
        assert.match(await page.locator("#extra-tracks-help").innerText(), /16 tracks/);
        for (const [paths, expected] of [
            [Array(17).fill(path.resolve(values.fixtures, "panels.bed")), /at most 16 tracks/],
            [[path.resolve(values.fixtures, "missing.bed")], /missing\.bed was not found/],
            [[path.resolve(values.fixtures, "reads.bam")], /Use BED.*bedGraph.*bigWig/],
        ]) {
            await page.locator('[name="extra-tracks"]').fill(paths.join("\n"));
            await page.getByRole("button", { name: "Open library file", exact: true }).click();
            await page.getByRole("alert").filter({ hasText: expected }).waitFor();
        }
        for (const name of ["malformed.bedgraph.gz", "csi-malformed.bedgraph.gz"]) {
            current = name;
            await page.locator('[name="extra-tracks"]').fill(path.resolve(values.fixtures, name));
            await page.getByRole("button", { name: "Open library file", exact: true }).click();
            await page.getByRole("alert").filter({ hasText: `Could not read ${name}` }).waitFor();
            await page.locator('[data-locus]:not([hidden])').waitFor();
            await page.locator('[data-locus] [name="locus"]').fill(values["next-locus"]);
            await page.getByRole("button", { name: "Go", exact: true }).click();
            await page.getByRole("alert").filter({ hasText: `Could not read ${name}` }).waitFor();
            await page.screenshot({ path: path.join(out, `library-invalid-${name}.png`), fullPage: true });
            proofs.push({ case: name, actionable: true, malformedValuesNotRendered: true });
        }
        await page.locator('[name="extra-tracks"]').fill("");
    }
    if (values.cytobands) {
        const table = path.resolve(values.cytobands);
        assert.ok(table.startsWith(`${out}${path.sep}`), "Only isolated cytoband fixtures may be changed.");
        current = "cytoband-ideogram";
        await page.locator('[name="library-path"]').fill(path.resolve(values.fixtures, "reads.bam"));
        await page.getByRole("button", { name: "Open library file", exact: true }).click();
        await checkDrawing(page, "reads.bam");
        const ideogram = page.locator(".igv-ideogram-canvas");
        await ideogram.first().waitFor();
        assert.ok(await ideogram.evaluateAll((canvases) => canvases.some((canvas) => {
            // Interior pixels distinguish bands from an empty dark outline.
            const pixels = canvas.getContext("2d").getImageData(
                Math.floor(canvas.width * 0.75), Math.floor(canvas.height * 0.5), 3, 1,
            ).data;
            return pixels.some((value, i) => i % 4 === 3 && value > 0 && pixels[i - 3] < 128);
        })), "Installed cytobands must fill the interior of the actual ideogram, not just draw an outline.");
        assert.ok(await ideogram.evaluateAll((canvases) => canvases.some((canvas) => {
            const pixels = canvas.getContext("2d").getImageData(
                Math.floor(canvas.width * 0.45), Math.floor(canvas.height * 0.5), Math.floor(canvas.width * 0.1), 1,
            ).data;
            return pixels.some((value, i) => i % 4 === 3 && value > 0
                && pixels[i - 3] > 50 && pixels[i - 2] < 50 && pixels[i - 1] < 50);
        })), "Interleaved chromosome rows must retain centromere bands as well as the final dark arm.");
        assert.ok(lastGrant.files.cytobands.features.length > 0);
        await page.screenshot({ path: path.join(out, "library-cytobands.png"), fullPage: true });
        await rename(table, `${table}.saved`);
        try {
            await page.getByRole("button", { name: "Open library file", exact: true }).click();
            await checkDrawing(page, "reads.bam");
            await page.locator("[data-cytoband-status]").filter({ hasText: "Cytoband resource cytobands_hg38.tsv is not installed" }).waitFor();
            assert.match(await page.locator("[data-cytoband-status]").innerText(), /wgsextract ref cytobands --build hg38 --download/);
            assert.equal(await ideogram.count(), 0);
            await page.locator('[data-locus] [name="locus"]').fill(values["next-locus"]);
            await page.getByRole("button", { name: "Go", exact: true }).click();
            await page.locator("[data-viewer-status]").filter({ hasText: "Local tracks loaded" }).waitFor();
            await page.screenshot({ path: path.join(out, "library-cytobands-missing.png"), fullPage: true });
        } finally {
            await rename(`${table}.saved`, table);
        }
        proofs.push({ case: "cytoband-ideogram", missingDataActionable: true });
    }
    if (values["gene-map"]) {
        const table = path.resolve(values["gene-map"]);
        assert.ok(table.startsWith(`${out}${path.sep}`), "Only isolated gene fixtures may be changed.");
        current = "gene-navigation";
        await page.locator('[name="library-path"]').fill(path.resolve(values.fixtures, "reads.bam"));
        await page.getByRole("button", { name: "Open library file", exact: true }).click();
        await checkDrawing(page, "RefSeq gene spans");
        const genes = page.locator(".igv-viewport").filter({ has: page.locator(".igv-track-label", { hasText: "RefSeq gene spans" }) });
        assert.ok(await genes.locator("canvas").evaluateAll((canvases) => canvases.some((canvas) => {
            const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
            return pixels.some((value, i) => i % 4 === 3 && value > 0 && (pixels[i - 3] < 220 || pixels[i - 2] < 220 || pixels[i - 1] < 220));
        })), "Gene spans must draw in the data viewport.");
        await page.locator('[data-locus] [name="locus"]').fill("refgene_demo");
        await page.getByRole("button", { name: "Go", exact: true }).click();
        await page.locator('[data-locus] [name="locus"]').filter({ visible: true }).waitFor();
        await page.waitForFunction(() => document.querySelector('[data-locus] [name="locus"]').value === "1:200-400");
        await page.screenshot({ path: path.join(out, "library-gene-navigation.png"), fullPage: true });
        await page.locator('[data-locus] [name="locus"]').fill("UNKNOWN");
        await page.getByRole("button", { name: "Go", exact: true }).click();
        await page.getByRole("alert").filter({ hasText: "Gene symbol not found" }).waitFor();
        await rename(table, `${table}.saved`);
        try {
            await page.getByRole("button", { name: "Open library file", exact: true }).click();
            await checkDrawing(page, "reads.bam");
            await page.locator("[data-gene-status]").filter({ hasText: "Gene-map genes_hg38.tsv is not installed" }).waitFor();
            assert.match(await page.locator("[data-gene-status]").innerText(), /wgsextract ref gene-map/);
            assert.equal(await page.locator(".igv-track-label").filter({ hasText: "RefSeq gene spans" }).count(), 0);
            await page.screenshot({ path: path.join(out, "library-gene-missing.png"), fullPage: true });
            await page.locator('[data-locus] [name="locus"]').fill("REFGENE_DEMO");
            await page.getByRole("button", { name: "Go", exact: true }).click();
            await page.getByRole("alert").filter({ hasText: "Gene search is unavailable" }).waitFor();
        } finally {
            await rename(`${table}.saved`, table);
        }
        proofs.push({ case: "gene-navigation", locus: "1:200-400", missingDataActionable: true });
    }
    // The real tool page's selected file enters the sibling pane, not a document navigation.
    current = "entry-point";
    await page.addLocatorHandler(page.locator("[data-setup-prompt-dismiss]"), async (button) => { await button.click(); });
    await page.goto(url.href);
    await page.locator('[data-page-id="info-bam"]').click();
    await page.locator('[data-field-id="bam_path"]').fill(path.resolve(values.fixtures, "reads.cram"));
    await page.locator('[data-field-id="bam_path"]').dispatchEvent("change");
    await page.getByRole("button", { name: "View reads", exact: true }).click();
    const frame = page.frameLocator(".genome-viewer-shell iframe");
    await checkDrawing(frame, "reads.cram");
    const grant = lastGrant;
    await frame.locator('[data-locus] [name="locus"]').fill(values.locus);
    await frame.getByRole("button", { name: "Go", exact: true }).click();
    await frame.locator("[data-viewer-status]").filter({ hasText: "Local tracks loaded" }).waitFor();
    await page.screenshot({ path: path.join(out, "library-entry-point.png"), fullPage: true });
    assert.deepEqual(await frame.locator("body").evaluate(() => window.policyViolations), []);
    const closed = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/viewer/close");
    await page.getByRole("button", { name: "Back to WGS Extract", exact: true }).click();
    await closed;
    const revoked = await context.request.get(new URL(grant.files.tracks[0].url, url).href, { headers: grant.files.tracks[0].headers });
    assert.equal(revoked.status(), 401, "Closing the pane revokes its scoped byte grant.");
    // Only the self-hosted isolated setup opts into commands that write result files.
    if (values.config) {
        await page.locator('[data-page-id="extract"]').click();
        const extraction = page.locator('[data-section-toggle="extract/extract-inputs"]');
        if (await extraction.getAttribute("aria-expanded") === "false") await extraction.click();
        await page.locator('[data-field-id="bam_path"]').fill(path.resolve(values.fixtures, "reads.bam"));
        await page.locator('[data-field-id="bam_path"]').dispatchEvent("change");
        await page.locator('[data-field-id="extract_region"]').fill(values.locus);
        await page.locator('[data-field-id="extract_region"]').dispatchEvent("change");
        await page.locator('[data-action-id="custom"]').click();
        const resultLink = page.locator("[data-result-viewer]").first();
        await resultLink.waitFor({ timeout: 60_000 });
        assert.equal(await resultLink.getAttribute("data-viewer-locus"), values.locus);
        await resultLink.scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(out, "library-extraction-result.png"), fullPage: true });
        await resultLink.click();
        const resultViewer = page.frameLocator(".genome-viewer-shell iframe");
        await resultViewer.locator("[data-locus]:not([hidden])").waitFor();
        assert.equal(await resultViewer.locator('[data-locus] [name="locus"]').inputValue(), values.locus);
        assert.deepEqual(await resultViewer.locator("body").evaluate(() => window.policyViolations), []);
        await page.screenshot({ path: path.join(out, "library-result-locus.png"), fullPage: true });
        proofs.push({ case: "extracted-region-result", locus: values.locus, reference: lastGrant.files.reference.name });
        await page.getByRole("button", { name: "Back to WGS Extract", exact: true }).click();
        assert.equal(await resultLink.evaluate((element) => element === document.activeElement), true, "Returning restores focus to the result action.");
        await verifyResultRows();
    } else {
        assert.deepEqual(actionRequests, [], "The standalone checker must not run commands in the connected app.");
    }
    assert.deepEqual(await page.evaluate(() => window.policyViolations), []);
    await Promise.all(byteReads);
    assert.deepEqual(external, [], "No request leaves the loopback origin.");
    assert.deepEqual(errors, [], "No console, CSP or page errors.");
    await writeFile(path.join(out, "library-browser-proof.json"), JSON.stringify({ proofs, largeFile, byteLog, actionRequests, external, errors, revokedStatus: revoked.status() }, null, 2));
    console.log(JSON.stringify(proofs));
} catch (error) {
    await page.screenshot({ path: path.join(out, "library-failure.png"), fullPage: true });
    await writeFile(path.join(out, "library-failure.html"), await page.content());
    throw error;
} finally {
    await writeFile(path.join(out, "library-console.json"), JSON.stringify({ errors, external, byteLog }, null, 2));
    await context.close();
    await browser.close();
}
