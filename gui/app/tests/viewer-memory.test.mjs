import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { runViewerVerification } from "../scripts/viewer-verification-process.mjs";

const root = path.resolve(import.meta.dirname, "../../..");
const tools = path.join(root, ".pixi/envs/default", process.platform === "win32" ? "Library/bin" : "bin");
const systemChromium = process.platform === "linux" && existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : chromium.executablePath();
const available = existsSync(systemChromium)
  && ["samtools", "bcftools", "bgzip", "tabix"].every(name => existsSync(path.join(tools, `${name}${process.platform === "win32" ? ".exe" : ""}`)));

test("#1158: offline Chromium heap stays bounded across repeated BAM/CRAM locus jumps", {
  skip: !available ? "Local browser regression needs installed Chromium and the locked default Pixi fixture tools." : false,
  timeout: 190000,
}, async t => {
  mkdirSync(path.join(root, "out"), { recursive: true });
  const output = mkdtempSync(path.join(root, "out/m-"));
  let passed = false;
  try {
    await runViewerVerification([
      path.join(root, "gui/app/scripts/verify-viewer-offline.mjs"),
      "--memory", "--out", output, "--chromium", systemChromium,
    ], { cwd: root, signal: t.signal });
    passed = true;
  } finally {
    if (passed) rmSync(output, { recursive: true, force: true });
  }
});

test("#1158: cancellation stops Chromium even when its verifier cannot handle signals", {
  skip: !available || process.platform === "win32" ? "Requires installed Chromium and POSIX process groups." : false,
  timeout: 20000,
}, async () => {
  mkdirSync(path.join(root, "out"), { recursive: true });
  const output = mkdtempSync(path.join(root, "out/m-hung-"));
  const script = path.join(output, "verifier.mjs");
  const pidFile = path.join(output, "browser-pid");
  writeFileSync(script, `
    import { chromium } from ${JSON.stringify(path.join(root, "gui/app/node_modules/playwright/index.mjs"))};
    import { writeFileSync } from "node:fs";
    const browser = await chromium.launch({ executablePath: ${JSON.stringify(systemChromium)}, headless: true });
    const cdp = await browser.newBrowserCDPSession();
    const { processInfo } = await cdp.send("SystemInfo.getProcessInfo");
    writeFileSync(${JSON.stringify(pidFile)}, String(processInfo.find(p => p.type === "browser").id));
    console.log("browser-ready");
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0);
  `);
  const controller = new AbortController();
  let passed = false;
  try {
    await assert.rejects(runViewerVerification([script], {
      cwd: root, timeout: 10000,
      signal: controller.signal,
      onOutput: text => { if (text.includes("browser-ready")) controller.abort(); },
    }), /cancelled or timed out/);
    const pid = readFileSync(pidFile, "utf8");
    const state = spawnSync("ps", ["-p", pid, "-o", "stat="], { encoding: "utf8" });
    assert.ok(state.status === 1 || state.stdout.trim().startsWith("Z"), "No running Chromium remains after cancellation.");
    passed = true;
  } finally {
    if (passed) rmSync(output, { recursive: true, force: true });
  }
});

test("#1158: timed-out verification shuts down its process group before output cleanup", {
  skip: process.platform === "win32" ? "POSIX process-group regression." : false,
  timeout: 15000,
}, async () => {
  mkdirSync(path.join(root, "out"), { recursive: true });
  const output = mkdtempSync(path.join(root, "out/m-cancel-"));
  const marker = path.join(output, "child-pid");
  const script = path.join(output, "runner.mjs");
  writeFileSync(script, `
    import { spawn } from "node:child_process";
    const child = spawn(process.execPath, ["--input-type=module", "-e", ${JSON.stringify(`
      import { writeFileSync } from "node:fs";
      writeFileSync(${JSON.stringify(marker)}, String(process.pid));
      console.log("ready");
      setInterval(() => {}, 1000);
    `)}], { stdio: "inherit" });
    process.on("SIGTERM", () => { child.kill("SIGTERM"); });
    child.on("close", () => process.exit(0));
  `);
  let passed = false;
  try {
    await assert.rejects(runViewerVerification([script], {
      cwd: root,
      timeout: 2000,
    }), /cancelled or timed out/);
    const state = spawnSync("ps", ["-p", readFileSync(marker, "utf8"), "-o", "stat="], { encoding: "utf8" });
    assert.ok(state.status === 1 || state.stdout.trim().startsWith("Z"), "Await child shutdown before deleting its evidence.");
    passed = true;
  } finally {
    if (passed) rmSync(output, { recursive: true, force: true });
  }
});

test("#1158: cancellation cannot launch Chromium after a delayed ownership snapshot", {
  skip: !available || process.platform === "win32" ? "Requires installed Chromium and POSIX process groups." : false,
  timeout: 20000,
}, async () => {
  mkdirSync(path.join(root, "out"), { recursive: true });
  const output = mkdtempSync(path.join(root, "out/m-launch-"));
  const script = path.join(output, "launcher.mjs");
  const pidFile = path.join(output, "browser-pid");
  // OS boundary: delay delivery of a real process snapshot while a browser launch is pending.
  writeFileSync(path.join(output, "ps"), `#!/bin/sh
/usr/bin/ps "$@" > "${output}/snapshot-$$"
/bin/sleep 0.5
/bin/cat "${output}/snapshot-$$"
/bin/rm "${output}/snapshot-$$"
`, { mode: 0o755 });
  writeFileSync(script, `
    import { chromium } from ${JSON.stringify(path.join(root, "gui/app/node_modules/playwright/index.mjs"))};
    import { writeFileSync } from "node:fs";
    process.on("SIGTERM", () => {});
    setTimeout(async () => {
      const browser = await chromium.launch({ executablePath: ${JSON.stringify(systemChromium)}, headless: true });
      const cdp = await browser.newBrowserCDPSession();
      const { processInfo } = await cdp.send("SystemInfo.getProcessInfo");
      writeFileSync(${JSON.stringify(pidFile)}, String(processInfo.find(p => p.type === "browser").id));
    }, 100);
    console.log("verifier-ready");
  `);
  const previousPath = process.env.PATH;
  const controller = new AbortController();
  let passed = false;
  try {
    process.env.PATH = `${output}${path.delimiter}${previousPath}`;
    await assert.rejects(runViewerVerification([script], {
      cwd: root, timeout: 10000, signal: controller.signal,
      onOutput: text => { if (text.includes("verifier-ready")) controller.abort(); },
    }), /cancelled or timed out/);
    assert.equal(existsSync(pidFile), false, "Frozen verifier must not launch a browser outside the snapshot.");
    passed = true;
  } finally {
    if (previousPath === undefined) delete process.env.PATH;
    else process.env.PATH = previousPath;
    if (passed) rmSync(output, { recursive: true, force: true });
  }
});
