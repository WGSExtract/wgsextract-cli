import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { loadLocalizedBundle } from "../dist/web/src/server/bundle-loader.js";
import { createProcessManager } from "../dist/web/src/server/process-runner.js";
import { createRequestHandler } from "../dist/web/src/server/routes.js";
import { migrateNetworkSettingsAfterCompletedSetup, readNetworkSettings } from "../dist/web/src/server/network-settings.js";
import { appSettingsPath } from "../dist/web/src/server/workspace-location.js";
import { isolateWorkspaceEnvironment } from "./workspace-fixture.mjs";

const app = path.resolve(import.meta.dirname, "..");
const executablePath = process.platform === "linux" && existsSync("/usr/bin/chromium")
    ? "/usr/bin/chromium" : chromium.executablePath();
const operationTimeout = 6_000;

async function within(promise, description) {
    let timer;
    try {
        return await Promise.race([promise, new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error(`${description} timed out after ${operationTimeout} ms`)), operationTimeout);
        })]);
    } finally {
        clearTimeout(timer);
    }
}

async function isolatedRoot(t) {
    const root = await mkdtemp(path.join(tmpdir(), "net-consent-"));
    isolateWorkspaceEnvironment(t, root);
    t.after(() => rm(root, { recursive: true, force: true }));
    return root;
}

async function browserFixture(t) {
    const bundleRoot = await mkdtemp(path.join(tmpdir(), "net-consent-"));
    const pendingRequests = new Set();
    const manager = createProcessManager({ maxOutputBytes: 1048576, maxErrorBytes: 65536 });
    const server = createServer();
    let browserServer;
    t.after(async () => {
        try {
            if (browserServer) {
                try {
                    await within(browserServer.close(), "Closing Chromium");
                } catch (error) {
                    await within(browserServer.kill(), "Killing Chromium");
                    throw error;
                }
            }
        } finally {
            if (server.listening) {
                const closed = new Promise((resolve) => server.close(resolve));
                server.closeAllConnections();
                await within(closed, "Closing the fixture HTTP server");
            }
            await within(manager.terminateAllProcesses(), "Stopping setup processes");
            await within(Promise.allSettled(pendingRequests), "Draining fixture requests");
            await rm(bundleRoot, { recursive: true, force: true });
        }
    });
    isolateWorkspaceEnvironment(t, bundleRoot);
    await writeFile(path.join(bundleRoot, "manifest.json"), JSON.stringify({
        id: "wgs-extract", displayName: "WGS Extract", summary: "Desktop consent fixture.",
        setup: { steps: [{ id: "bootstrap", kind: "pathTool", label: "Bootstrap fixture", value: process.execPath }] },
        pages: [
            { id: "home", title: "Home", summary: "Available offline.", sections: [{ id: "main" }] },
            { id: "settings", title: "Settings", summary: "App preferences.", sections: [{ id: "settings" }] },
        ],
    }));
    const repoRoot = path.dirname(app);
    const load = (locale) => loadLocalizedBundle(locale, repoRoot, bundleRoot, bundleRoot);
    const loaded = await load();
    const handle = createRequestHandler({
        ...loaded, apiToken: "browser-consent-test", repoRoot, maxBodyBytes: 1048576,
        distRoot: path.join(app, "dist"), webRoot: path.join(app, "web"), bundleRoot,
        dynamicActions: new Map(), localizedBundleLoader: { load }, runProcess: manager.runProcess,
    });
    server.on("request", (request, response) => {
        const pending = handle(request, response);
        pendingRequests.add(pending);
        void pending.finally(() => pendingRequests.delete(pending));
    });
    await within(new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", resolve);
    }), "Starting the fixture HTTP server");
    browserServer = await chromium.launchServer({ executablePath, timeout: operationTimeout });
    const browser = await chromium.connect(browserServer.wsEndpoint(), { timeout: operationTimeout });
    const page = await within(browser.newPage({ viewport: { width: 1060, height: 780 } }), "Opening a Chromium page");
    page.setDefaultTimeout(operationTimeout);
    const origin = `http://127.0.0.1:${server.address().port}`;
    const setupCalls = [], external = [];
    await page.route((url) => url.origin !== origin, (route) => {
        external.push(route.request().url());
        return route.abort();
    });
    page.on("request", (request) => {
        const url = new URL(request.url());
        if (url.origin === origin && url.pathname === "/api/setup/stream") setupCalls.push(url.pathname);
    });
    await page.addInitScript(() => {
        window.WGSEXTRACT_GUI_AUTO_UPDATE = true;
        window.updateRequests = [];
        window.__TAURI__ = {
            event: { listen: async () => {
                document.documentElement.dataset.testUpdaterReady = "true";
            } },
            core: { invoke: async (command) => {
                if (command.startsWith("gfc_update_")) window.updateRequests.push(command);
                if (command === "gfc_update_check") document.documentElement.dataset.testStartupUpdate = "true";
                return { currentVersion: "0.8.10" };
            } },
        };
    });
    await page.goto(origin, { waitUntil: "domcontentloaded" });
    await page.locator("html[data-test-updater-ready]").waitFor({ state: "attached" });
    return {
        page, external, setupCalls,
        updates: () => page.evaluate(() => window.updateRequests),
        startupUpdate: () => page.locator("html[data-test-startup-update]").waitFor({ state: "attached" }),
    };
}

const browserTestOptions = { skip: !existsSync(executablePath) && "Requires installed Chromium.", timeout: 30_000 };

test("#1591 first launch makes no outbound requests until OK, then starts setup and checks for updates", browserTestOptions, async (t) => {
    const { page, external, setupCalls, updates, startupUpdate } = await browserFixture(t);
    const dialog = page.getByRole("dialog", { name: "Network & Privacy" });
    const ok = dialog.getByRole("button", { name: "OK", exact: true });
    await ok.waitFor();
    assert.equal(await ok.evaluate((button) => document.activeElement === button), true);
    await page.evaluate(() => window.dispatchEvent(new Event("wgsextract-gui-check-for-updates")));
    assert.deepEqual({ external, setupCalls, updates: await updates() }, { external: [], setupCalls: [], updates: [] });

    const [setup] = await Promise.all([
        page.waitForResponse((response) => response.url().endsWith("/api/setup/stream")),
        startupUpdate(),
        ok.press("Enter"),
    ]);
    assert.equal(setup.status(), 200);
    // This test checks that setup starts. Teardown aborts and drains the stream.
    assert.equal(await page.getByRole("checkbox", { name: "Check for updates at launch" }).isChecked(), true);
    assert.deepEqual(external, []);
});

test("#1591 Not now opens Settings offline and saves an independent launch preference", browserTestOptions, async (t) => {
    const { page, external, setupCalls, updates, startupUpdate } = await browserFixture(t);
    await page.getByRole("button", { name: "Not now" }).click();
    const section = page.getByRole("region", { name: "Network & Privacy" });
    await section.waitFor();
    assert.equal(await section.evaluate((element) => document.activeElement === element), true);
    const checkbox = section.getByRole("checkbox", { name: "Check for updates at launch" });
    assert.equal(await checkbox.isChecked(), false);
    await page.getByRole("button", { name: "Home" }).click();
    await page.getByText("Available offline.").waitFor();
    assert.deepEqual({ external, setupCalls, updates: await updates() }, { external: [], setupCalls: [], updates: [] });
    await page.evaluate(() => window.dispatchEvent(new Event("wgsextract-gui-check-for-updates")));
    assert.deepEqual(await updates(), ["gfc_update_check_from_menu"]);

    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "Settings" }).click();
    assert.equal(await checkbox.isChecked(), false);
    assert.deepEqual(await updates(), []);
    const save = page.waitForResponse((response) => response.url().endsWith("/api/network-settings"));
    await checkbox.check();
    assert.equal((await save).status(), 200);
    await page.reload();
    await checkbox.waitFor();
    await startupUpdate();
    assert.deepEqual(setupCalls, []);
});

test("#1591 Not now does not start networking when another window already saved OK", browserTestOptions, async (t) => {
    const previous = process.env.WGSEXTRACT_GUI_AUTO_RUN_SETUP;
    process.env.WGSEXTRACT_GUI_AUTO_RUN_SETUP = "1";
    t.after(() => {
        if (previous === undefined) delete process.env.WGSEXTRACT_GUI_AUTO_RUN_SETUP;
        else process.env.WGSEXTRACT_GUI_AUTO_RUN_SETUP = previous;
    });
    const { page, external, setupCalls, updates } = await browserFixture(t);
    const saved = await page.request.post(new URL("/api/network-settings", page.url()).href, {
        headers: { "x-gfc-token": "browser-consent-test" },
        data: { consent: "accepted" },
    });
    assert.equal(saved.ok(), true);
    await page.getByRole("button", { name: "Not now" }).click();
    await page.getByRole("region", { name: "Network & Privacy" }).waitFor();
    const localized = page.waitForResponse((response) => response.url().endsWith("/api/manifest?locale=en"));
    await page.locator("[data-locale-picker]").selectOption("en");
    assert.equal((await localized).status(), 200);
    await page.waitForLoadState("networkidle");
    assert.deepEqual({ external, setupCalls, updates: await updates() }, { external: [], setupCalls: [], updates: [] });
});

test("#1591 a completed installation's previous effective startup preference survives migration", async (t) => {
    await isolatedRoot(t);
    const file = appSettingsPath();
    await mkdir(path.dirname(file), { recursive: true });
    await writeFile(file, JSON.stringify({ privacyMode: true }));
    await migrateNetworkSettingsAfterCompletedSetup();
    assert.deepEqual(await readNetworkSettings(), { consent: "accepted", privacyMode: true, checkForUpdatesAtLaunch: false });
    await writeFile(file, "{}");
    await migrateNetworkSettingsAfterCompletedSetup();
    assert.equal((await readNetworkSettings()).checkForUpdatesAtLaunch, true);
    await writeFile(file, JSON.stringify({ networkConsent: "deferred" }));
    await migrateNetworkSettingsAfterCompletedSetup();
    assert.equal((await readNetworkSettings()).consent, "deferred");
});
