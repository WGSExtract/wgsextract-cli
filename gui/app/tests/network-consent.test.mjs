import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { completeLifecycleSetup } from "../../scripts/ui-driver-setup.mjs";
import { effectiveNetworkMode, networkAllowed } from "../dist/shared/network-settings.js";
import { loadLocalizedBundle } from "../dist/web/src/server/bundle-loader.js";
import { createProcessManager } from "../dist/web/src/server/process-runner.js";
import { createRequestHandler } from "../dist/web/src/server/routes.js";
import { migrateNetworkSettingsAfterCompletedSetup, readNetworkSettings, saveNetworkSettings } from "../dist/web/src/server/network-settings.js";
import { createNetworkCostDetector, systemNetworkCost } from "../dist/web/src/server/network-cost/detector.js";
import { withCliNetworkPolicy } from "../dist/web/src/server/cli-network-policy.js";
import { appSettingsPath } from "../dist/web/src/server/workspace-location.js";
import { isolateWorkspaceEnvironment } from "./workspace-fixture.mjs";

const app = path.resolve(import.meta.dirname, "..");
const executablePath = process.platform === "linux" && existsSync("/usr/bin/chromium")
    ? "/usr/bin/chromium" : chromium.executablePath();
// Match the Playwright launch default used by the viewer tests, independently of page waits.
const browserStartupTimeout = 30_000;
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

async function browserFixture(t, { cost = "unconstrained", cli = false } = {}) {
    t.mock.method(systemNetworkCost, "detect", async () => cost);
    const bundleRoot = await mkdtemp(path.join(tmpdir(), "net-consent-"));
    const pendingRequests = new Set();
    const manager = createProcessManager({ maxOutputBytes: 1048576, maxErrorBytes: 65536 });
    const server = createServer();
    let browserContext;
    t.after(async () => {
        try {
            if (browserContext) await within(browserContext.close(), "Closing Chromium");
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
    if (cli) {
        const posix = path.join(bundleRoot, "scripts", "posix");
        const windows = path.join(bundleRoot, "scripts", "windows");
        await mkdir(posix, { recursive: true });
        await mkdir(windows, { recursive: true });
        await writeFile(path.join(posix, "run-wgsextract.sh"), 'case " $* " in *" --network low "*) echo CLI_NETWORK_LOW;; *) exit 2;; esac\n');
        await writeFile(path.join(windows, "run-wgsextract.ps1"), 'if (($args -join " ") -match "(^| )--network low( |$)") { Write-Output CLI_NETWORK_LOW } else { exit 2 }\n');
    }
    await writeFile(path.join(bundleRoot, "manifest.json"), JSON.stringify({
        id: "wgs-extract", displayName: "WGS Extract", summary: "Desktop consent fixture.",
        setup: { steps: [{ id: "bootstrap", kind: "pathTool", label: "Bootstrap fixture", value: process.execPath }] },
        pages: [
            { id: "home", title: "Home", summary: "Available offline.", sections: [{ id: "main", actions: cli ? [{
                id: "inspect", title: "Inspect fixture", command: { executable: "{{bundleRoot}}/scripts/run-wgsextract.sh", arguments: ["inspect"] },
            }] : [] }] },
            { id: "settings", title: "Settings", summary: "App preferences.", sections: [{ id: "settings" }] },
        ],
    }));
    const repoRoot = path.dirname(app);
    const load = (locale) => loadLocalizedBundle(locale, repoRoot, bundleRoot, bundleRoot);
    const loaded = await load();
    const handle = createRequestHandler({
        ...loaded, apiToken: "browser-consent-test", repoRoot, maxBodyBytes: 1048576,
        distRoot: path.join(app, "dist"), webRoot: path.join(app, "web"), bundleRoot,
        dynamicActions: new Map(), localizedBundleLoader: { load }, runProcess: withCliNetworkPolicy(manager.runProcess),
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
    // Use Chromium's first tab; don't create another renderer through a remote connection during startup.
    browserContext = await chromium.launchPersistentContext("", {
        executablePath, timeout: browserStartupTimeout, viewport: { width: 1060, height: 780 },
    });
    const [page] = browserContext.pages();
    assert.ok(page, "Chromium started without an initial page");
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

const browserTestOptions = { skip: !existsSync(executablePath) && "Requires installed Chromium.", timeout: 120_000 };

test("#1674 bootstrap accepts both the pinned CLI and the network-aware tree", async (t) => {
    const root = await isolatedRoot(t);
    const appData = path.join(root, "App Data");
    const appDir = path.relative(root, path.join(appData, "runtime", "wgsextract-cli", "app"));
    const source = path.join(root, appDir, "src", "wgsextract_cli");
    await mkdir(path.join(source, "core"), { recursive: true });
    await writeFile(path.join(source, "__init__.py"), '__version__ = "0.8.10"\n');
    await writeFile(path.join(source, "core", "network_policy.py"), "");
    const windows = process.platform === "win32";
    const extension = windows ? "ps1" : "sh";
    const script = path.join(root, `bootstrap-reference-library.${extension}`);
    const localScript = path.join(root, `run-wgsextract.${extension}`);
    const optionsModule = path.join(source, "core", "network_options.py");
    const invocation = windows
        ? ["powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]]
        : ["/bin/sh", []];
    const [executable, args] = invocation;
    await writeFile(script, windows
        ? 'if ($args.Count -ne 0) { exit 2 }; Write-Output "BOOTSTRAPPED"\n'
        : 'if [ "$#" -ne 0 ]; then exit 2; fi; echo BOOTSTRAPPED\n');
    await writeFile(localScript, windows
        ? 'if (($args -join " ") -ne "info") { exit 2 }; Write-Output "LOCAL_RESULT"\n'
        : 'if [ "$*" != info ]; then exit 2; fi; echo LOCAL_RESULT\n');
    const manager = createProcessManager({ maxOutputBytes: 1024, maxErrorBytes: 1024 });
    t.after(() => manager.terminateAllProcesses());
    const run = withCliNetworkPolicy(manager.runProcess);
    const warnings = [];
    const env = Object.fromEntries(Object.entries(process.env)
        .filter(([key]) => !["WGSEXTRACT_APP_DIR", "WGSEXTRACT_GUI_APP_DATA"].includes(key.toUpperCase())));
    const launch = (target, flags, variable, value) => run(executable, [...args, target, ...flags], {
        cwd: root, env: { ...env, [windows ? variable.toLowerCase() : variable]: value },
        onStderr: (text) => warnings.push(text),
    });
    const bootstrap = (...flags) => launch(script, flags, "WGSEXTRACT_APP_DIR", appDir);
    await saveNetworkSettings({ consent: "accepted" });
    await saveNetworkSettings({ mode: "full" });
    assert.match((await bootstrap()).stdout, /BOOTSTRAPPED/);

    await saveNetworkSettings({ mode: "low" });
    assert.match((await bootstrap("--allow-large-download")).stdout, /BOOTSTRAPPED/);
    for (const mode of ["low", "off"]) {
        await saveNetworkSettings({ mode });
        warnings.length = 0;
        const result = await launch(localScript, ["info"], "WGSEXTRACT_GUI_APP_DATA", appData);
        assert.match(result.stdout, /LOCAL_RESULT/);
        assert.match(warnings.join(""), /cannot enforce Low Data or Off.*downloads still require/);
    }

    await writeFile(optionsModule, "");
    await writeFile(script, windows
        ? 'if (($args -join " ") -eq "--allow-large-download --network low") { Write-Output "BOOTSTRAPPED" } else { exit 2 }\n'
        : 'case "$*" in "--allow-large-download --network low") echo BOOTSTRAPPED;; *) exit 2;; esac\n');
    await saveNetworkSettings({ mode: "low" });
    assert.match((await bootstrap("--allow-large-download")).stdout, /BOOTSTRAPPED/);
});

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
    await page.waitForFunction(() => window.updateRequests.includes("gfc_update_check_from_menu"));
    assert.deepEqual(await updates(), ["gfc_update_check_from_menu"]);

    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "Settings" }).click();
    assert.equal(await checkbox.isChecked(), false);
    assert.deepEqual(await updates(), []);
    const save = page.waitForResponse((response) => response.url().endsWith("/api/network-settings") && response.request().method() === "POST");
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
    t.mock.method(systemNetworkCost, "detect", async () => "unknown");
    await isolatedRoot(t);
    const file = appSettingsPath();
    await mkdir(path.dirname(file), { recursive: true });
    await writeFile(file, JSON.stringify({ privacyMode: true }));
    await migrateNetworkSettingsAfterCompletedSetup();
    assert.deepEqual(await readNetworkSettings(), { consent: "accepted", privacyMode: true, checkForUpdatesAtLaunch: false, mode: "automatic", detectedCost: "unknown" });
    await writeFile(file, "{}");
    await migrateNetworkSettingsAfterCompletedSetup();
    assert.equal((await readNetworkSettings()).checkForUpdatesAtLaunch, true);
    await writeFile(file, JSON.stringify({ networkConsent: "deferred" }));
    await migrateNetworkSettingsAfterCompletedSetup();
    assert.equal((await readNetworkSettings()).consent, "deferred");
});

test("#1591 metered first launch defers setup, reports Low in Settings, and runs the CLI in Low", browserTestOptions, async (t) => {
    const { page, external, setupCalls, startupUpdate } = await browserFixture(t, { cost: "constrained", cli: true });
    await page.getByRole("button", { name: "OK", exact: true }).click();
    await startupUpdate();
    const settings = page.getByRole("region", { name: "Network & Privacy" });
    const mode = settings.getByRole("combobox", { name: "Network mode" });
    const status = settings.getByRole("status");
    assert.equal(await mode.inputValue(), "automatic");
    assert.match(await status.textContent(), /Metered.*In use: Low/);
    assert.deepEqual({ external, setupCalls }, { external: [], setupCalls: [] });

    const run = await page.request.post(new URL("/api/run/stream", page.url()).href, {
        headers: { "x-gfc-token": "browser-consent-test" },
        data: {
            action: { pageID: "home", sectionID: "main", actionID: "inspect" },
            context: { fieldValues: {}, checkedOptions: {}, configValues: {}, rowValues: {} },
        },
    });
    assert.equal(run.status(), 200);
    assert.match(await run.text(), /CLI_NETWORK_LOW/);

    const saved = page.waitForResponse((response) => response.url().endsWith("/api/network-settings") && response.request().method() === "POST");
    await mode.selectOption("off");
    assert.equal((await saved).status(), 200);
    await status.filter({ hasText: /In use: Off/ }).waitFor();
    assert.equal(await mode.inputValue(), "off");
    assert.match(await status.textContent(), /Metered.*In use: Off/);
    const persisted = await page.request.get(new URL("/api/network-settings", page.url()).href, {
        headers: { "x-gfc-token": "browser-consent-test" },
    });
    assert.equal((await persisted.json()).mode, "off");
});

test("#1591 maps local OS cost signals, not connectivity, to constrained", async () => {
    const cases = [
        ["darwin", [["true false", "constrained"], ["false true", "constrained"], ["false false", "unconstrained"], ["", "unknown"]]],
        ["win32", [["Fixed False", "constrained"], ["Variable False", "constrained"], ["Unrestricted True", "constrained"], ["Unrestricted False", "unconstrained"], ["Unknown False", "unknown"]]],
        ["linux", [
            ["GENERAL.TYPE:wifi\nGENERAL.STATE:100 (connected)\nGENERAL.METERED:yes (guessed)", "constrained"],
            ["GENERAL.TYPE:ethernet\nGENERAL.STATE:100 (connected)\nGENERAL.METERED:no", "unconstrained"],
            ["GENERAL.TYPE:loopback\nGENERAL.STATE:100 (connected)\nGENERAL.METERED:no\n\nGENERAL.TYPE:wifi\nGENERAL.STATE:30 (disconnected)\nGENERAL.METERED:yes", "unknown"],
        ]],
    ];
    for (const [platform, signals] of cases) {
        for (const [output, expected] of signals) {
            const detect = createNetworkCostDetector(platform, async () => output, "/native");
            assert.equal(await detect(), expected, `${platform}: ${output}`);
        }
    }
    assert.equal(await createNetworkCostDetector("linux", async () => { throw new Error("NetworkManager unavailable"); })(), "unknown");
});

test("#1591 an unmetered interface cannot hide another active interface's unknown cost", async () => {
    const output = (cost) => [
        "GENERAL.TYPE:bridge\nGENERAL.STATE:100 (connected)\nGENERAL.METERED:no",
        "GENERAL.TYPE:wifi\nGENERAL.STATE:100 (connected)\nGENERAL.METERED:unknown",
        `GENERAL.TYPE:ethernet\nGENERAL.STATE:100 (connected)\nGENERAL.METERED:${cost}`,
    ].join("\n\n");
    assert.equal(await createNetworkCostDetector("linux", async () => output("no"))(), "unknown");
    assert.equal(await createNetworkCostDetector("linux", async () => output("yes"))(), "constrained");
});

test("#1591 the lifecycle walkthrough confirms manual setup on an unknown-cost connection", browserTestOptions, async (t) => {
    const { page, setupCalls } = await browserFixture(t, { cost: "unknown" });
    const prompt = page.getByRole("button", { name: "OK", exact: true });

    await completeLifecycleSetup(page, prompt, operationTimeout);

    assert.equal(setupCalls.length, 1);
    assert.equal(await page.locator(".setup-global-banner").count(), 0);
});

test("#1591 unavailable OS detection leaves bulk startup transfers opt-in", async () => {
    const detectedCost = await createNetworkCostDetector("darwin", async () => "false false")();
    const settings = { consent: "accepted", privacyMode: false, checkForUpdatesAtLaunch: true, mode: "automatic", detectedCost };
    assert.equal(effectiveNetworkMode(settings), "low");
    assert.equal(networkAllowed(settings, "startup-setup"), false);
    assert.equal(networkAllowed(settings, "startup-download"), false);
    assert.equal(networkAllowed({ ...settings, mode: "full" }, "startup-setup"), true);
});
