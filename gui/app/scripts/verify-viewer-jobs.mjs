import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { localizationLabels, parseTomlStrings } from "../dist/shared/localization.js";
import { createProcessManager } from "../dist/web/src/server/process-runner.js";
import { createRequestHandler } from "../dist/web/src/server/routes.js";

/** Reproduce the review regression with the real app, NDJSON stream and a real, bounded Node job. */
export async function verifyViewerJobs(browser, fixtures, output) {
  const app = fileURLToPath(new URL("../", import.meta.url));
  const root = path.join(output, "job-fixture");
  await mkdir(path.join(root, "Assets"), { recursive: true });
  await writeFile(path.join(root, "Assets/icon.png"), Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64"));
  const labels = localizationLabels(parseTomlStrings(await readFile(path.join(app, "../resources/BuiltinStrings/strings.en.toml"), "utf8")));
  const manifest = {
    id: "wgs-extract", displayName: "Viewer job regression",
    pages: [{
      id: "main", title: "Main", sections: [{
        id: "job", actions: [{
          id: "wait", title: "Start job",
          command: { executable: process.execPath, arguments: ["-e", "let n=0; const timer=setInterval(()=>{ console.log('viewer-job-tick'); if(++n===50) clearInterval(timer); },100)"] },
        }],
      }],
    }],
  };
  const processes = createProcessManager({ maxOutputBytes: 65_536, maxErrorBytes: 65_536 });
  let finish;
  const completed = new Promise((resolve) => { finish = resolve; });
  let settled = false;
  const server = createServer(createRequestHandler({
    apiToken: "viewer-job-test-token-0123456789abcdef",
    bundleRoot: root, sourceBundleRoot: root,
    repoRoot: path.resolve(app, ".."), webRoot: path.join(app, "web"), distRoot: path.join(app, "dist"),
    dynamicActions: new Map(), enableDevReload: false, manifest, jobServer: undefined,
    maxBodyBytes: 1_048_576,
    addDevReloadClient() {},
    localizedBundleLoader: {
      async load() {
        return {
          manifest, labels, localizationCode: "en", localizationOptions: [], iconMap: {},
          bundleRootPath: root, sourceRootPath: root,
          bundleState: { uiMode: "advanced" },
        };
      },
    },
    async loadBundle() { throw new Error("Not used by this test."); },
    async moveWorkspace() { throw new Error("Not used by this test."); },
    shutdown() {},
    async runProcess(executable, args, options) {
      try {
        const result = await processes.runProcess(executable, args, options);
        finish({ exitCode: result.exitCode, cancelled: options.signal?.aborted ?? false });
        return result;
      } catch (error) {
        finish({ error: String(error), cancelled: options.signal?.aborted ?? false });
        throw error;
      } finally {
        settled = true;
      }
    },
  }));
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const url = `http://127.0.0.1:${server.address().port}/`;
    await page.goto(url);
    await page.locator('[data-action-id="wait"]').click();
    await page.waitForFunction(() => document.querySelector(".terminal-log")?.textContent?.includes("viewer-job-tick"));
    assert.equal(settled, false, "The job must still be running when the viewer opens.");
    await page.getByRole("button", { name: "Genome viewer", exact: true }).click();
    const frame = page.frameLocator(".genome-viewer-shell iframe");
    await frame.getByRole("heading", { name: "Genome viewer", exact: true }).waitFor();
    assert.equal(page.url(), url, "Opening the viewer must not navigate away from the action stream.");
    await frame.locator('[name="reference"]').setInputFiles(["reference.fa", "reference.fa.fai"].map((name) => path.join(fixtures, name)));
    await frame.locator('[name="tracks"]').setInputFiles(["reads.bam", "reads.bam.bai", "variants.vcf.gz", "variants.vcf.gz.tbi"].map((name) => path.join(fixtures, name)));
    await frame.locator('[data-files] [name="locus"]').fill("chr20:1200-1450");
    await frame.getByRole("button", { name: "Open local files", exact: true }).click();
    await frame.locator("[data-viewer-status]").filter({ hasText: "Local tracks loaded" }).waitFor();
    const result = await completed;
    assert.deepEqual(result, { exitCode: 0, cancelled: false }, "The real job should finish normally while the viewer is open.");
    await frame.locator("[data-viewer-status]").filter({ hasText: "Local tracks loaded" }).waitFor();
    assert.equal(await frame.locator('[name="tracks"]').evaluate((input) => input.files.length), 4, "Job-completion renders must not reload the viewer frame.");
    await page.screenshot({ path: path.join(output, "viewer-in-app.png"), fullPage: true });
    await page.getByRole("button", { name: "Back to WGS Extract", exact: true }).click();
    await page.locator('[data-action-id="wait"]').waitFor();
    assert.equal(await page.locator(".genome-viewer-shell").count(), 0, "Returning closes the selected files.");
    await page.waitForFunction(() => (document.querySelector(".terminal-log")?.textContent?.match(/viewer-job-tick/g) ?? []).length >= 50);
    assert.deepEqual(errors, [], "Read actual browser console errors from the in-app flow.");
    await writeFile(path.join(output, "viewer-job-proof.json"), JSON.stringify({ ...result, ticks: 50, errors }, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(output, "viewer-job-failure.png"), fullPage: true });
    await writeFile(path.join(output, "viewer-job-errors.json"), JSON.stringify(errors, null, 2));
    throw error;
  } finally {
    await context.close();
    await processes.terminateAllProcesses();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
}
