import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { chromium } from "playwright";

const root = path.resolve(import.meta.dirname, "../../..");
const tools = path.join(root, ".pixi/envs/default", process.platform === "win32" ? "Library/bin" : "bin");
const systemChromium = process.platform === "linux" && existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : chromium.executablePath();
const available = existsSync(systemChromium)
  && ["samtools", "bcftools", "bgzip", "tabix"].every(name => existsSync(path.join(tools, `${name}${process.platform === "win32" ? ".exe" : ""}`)));

test("#1158: offline Chromium heap stays bounded across repeated BAM/CRAM locus jumps", { skip: !available, timeout: 180000 }, () => {
  mkdirSync(path.join(root, "out"), { recursive: true });
  const output = mkdtempSync(path.join(root, "out/m-"));
  try {
    const result = spawnSync(process.execPath, [
      path.join(root, "gui/app/scripts/verify-viewer-offline.mjs"),
      "--memory", "--out", output, "--chromium", systemChromium,
    ], { cwd: root, encoding: "utf8", timeout: 170000, env: { ...process.env, TMPDIR: output } });
    assert.equal(result.status, 0, `${result.error ?? ""}\n${result.stdout}\n${result.stderr}`);
  } finally {
    rmSync(output, { recursive: true, force: true });
  }
});
