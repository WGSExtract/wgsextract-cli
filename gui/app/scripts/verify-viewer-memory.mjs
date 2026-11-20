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
const launched = chromium.launch({ executablePath: values.chromium, headless: true });
const cancel = async () => {
  await (await launched).close();
  process.exit(143);
};
process.once("SIGTERM", cancel);
process.once("SIGINT", cancel);
const browser = await launched;
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
      const loadTrack = instance.loadTrack.bind(instance);
      instance.loadTrack = config => {
        // Tiny CRAMs fit in the header byte prefetch. The public cache option
        // ensures the first alignment search has a real range read to suspend.
        return loadTrack({ ...config, cacheFetches: false });
      };
      const search = instance.search.bind(instance);
      window.pendingSearches = 0;
      instance.search = async (...args) => {
        window.pendingSearches++;
        try { return await search(...args); } finally { window.pendingSearches--; }
      };
      const update = instance.updateViews.bind(instance);
      window.pendingUpdates = 0;
      window.completedUpdates = 0;
      instance.updateViews = async (...args) => {
        window.pendingUpdates++;
        try { return await update(...args); } finally {
          window.pendingUpdates--;
          window.completedUpdates++;
        }
      };
      return instance;
    };
  });
  const cdp = await context.newCDPSession(page);
  await cdp.send("HeapProfiler.enable");
  const loci = ["1:1-100", "1:150-250", "1:300-400", "1:450-550", "1:600-700", "1:750-850"];
  for (const format of ["bam", "cram"]) {
    await page.locator('[name="library-path"]').fill(path.join(values.fixtures, `reads.${format}`));
    await page.locator('[data-library] [name="locus"]').fill("1:20000-20100");
    await page.evaluate(() => { window.memoryBrowser = null; });
    const opened = page.waitForResponse(response => new URL(response.url()).pathname === "/api/viewer/open");
    await page.getByRole("button", { name: "Open library file", exact: true }).click();
    await page.locator("[data-viewer-status]").filter({ hasText: "Matched local reference" }).waitFor({ timeout: 60000 });
    await page.waitForFunction(() => window.memoryBrowser);
    await page.locator(".igv-track-label").filter({ hasText: `reads.${format}` }).waitFor();
    const session = await (await opened).json();
    const alignmentURL = new URL(session.files.tracks[0].url, url).href;
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    let reached;
    const blocked = new Promise(resolve => { reached = resolve; });
    let held = false;
    await page.route(alignmentURL, async route => {
      if (held || !route.request().headers().range) return route.continue();
      held = true;
      const response = await route.fetch();
      assert.equal(response.status(), 206, "Hold an actual local indexed range response.");
      reached();
      await gate;
      await route.fulfill({ response });
    });
    try {
      await page.evaluate(() => {
        window.searchAComplete = false;
        window.searchA = window.memoryBrowser.search("1:200-400").then(() => { window.searchAComplete = true; });
      });
      let timer;
      try {
        await Promise.race([blocked, new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("Search A never reached its range read.")), 10000);
        })]);
      } finally { clearTimeout(timer); }
      await page.evaluate(() => {
        window.discardedViewport = window.memoryBrowser.trackViews.find(view => view.track.type === "alignment").viewports[0];
        window.searchB = window.memoryBrowser.search("1:240-300");
      });
      await page.waitForFunction(() => !window.discardedViewport.viewportElement.isConnected);
      assert.equal(await page.evaluate(() => window.searchAComplete), false, "A remains suspended while B discards its viewport.");
      release();
      await page.evaluate(async () => { await Promise.all([window.searchA, window.searchB]); });
      assert.equal(await page.evaluate(() => window.discardedViewport.featureCache?.features.hasAlignments), true,
        "The discarded viewport's suspended read must finish decoding.");
      await checkAlignmentDrawing(page, "1:240-300");
      await page.evaluate(() => {
        delete window.searchA;
        delete window.searchB;
        delete window.discardedViewport;
      });
    } finally {
      release();
      await page.unroute(alignmentURL);
    }
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
    // Revisit the read-bearing locus, then exercise the native ruler sweep:
    // disposal must remove only obsolete observers, not the new viewport's.
    await page.locator('[data-locus] [name="locus"]').fill("1:200-400");
    await page.getByRole("button", { name: "Go", exact: true }).click();
    await page.waitForFunction(() => !window.pendingSearches);
    await checkAlignmentDrawing(page, "1:200-400");
    const completed = await page.evaluate(() => window.completedUpdates);
    const ruler = page.locator('[data-track-type="ruler"]');
    await ruler.scrollIntoViewIfNeeded();
    const bounds = await ruler.boundingBox();
    assert.ok(bounds);
    await page.mouse.move(bounds.x + bounds.width * 0.25, bounds.y + bounds.height / 2);
    await page.mouse.down();
    await page.mouse.move(bounds.x + bounds.width * 0.75, bounds.y + bounds.height / 2);
    await page.mouse.up();
    await page.waitForFunction(() => window.memoryBrowser.currentLoci() !== "1:200-400");
    await page.waitForFunction(count => window.completedUpdates > count && !window.pendingUpdates, completed);
    const sweptLocus = await page.evaluate(() => window.memoryBrowser.currentLoci());
    await checkAlignmentDrawing(page, sweptLocus);
    assert.equal(await page.getByRole("alert").count(), 0);
  }
  assert.deepEqual(external, []);
  assert.deepEqual(errors, []);
  assert.deepEqual(await page.evaluate(() => window.policyViolations), []);
} finally {
  await mkdir(values.out, { recursive: true });
  await writeFile(path.join(values.out, "memory-proof.json"), JSON.stringify({ samples, external, errors }, null, 2));
  await browser.close();
  process.off("SIGTERM", cancel);
  process.off("SIGINT", cancel);
}

async function checkAlignmentDrawing(page, locus) {
  const drawing = await page.evaluate(locus => {
    const view = window.memoryBrowser.trackViews.find(view => view.track.type === "alignment");
    const viewport = view.viewports[0];
    const canvas = viewport.canvas;
    const data = canvas?._data;
    const features = viewport.featureCache?.features;
    const frame = viewport.referenceFrame;
    if (!canvas || !data || !features || window.memoryBrowser.currentLoci() !== locus) return null;
    const start = frame.start, end = frame.calculateEnd(viewport.viewportElement.clientWidth);
    if (data.bpPerPixel !== frame.bpPerPixel || data.bpStart > start || data.bpEnd < end) return null;
    const ratio = canvas.width / data.pixelWidth;
    const x = Math.max(0, Math.ceil((start - data.bpStart) / data.bpPerPixel * ratio));
    const width = Math.min(canvas.width - x, Math.floor((end - start) / data.bpPerPixel * ratio));
    const coverageHeight = view.track.coverageTrackHeight;
    const ink = (top, height) => {
      const y = Math.max(0, Math.ceil((top - data.pixelTop) * ratio));
      const h = Math.min(canvas.height - y, Math.floor(height * ratio));
      if (width <= 0 || h <= 0) return false;
      const pixels = canvas.getContext("2d").getImageData(x, y, width, h).data;
      return pixels.some((v, i) => i % 4 === 3 && v > 0 && (pixels[i - 3] < 220 || pixels[i - 2] < 220 || pixels[i - 1] < 220));
    };
    return {
      reads: features.allAlignments().some(read => read.start < end && read.start + read.lengthOnRef > start),
      coverage: features.coverageMap?.coverage.slice(Math.max(0, Math.floor(start - features.coverageMap.bpStart)),
        Math.ceil(end - features.coverageMap.bpStart)).some(bin => bin?.total > 0),
      readPixels: ink(coverageHeight + 5, data.pixelHeight - coverageHeight - 5),
      coveragePixels: ink(0, coverageHeight),
    };
  }, locus);
  assert.ok(drawing?.reads && drawing.coverage && drawing.readPixels && drawing.coveragePixels,
    `Alignment reads and coverage must paint the current locus ${locus}: ${JSON.stringify(drawing)}`);
}
