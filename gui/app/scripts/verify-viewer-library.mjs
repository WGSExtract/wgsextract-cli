import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";
import { chromium } from "playwright";

const { values } = parseArgs({ options: {
    url: { type: "string" }, fixtures: { type: "string" }, out: { type: "string" },
    "large-bam": { type: "string" }, vcf: { type: "string" }, chromium: { type: "string" },
    config: { type: "string" },
} });
if (!values.url || !values.fixtures || !values.out || !values["large-bam"] || !values.vcf) {
    throw new Error("Usage: --url URL --fixtures DIR --large-bam PATH --vcf PATH --out DIR [--chromium PATH]");
}
const url = new URL(values.url);
assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(url.hostname));
const out = path.resolve(values.out);
if (values.config) assert.ok(path.resolve(values.config).startsWith(`${out}${path.sep}`), "Only an isolated output-directory config may be changed.");
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ executablePath: values.chromium, headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
const page = await context.newPage();
const errors = [];
const external = [];
const byteLog = [];
const proofs = [];
let current = "";
let lastGrant;
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
        byteLog.push({ case: current, fileID: pathname, status: response.status(), range: headers["content-range"] ?? "", bytes: Number(headers["content-length"] ?? 0) });
    }
});
async function checkDrawing(target, name) {
    await target.locator("[data-viewer-status]").filter({ hasText: "Matched local reference: hs38DH.fa.gz." }).waitFor({ timeout: 60_000 });
    await target.locator(".igv-track-label").filter({ hasText: name }).waitFor();
    const canvases = await target.locator("[data-igv] canvas").evaluateAll((items) => items.filter((canvas) => {
        if (!canvas.width || !canvas.height || !canvas.getBoundingClientRect().width) return false;
        return canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data.some((value, i) => i % 4 === 3 && value > 0);
    }).length);
    assert.ok(canvases >= 2, "Actual indexed track and sequence/ruler must draw.");
}
try {
    await page.goto(new URL("/viewer", url).href);
    const cases = [
        ["bam", path.join(values.fixtures, "reads.bam")],
        ["cram", path.join(values.fixtures, "reads.cram")],
        ["vcf", values.vcf],
        ["large-real-bam", values["large-bam"]],
    ];
    for (const [label, file] of cases) {
        current = label;
        await page.locator('[name="library-path"]').fill(path.resolve(file));
        await page.locator('[data-library] [name="locus"]').fill("chr20:10001200-10001450");
        await page.getByRole("button", { name: "Open library file", exact: true }).click();
        await checkDrawing(page, path.basename(file));
        await page.screenshot({ path: path.join(out, `library-${label}.png`), fullPage: true });
        await page.locator('[data-locus] [name="locus"]').fill("chr20:10002400-10002700");
        await page.getByRole("button", { name: "Go", exact: true }).click();
        await page.locator("[data-viewer-status]").filter({ hasText: "Local tracks loaded" }).waitFor();
        proofs.push({ case: label, reference: lastGrant.files.reference.name, totalBytes: byteLog.filter((read) => read.case === label).reduce((sum, read) => sum + read.bytes, 0) });
    }
    assert.ok(proofs.find((proof) => proof.case === "large-real-bam").totalBytes < 16 * 1024 * 1024, "A 5 GiB real BAM locus must read bounded blocks and indexes.");
    assert.ok(byteLog.some((read) => read.status === 206), "Actual igv range requests must receive 206.");
    assert.ok(byteLog.every((read) => [200, 206].includes(read.status)));
    assert.deepEqual(await page.evaluate(() => window.policyViolations), []);
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
    await frame.locator('[data-locus] [name="locus"]').fill("chr20:10001200-10001450");
    await frame.getByRole("button", { name: "Go", exact: true }).click();
    await frame.locator("[data-viewer-status]").filter({ hasText: "Local tracks loaded" }).waitFor();
    await page.screenshot({ path: path.join(out, "library-entry-point.png"), fullPage: true });
    assert.deepEqual(await frame.locator("body").evaluate(() => window.policyViolations), []);
    const closed = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/viewer/close");
    await page.getByRole("button", { name: "Back to WGS Extract", exact: true }).click();
    await closed;
    const revoked = await context.request.get(new URL(grant.files.tracks[0].url, url).href, { headers: grant.files.tracks[0].headers });
    assert.equal(revoked.status(), 401, "Closing the pane revokes its scoped byte grant.");
    assert.deepEqual(external, [], "No request leaves the loopback origin.");
    assert.deepEqual(errors, [], "No console, CSP or page errors.");
    await writeFile(path.join(out, "library-browser-proof.json"), JSON.stringify({ proofs, byteLog, external, errors, revokedStatus: revoked.status() }, null, 2));
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
