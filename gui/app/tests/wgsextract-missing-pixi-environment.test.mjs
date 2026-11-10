import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// Setup installs WGS Extract's Pixi environments under %LOCALAPPDATA%\WGSExtractPixi\envs, outside the
// workspace that records setup as done. When setup ran where Windows keeps AppData\Local apart (another
// account, an MSIX app's sandbox), every page's data source failed with "python: command not found" /
// "wgsextract: command not found" and Pixi's task list, and nothing said to run setup again.

const windowsScripts = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../examples/WGSExtract/scripts/windows");
const skip = process.platform !== "win32" && "Windows PowerShell scripts";

/** A workspace whose setup finished, with its environments in a folder named with brackets and an accent. */
async function setUpWorkspace(root) {
  const runtime = path.join(root, "wgs extract", "runtime", "wgsextract-cli");
  const appDir = path.join(runtime, "app");
  const envsDir = path.join(root, "Dåta [D]", "WGSExtractPixi", "envs");
  await mkdir(path.join(appDir, ".pixi"), { recursive: true });
  await mkdir(path.join(runtime, "bin"), { recursive: true });
  await mkdir(envsDir, { recursive: true });
  await writeFile(path.join(appDir, "pixi.toml"), "");
  await writeFile(path.join(runtime, "bin", "wgsextract.cmd"), "@echo off\r\n");
  await writeFile(path.join(appDir, ".pixi", "config.toml"), `detached-environments = '${envsDir}' # set by setup\n`);
  // Another checkout's environments in the same folder don't count, nor does an unreadable record.
  await installEnvironment(envsDir, "wgsextract-cli-2", path.join(root, "other checkout", "pixi.toml"));
  const unreadable = path.join(envsDir, "wgsextract-cli-3", "envs", "default", "conda-meta");
  await mkdir(unreadable, { recursive: true });
  await writeFile(path.join(unreadable, "pixi"), "{ not json");

  const log = path.join(root, "pixi-calls.log");
  const pixi = path.join(root, "pixi.cmd");
  await writeFile(pixi, `@echo off\r\n>> "${log}" echo %*\r\nexit /b 0\r\n`);
  return { appDir, envsDir, log, pixi };
}

async function installEnvironment(envsDir, project, manifest) {
  const meta = path.join(envsDir, project, "envs", "default", "conda-meta");
  await mkdir(meta, { recursive: true });
  await writeFile(path.join(meta, "pixi"), JSON.stringify({ manifest_path: manifest, environment_name: "default" }));
}

function runInEnvironment(workspace, args, { dataSource }) {
  const env = { ...process.env, PIXI: workspace.pixi, WGSEXTRACT_APP_DIR: workspace.appDir };
  delete env.GUI_FOR_CLI_DATA_SOURCE;
  if (dataSource) {
    env.GUI_FOR_CLI_DATA_SOURCE = "1";
  }
  return new Promise((resolve) => {
    const child = execFile("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.join(windowsScripts, "run-wgsextract-env.ps1"), ...args], { env, timeout: 60_000 },
      (error, stdout, stderr) => resolve({ exitCode: error ? error.code : 0, stdout, stderr }));
    // As the app runs data sources: with nothing on stdin, which PowerShell would otherwise wait on.
    child.stdin.end();
  });
}

async function pixiCalls(workspace) {
  return existsSync(workspace.log) ? (await readFile(workspace.log, "utf8")).trim().split(/\r?\n/) : [];
}

test("a data source says to run setup again when this account has no WGS Extract environment", { skip }, async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "wgsextract-envs-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const workspace = await setUpWorkspace(root);

  const result = await runInEnvironment(workspace, ["python", "-c", "pass"], { dataSource: true });

  assert.equal(result.exitCode, 127);
  assert.match(result.stderr, /Run Setup again/);
  // Windows PowerShell writes stderr in the console's code page, so only the ASCII end of the folder is compared.
  assert.ok(result.stderr.includes(path.join("WGSExtractPixi", "envs")), result.stderr);
  assert.deepEqual(await pixiCalls(workspace), [], "nothing runs in a missing environment");
});

test("an environment whose install didn't finish counts as missing", { skip }, async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "wgsextract-envs-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const workspace = await setUpWorkspace(root);
  // Not detached: the environment is in the workspace, and Pixi writes conda-meta\pixi last.
  await writeFile(path.join(workspace.appDir, ".pixi", "config.toml"), "");
  await mkdir(path.join(workspace.appDir, ".pixi", "envs", "default", "conda-meta"), { recursive: true });

  const result = await runInEnvironment(workspace, ["python", "-c", "pass"], { dataSource: true });

  assert.equal(result.exitCode, 127);
  assert.match(result.stderr, /Run Setup again/);
  assert.deepEqual(await pixiCalls(workspace), []);
});

test("data sources run in the environment setup installed for this workspace", { skip }, async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "wgsextract-envs-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const workspace = await setUpWorkspace(root);
  await installEnvironment(workspace.envsDir, "wgsextract-cli-1", path.join(workspace.appDir, "pixi.toml"));

  const result = await runInEnvironment(workspace, ["wgsextract", "ref", "list"], { dataSource: true });

  assert.equal(result.exitCode, 0, result.stderr);
  assert.deepEqual(await pixiCalls(workspace), ["run --no-install wgsextract ref list"]);
});

test("actions still let Pixi install a missing environment", { skip }, async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "wgsextract-envs-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const workspace = await setUpWorkspace(root);

  const result = await runInEnvironment(workspace, ["wgsextract", "info"], { dataSource: false });

  assert.equal(result.exitCode, 0, result.stderr);
  assert.deepEqual(await pixiCalls(workspace), ["run wgsextract info"]);
});
