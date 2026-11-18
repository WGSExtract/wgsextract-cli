import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";
import { chromium } from "playwright";

const { values } = parseArgs({
  options: {
    url: { type: "string" },
    fixtures: { type: "string" },
    out: { type: "string" },
    chromium: { type: "string" },
  },
});
if (!values.url || !values.fixtures || !values.out) {
  throw new Error("Usage: node scripts/verify-genome-viewer.mjs --url URL --fixtures DIR --out DIR [--chromium PATH]");
}
const url = new URL(values.url);
assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(url.hostname), "Verification must use a loopback app server.");
const fixtures = path.resolve(values.fixtures);
const output = path.resolve(values.out);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ ...(values.chromium ? { executablePath: values.chromium } : {}), headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
const page = await context.newPage();
const externalRequests = [];
const consoleMessages = [];
const requests = [];
try {
  await context.route("**/*", (route) => {
    if (new URL(route.request().url()).origin !== url.origin) {
      externalRequests.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  page.on("console", (message) => consoleMessages.push({ type: message.type(), text: message.text() }));
  page.on("pageerror", (error) => consoleMessages.push({ type: "pageerror", text: error.message }));
  page.on("request", (request) => requests.push({ url: request.url(), method: request.method(), body: request.postData() }));
  await page.addInitScript(() => {
    window.viewerFileReads = [];
    window.viewerPolicyViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => window.viewerPolicyViolations.push(event.blockedURI));
    const slice = Blob.prototype.slice;
    Blob.prototype.slice = function (start, end, type) {
      const result = slice.call(this, start, end, type);
      if (this instanceof File) {
        window.viewerFileReads.push({ name: this.name, total: this.size, start, end, bytes: result.size });
      }
      return result;
    };
  });
  await page.goto(new URL("/viewer?locale=en&genome=hg38&file=https://example.com/never.bam", url).href);
  const status = page.locator("[data-viewer-status]");
  await page.locator('[data-files] [name="locus"]').fill("chr20:1200-1450");
  await page.getByRole("button", { name: "Open local files", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Reference not installed" }).waitFor();
  await page.screenshot({ path: path.join(output, "viewer-missing-reference.png"), fullPage: true });

  await page.locator('[name="reference"]').setInputFiles(["reference.fa", "reference.fa.fai"].map((name) => path.join(fixtures, name)));
  const variants = ["variants.vcf.gz", "variants.vcf.gz.tbi"];
  for (const [label, tracks] of [
    ["bam", ["reads.bam", "reads.bam.bai", ...variants]],
    ["cram", ["reads.cram", "reads.cram.crai", ...variants]],
    ["large-bam", ["large.bam", "large.bam.bai"]],
  ]) {
    await page.locator('[name="tracks"]').setInputFiles(tracks.map((name) => path.join(fixtures, name)));
    await page.getByRole("button", { name: "Open local files", exact: true }).click();
    await status.filter({ hasText: "Local tracks loaded" }).waitFor({ timeout: 60_000 });
    await page.locator(".igv-track-label").filter({ hasText: tracks[0] }).waitFor();
    if (label !== "large-bam") {
      await page.locator(".igv-track-label").filter({ hasText: "variants.vcf.gz" }).waitFor();
    }
    // Visible non-empty canvases prove actual drawing, not merely a successful library import.
    const drawnCanvases = await page.locator("[data-igv] canvas").evaluateAll((canvases) => canvases.filter((canvas) => {
      if (canvas.width === 0 || canvas.height === 0 || canvas.getBoundingClientRect().width === 0) {
        return false;
      }
      const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
      return data.some((value, index) => index % 4 === 3 && value > 0);
    }).length);
    assert.ok(drawnCanvases >= 3, `${label}: reads, coverage and ruler should be rendered.`);
    await page.screenshot({ path: path.join(output, `viewer-${label}.png`), fullPage: true });
    await page.locator('[data-locus] [name="locus"]').fill("rs429358");
    await page.getByRole("button", { name: "Go", exact: true }).click();
    await status.filter({ hasText: "Gene and rsID search are not available" }).waitFor();
    await page.locator('[data-locus] [name="locus"]').fill("chrMissing:1-100");
    await page.getByRole("button", { name: "Go", exact: true }).click();
    await status.filter({ hasText: "Position not found in the selected reference" }).waitFor();
    await page.locator('[data-locus] [name="locus"]').fill("chr20:2400-2700");
    await page.getByRole("button", { name: "Go", exact: true }).click();
    await status.filter({ hasText: "Local tracks loaded" }).waitFor();
    await page.locator("[data-igv] canvas").first().click({ button: "right" });
    assert.equal(await page.getByText("BLAT visible sequence", { exact: true }).count(), 0);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, "viewer-narrow.png"), fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, "The page should fit a narrow viewport.");
  const reads = await page.evaluate(() => window.viewerFileReads);
  const largeReads = reads.filter((read) => read.name === "large.bam");
  assert.ok(largeReads.length > 0, "Record actual file slicing for the large BAM.");
  const largeBytes = largeReads.reduce((sum, read) => sum + read.bytes, 0);
  assert.ok(largeBytes < 2 * 1024 * 1024, `A locus must not read the 4 GiB BAM whole: read ${largeBytes} bytes.`);
  assert.deepEqual(externalRequests, [], "No request may leave the app origin.");
  assert.deepEqual(await page.evaluate(() => window.viewerPolicyViolations), [], "No attempted outside connection may be hidden by CSP.");
  assert.deepEqual(requests.filter((request) => request.method !== "GET"), [], "No sample data may be uploaded.");
  assert.deepEqual(consoleMessages.filter((message) => ["error", "pageerror"].includes(message.type)), [], "Read actual browser console errors.");
  await writeFile(path.join(output, "viewer-browser-proof.json"), JSON.stringify({ externalRequests, requests, consoleMessages, largeBytes, reads }, null, 2));
  console.log(`BAM, CRAM, VCF and 4 GiB sparse-BAM checks passed; large BAM read ${largeBytes} bytes; no external requests or console errors.`);
} catch (error) {
  await page.screenshot({ path: path.join(output, "viewer-failure.png"), fullPage: true });
  await writeFile(path.join(output, "viewer-failure.html"), await page.content());
  throw error;
} finally {
  await writeFile(path.join(output, "viewer-console.json"), JSON.stringify({ consoleMessages, externalRequests }, null, 2));
  await context.close();
  await browser.close();
}
