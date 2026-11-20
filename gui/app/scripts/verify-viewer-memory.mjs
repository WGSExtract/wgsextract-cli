import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";
import { chromium } from "playwright";

// Accept the shared offline runner's arguments; only these are needed here.
const { values } = parseArgs({ strict: false, options: {
  url: { type: "string" }, fixtures: { type: "string" }, out: { type: "string" }, chromium: { type: "string" },
} });
if (!values.url || !values.fixtures || !values.out) throw new Error("Provide --url, --fixtures and --out.");
const url = new URL(values.url);
assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(url.hostname));
const browser = await chromium.launch({ executablePath: values.chromium, headless: true });
const external = [], errors = [], samples = [];
try {
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => {
    if (new URL(route.request().url()).origin !== url.origin) {
      external.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  await page.addInitScript(() => {
    window.policyViolations = [];
    document.addEventListener("securitypolicyviolation", event => window.policyViolations.push(event.blockedURI));
  });
  await page.goto(new URL("/viewer", url).href);
  await page.waitForFunction(() => window.igv?.createBrowser);
  await page.evaluate(() => {
    const create = window.igv.createBrowser;
    window.igv.createBrowser = async (...args) => {
      const instance = await create(...args);
      window.memoryBrowser = instance;
      const search = instance.search.bind(instance);
      window.pendingSearches = 0;
      instance.search = async (...args) => {
        window.pendingSearches++;
        try { return await search(...args); } finally { window.pendingSearches--; }
      };
      return instance;
    };
  });
  const cdp = await context.newCDPSession(page);
  await cdp.send("HeapProfiler.enable");
  const loci = ["1:1-100", "1:150-250", "1:300-400", "1:450-550", "1:600-700", "1:750-850"];
  for (const format of ["bam", "cram"]) {
    await page.locator('[name="library-path"]').fill(path.join(values.fixtures, `reads.${format}`));
    await page.locator('[data-library] [name="locus"]').fill(loci[0]);
    await page.getByRole("button", { name: "Open library file", exact: true }).click();
    await page.locator("[data-viewer-status]").filter({ hasText: "Matched local reference" }).waitFor({ timeout: 60000 });
    const heap = [];
    for (let cycle = 0; cycle < 20; cycle++) {
      for (const locus of loci) {
        await page.locator('[data-locus] [name="locus"]').fill(locus);
        await page.getByRole("button", { name: "Go", exact: true }).click();
        await page.waitForFunction(() => !window.pendingSearches && window.memoryBrowser.trackViews.every(view => !view.loading));
        assert.equal(await page.evaluate(() => window.memoryBrowser.currentLoci()), locus);
        assert.equal(await page.getByRole("alert").count(), 0);
      }
      await cdp.send("HeapProfiler.collectGarbage");
      heap.push((await cdp.send("Runtime.getHeapUsage")).usedSize);
    }
    // Warm up JIT/decoder caches for four cycles. Median windows tolerate GC/JIT
    // noise but fail the old ~220 KiB/cycle tiny-fixture leak over 96 more jumps.
    const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
    const growth = median(heap.slice(-4)) - median(heap.slice(4, 8));
    samples.push({ format, jumps: 120, heap, growth });
    assert.ok(growth < 1024 * 1024, `#1158 ${format}: retained heap grew ${growth} bytes after warm-up.`);
    // Public igv searches can overlap (rapid Go submissions also use this API).
    // Releasing obsolete observers must not null fields a pending read uses.
    await page.evaluate(async loci => {
      await Promise.all(loci.map(locus => window.memoryBrowser.search(locus)));
    }, loci);
    assert.equal(await page.getByRole("alert").count(), 0);
    // Revisit the read-bearing locus, then exercise the native ruler sweep:
    // disposal must remove only obsolete observers, not the new viewport's.
    await page.locator('[data-locus] [name="locus"]').fill("1:200-400");
    await page.getByRole("button", { name: "Go", exact: true }).click();
    await page.waitForFunction(() => !window.pendingSearches);
    const ruler = page.locator('[data-track-type="ruler"]');
    await ruler.scrollIntoViewIfNeeded();
    const bounds = await ruler.boundingBox();
    assert.ok(bounds);
    await page.mouse.move(bounds.x + bounds.width * 0.25, bounds.y + bounds.height / 2);
    await page.mouse.down();
    await page.mouse.move(bounds.x + bounds.width * 0.75, bounds.y + bounds.height / 2);
    await page.mouse.up();
    await page.waitForFunction(() => window.memoryBrowser.currentLoci() !== "1:200-400");
    assert.equal(await page.getByRole("alert").count(), 0);
    const drawn = await page.locator("[data-igv] canvas").evaluateAll(canvases => canvases.filter(canvas => {
      if (!canvas.width || !canvas.height) return false;
      return canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data.some((v, i) => i % 4 === 3 && v > 0);
    }).length);
    assert.ok(drawn >= 2, "Reads/coverage and ruler still render after repeated jumps.");
  }
  assert.deepEqual(external, []);
  assert.deepEqual(errors, []);
  assert.deepEqual(await page.evaluate(() => window.policyViolations), []);
} finally {
  await mkdir(values.out, { recursive: true });
  await writeFile(path.join(values.out, "memory-proof.json"), JSON.stringify({ samples, external, errors }, null, 2));
  await browser.close();
}
