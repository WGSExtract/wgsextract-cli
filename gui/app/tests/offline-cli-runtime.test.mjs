import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync, lstatSync } from "node:fs";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { offlineCliAppDir } from "../scripts/offline-cli-runtime.mjs";

// The offline viewer and gene-page verifiers ran the bundle's wrapper with WGSEXTRACT_APP_DIR set to
// the checkout. The Windows wrapper refuses a runtime without setup's bin\wgsextract.cmd shim, so every
// viewer open reported headerToolMissing and #1158's memory proof never saw "Matched local reference".

const root = path.resolve(import.meta.dirname, "../../..");
const scripts = path.join(root, "gui/examples/WGSExtract/scripts");
const installed = existsSync(path.join(root, ".pixi/envs/default/conda-meta/pixi"));

function runInEnvironment(appDir, args) {
  const [command, wrapperArgs] = process.platform === "win32"
    ? ["powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.join(scripts, "windows/run-wgsextract-env.ps1")]]
    : ["sh", [path.join(scripts, "posix/run-wgsextract-env.sh")]];
  return new Promise((resolve) => {
    const child = execFile(command, [...wrapperArgs, ...args], { env: { ...process.env, WGSEXTRACT_APP_DIR: appDir }, timeout: 120_000 },
      (error, stdout, stderr) => resolve({ exitCode: error ? error.code : 0, stdout, stderr }));
    child.stdin.end();
  });
}

test("offline verifiers run the checkout's CLI through the bundle wrapper", {
  skip: !installed && "Needs the repo root's locked default Pixi environment.",
  timeout: 150_000,
}, async () => {
  const runtime = await offlineCliAppDir(root);
  try {
    const result = await runInEnvironment(runtime.appDir, ["python", "-c", "import sys, wgsextract_cli; print(sys.prefix)"]);
    assert.equal(result.exitCode, 0, result.stderr);
    assert.equal(path.resolve(result.stdout.trim()).toLowerCase().endsWith(path.join(".pixi", "envs", "default").toLowerCase()), true, result.stdout);
  } finally {
    await runtime.dispose();
  }
});

test("Windows offline runtime removes only its junction, never the checkout", {
  skip: process.platform !== "win32" && "Windows junction layout.",
}, async (t) => {
  const checkout = await mkdtemp(path.join(tmpdir(), "wgse-checkout-"));
  t.after(() => rm(checkout, { recursive: true, force: true }));
  await writeFile(path.join(checkout, "pixi.toml"), "");
  const runtime = await offlineCliAppDir(checkout);
  const home = path.dirname(runtime.appDir);
  assert.equal(lstatSync(runtime.appDir).isSymbolicLink(), true);
  assert.equal(existsSync(path.join(runtime.appDir, "pixi.toml")), true);
  assert.equal(existsSync(path.join(home, "bin", "wgsextract.cmd")), true);
  assert.equal(home.startsWith(`${root}${path.sep}`), false, "Keep the junction out of the repository.");
  await runtime.dispose();
  assert.equal(existsSync(home), false);
  assert.equal(existsSync(path.join(checkout, "pixi.toml")), true);
});

test("non-Windows verifiers keep using the checkout directly", async () => {
  const runtime = await offlineCliAppDir("/repo", "linux");
  assert.equal(runtime.appDir, "/repo");
  await runtime.dispose();
});
