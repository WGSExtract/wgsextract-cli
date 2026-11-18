import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";

export function prepareAdversarialVCF(fixtures, bcftools) {
  const payload = '<meta http-equiv="refresh" content="0;url=https://example.invalid/viewer-adversarial"><img src="https://example.invalid/pixel">';
  const vcf = `##fileformat=VCFv4.2
##contig=<ID=chr20,length=64444167>
##INFO=<ID=NOTE,Number=1,Type=String,Description="Untrusted file text">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
chr20\t10001250\tviewer-adversarial\tA\tC\t60\tPASS\tNOTE=${encodeURIComponent(payload)}
`;
  const target = path.join(fixtures, "adversarial.gvcf.bgz");
  execFileSync(bcftools, ["view", "-Oz", "-o", target, "-"], { input: vcf });
  execFileSync(bcftools, ["index", "--tbi", "--force", target]);
}

/** Observe the real pinned browser without adding a production debug API or replacing popup methods. */
export async function observeViewer(page) {
  await page.addInitScript(() => {
    let runtime;
    Object.defineProperty(window, "igv", {
      configurable: true,
      get: () => runtime,
      set(value) {
        runtime = value;
        const create = value.createBrowser;
        value.createBrowser = async (...args) => {
          const browser = await create(...args);
          window.viewerBrowser = browser;
          window.viewerPopupFields = [];
          const fireEvent = browser.fireEvent;
          browser.fireEvent = function (event, args, ...rest) {
            if (event === "trackclick") window.viewerPopupFields.push(args[1]);
            return fireEvent.call(this, event, args, ...rest);
          };
          return browser;
        };
      },
    });
  });
}

export async function verifyViewerPopups(page, fixtures, output, label) {
  // Find an actual mismatching read/base from the decoded HG002 alignments, then click the canvas.
  const targets = await page.evaluate(() => {
    const track = window.viewerBrowser.trackViews.find((view) => view.track.type === "alignment");
    const viewport = track.viewports[0];
    const { referenceFrame: frame, cachedFeatures: features } = viewport;
    const { coverageMap } = features;
    const canvas = viewport.viewportElement.getBoundingClientRect();
    const start = Math.ceil(frame.start) + 2;
    const end = Math.floor(frame.start + canvas.width * frame.bpPerPixel) - 2;
    const alignment = track.track.alignmentTrack;
    for (let position = start; position < end; position++) {
      const reference = coverageMap.refSeq[position - coverageMap.bpStart]?.toUpperCase();
      if (!"ACGT".includes(reference)) continue;
      const x = (position + 0.5 - frame.start) / frame.bpPerPixel;
      for (let y = track.track.coverageTrackHeight + 4; y < Math.min(canvas.height, 280); y += 2) {
        const state = { genomicLocation: position, y: y + viewport.contentTop, viewport, referenceFrame: frame };
        const read = alignment.getClickedObject(state);
        const base = read?.readBaseAt?.(position);
        const insertion = read?.insertions?.some((entry) => position === entry.start || position === entry.start - 1);
        if (base && "ACGT".includes(base) && base !== reference && !insertion) {
          return {
            position, reference, base, readName: read.readName,
            read: { x: canvas.x + x, y: canvas.y + y },
            coverage: { x: canvas.x + x, y: canvas.y + track.track.coverageTrackHeight - 8 },
          };
        }
      }
    }
    throw new Error("HG002 fixture must contain a visible mismatch.");
  });
  await page.mouse.click(targets.read.x, targets.read.y);
  await revealPopup(page, page.locator(".igv-ui-popover").filter({ hasText: "Read Name" }));
  await page.screenshot({ path: path.join(output, `viewer-${label}-read-popup.png`), fullPage: true });
  await page.locator(".igv-ui-popover > div:first-child > div:last-child").click();
  await page.mouse.click(targets.coverage.x, targets.coverage.y);
  await page.locator(".igv-ui-popover").filter({ hasText: "Total Count" }).waitFor();
  await page.screenshot({ path: path.join(output, `viewer-${label}-coverage-popup.png`), fullPage: true });
  await page.locator(".igv-ui-popover > div:first-child > div:last-child").click();
  assert.ok(await page.evaluate(() => window.viewerPopupFields.length >= 2), "Real read and coverage clicks must reach popup rendering.");

  await page.locator('[data-locus] [name="locus"]').fill("chr20:10002000-10002300");
  await page.getByRole("button", { name: "Go", exact: true }).click();
  await page.locator("[data-viewer-status]").filter({ hasText: "Local tracks loaded" }).waitFor();
  await clickVariant(page, 10002076);
  await page.waitForFunction(() => JSON.stringify(window.viewerPopupFields.at(-1)).replaceAll(",", "").includes("10002077"));
  await revealPopup(page, page.locator(".igv-ui-popover"));
  await page.screenshot({ path: path.join(output, `viewer-${label}-giab-variant-popup.png`), fullPage: true });

  await page.locator('[name="tracks"]').setInputFiles(["adversarial.gvcf.bgz", "adversarial.gvcf.bgz.tbi"].map((name) => path.join(fixtures, name)));
  await page.getByRole("button", { name: "Open local files", exact: true }).click();
  await page.locator("[data-viewer-status]").filter({ hasText: "Local tracks loaded" }).waitFor();
  await clickVariant(page, 10001249);
  const popup = page.locator(".igv-ui-popover").filter({ hasText: "viewer-adversarial" });
  await revealPopup(page, popup);
  assert.match(await popup.innerText(), /<meta http-equiv="refresh"/, "Decoded VCF INFO must appear literally as text.");
  assert.equal(await popup.locator("meta, img, script, iframe, a").count(), 0, "No VCF field may create active HTML.");
  await popup.getByText("NOTE:", { exact: false }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, `viewer-${label}-adversarial-popup.png`), fullPage: true });
  assert.ok(await page.evaluate(() => window.viewerPopupFields.some((fields) =>
    fields.some((field) => String(field.value ?? "").includes('<meta http-equiv="refresh"')))),
  "The crafted INFO must pass through igv's actual VCF parser and trackclick.");
  return targets;
}

async function clickVariant(page, position) {
  const variant = await page.evaluate((position) => {
    const view = window.viewerBrowser.trackViews.find((entry) => entry.track.type === "variant");
    const viewport = view.viewports[0];
    const { referenceFrame: frame } = viewport;
    const canvas = viewport.viewportElement.getBoundingClientRect();
    return { x: canvas.x + (position + 0.5 - frame.start) / frame.bpPerPixel, y: canvas.y + 15 };
  }, position);
  await page.mouse.click(variant.x, variant.y);
}

async function revealPopup(page, popup) {
  await popup.waitFor();
  const box = await popup.boundingBox();
  const host = await page.locator("[data-igv]").boundingBox();
  // Use igv's real draggable header to bring a low-track popup into the plot for the evidence capture.
  await page.mouse.move(box.x + 25, box.y + 10);
  await page.mouse.down();
  await page.mouse.move(box.x + 25, host.y + 10, { steps: 5 });
  await page.mouse.up();
}
