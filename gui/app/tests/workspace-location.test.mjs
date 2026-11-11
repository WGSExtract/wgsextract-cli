import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const cli = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist/web/src/server/bundle-uninstall-cli.js");
const { saveConfig } = await import("../dist/web/src/server/config-store.js");
const { applicationSupportDirectory, normalizeContext } = await import("../dist/web/src/server/paths.js");
const { prepareBundleWorkspace } = await import("../dist/web/src/server/workspace.js");
const { appDataWorkspacesDirectory, appSettingsPath, defaultWorkspaceRoot, saveWorkspacesDirectory, workspaceLocationInfo } =
  await import("../dist/web/src/server/workspace-location.js");
const { moveWorkspace } = await import("../dist/web/src/server/workspace-move.js");
const { displayWorkspacePath, relocatePath } = await import("../dist/shared/workspace-paths.js");

const settingsControl = {
  id: "paths",
  label: "Paths",
  kind: "configEditor",
  configFile: { path: "{{bundleWorkspace}}/settings/config.toml", format: "toml" },
  settings: [
    { id: "output", key: "output_directory", label: "Output", kind: "path" },
    { id: "elsewhere", key: "elsewhere", label: "Elsewhere", kind: "path" },
  ],
};
const manifest = {
  id: "location.bundle",
  displayName: "Location Fixture",
  summary: "Tests where workspaces live.",
  pages: [{ id: "main", title: "Main", summary: "Main page.", sections: [{ id: "main", controls: [settingsControl] }] }],
};

function dataEnvironment(root) {
  return {
    HOME: root,
    USERPROFILE: root,
    XDG_DATA_HOME: path.join(root, "data"),
    LOCALAPPDATA: path.join(root, "data"),
    WGSEXTRACT_GUI_APP_DATA_NAME: "test.app",
  };
}

async function fixture(t, fixtureManifest = manifest) {
  const root = await mkdtemp(path.join(tmpdir(), "gui-for-cli-workspace-location-"));
  const original = {};
  for (const [key, value] of Object.entries(dataEnvironment(root))) {
    original[key] = process.env[key];
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }
  t.after(async () => {
    for (const [key, value] of Object.entries(original)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
    await rm(root, { force: true, recursive: true });
  });
  const source = path.join(root, "source");
  await mkdir(source, { recursive: true });
  await writeFile(path.join(source, "manifest.json"), JSON.stringify(fixtureManifest));
  return { root, source };
}

function move(request) {
  return moveWorkspace({ manifest, onProgress: () => undefined, signal: new AbortController().signal, ...request });
}

test("a bundle can default its workspace to a visible folder in the home folder", async (t) => {
  const declared = { ...manifest, defaultWorkspace: "~/visible-workspace" };
  const { source } = await fixture(t, declared);

  assert.equal(await prepareBundleWorkspace(declared, source), path.join(homedir(), "visible-workspace"));
  assert.equal(defaultWorkspaceRoot(manifest), path.join(appDataWorkspacesDirectory(), "location.bundle"));
  for (const defaultWorkspace of ["/etc/app", "~/.hidden", "~/a/b", "~/../up"]) {
    assert.throws(() => defaultWorkspaceRoot({ ...manifest, defaultWorkspace }), /defaultWorkspace/);
  }
});

test("PR #531 review: a folder of the user's with the default name is left alone and the next free name is used from then on", async (t) => {
  const declared = { ...manifest, defaultWorkspace: "~/visible-workspace" };
  const { root, source } = await fixture(t, declared);
  for (const name of ["visible-workspace", "visible-workspace-2"]) {
    await mkdir(path.join(root, name));
    await writeFile(path.join(root, name, "manifest.json"), "someone else's");
  }

  const fallback = path.join(root, "visible-workspace-3");
  assert.equal(await prepareBundleWorkspace(declared, source), fallback);
  assert.equal(await readFile(path.join(root, "visible-workspace", "manifest.json"), "utf8"), "someone else's");
  assert.equal(await readFile(path.join(root, "visible-workspace-2", "manifest.json"), "utf8"), "someone else's");
  // Still there once the user's folder is gone: the workspace doesn't jump back.
  await rm(path.join(root, "visible-workspace"), { recursive: true });
  assert.equal(await prepareBundleWorkspace(declared, source), fallback);
  assert.equal((await workspaceLocationInfo(declared, fallback)).usingDefault, true);
});

test("PR #531 re-review: a file, or a folder that can't be read, with the default name doesn't stop the backend", async (t) => {
  const declared = { ...manifest, defaultWorkspace: "~/visible-workspace" };
  const { root, source } = await fixture(t, declared);
  await writeFile(path.join(root, "visible-workspace"), "");
  // Permissions don't hide a folder from root or on Windows.
  const unreadable = process.platform !== "win32" && process.getuid?.() !== 0;
  if (unreadable) {
    await mkdir(path.join(root, "visible-workspace-2"), { mode: 0o000 });
  }

  const fallback = path.join(root, unreadable ? "visible-workspace-3" : "visible-workspace-2");
  try {
    assert.equal(await prepareBundleWorkspace(declared, source), fallback);
  } finally {
    if (unreadable) {
      await chmod(path.join(root, "visible-workspace-2"), 0o700);
    }
  }
  assert.equal(await readFile(path.join(root, "visible-workspace"), "utf8"), "");
});

test("a folder chosen in Settings that isn't a workspace fails saying how to recover, and is left alone", async (t) => {
  const { root, source } = await fixture(t);
  await mkdir(path.join(root, "chosen", "location.bundle"), { recursive: true });
  await writeFile(path.join(root, "chosen", "location.bundle", "notes.txt"), "mine");
  await saveWorkspacesDirectory(path.join(root, "chosen"));

  await assert.rejects(prepareBundleWorkspace(manifest, source), (error) => error.message.includes("bundleWorkspacesDirectory") && error.message.includes(appSettingsPath()));
  assert.equal(await readFile(path.join(root, "chosen", "location.bundle", "notes.txt"), "utf8"), "mine");
});

test("moving keeps setup's run when its runtime is outside the workspace, in the app's data folder", async (t) => {
  const { root, source } = await fixture(t);
  const current = await prepareBundleWorkspace(manifest, source);
  await writeFile(path.join(current, "state.json"), JSON.stringify({ setupRun: { status: "ok" } }));

  const chosen = path.join(root, "big drive");
  await move({ sourceRoot: source, workspaceRoot: current, directory: chosen });

  const state = JSON.parse(await readFile(path.join(chosen, "location.bundle", "state.json"), "utf8"));
  assert.deepEqual(state.setupRun, { status: "ok" });
});

test("moving takes the contents along, and saved paths follow unless set elsewhere", async (t) => {
  const { root, source } = await fixture(t);
  const current = await prepareBundleWorkspace(manifest, source);
  const elsewhere = path.join(root, "my outputs");
  await saveConfig(settingsControl, undefined, { output_directory: "{{bundleWorkspace}}/output", elsewhere }, current);
  await mkdir(path.join(current, "reference"));
  await writeFile(path.join(current, "reference", "hg38.fa"), "ACGT");
  await writeFile(path.join(current, "state.json"), JSON.stringify({
    fieldValues: { bam: path.join(current, "genomes", "a.bam"), other: elsewhere },
    setupRun: { status: "ok" },
  }));
  // A CLI runtime setup installed in the workspace (WGS Extract's, up to 0.8.6) holds absolute paths.
  await mkdir(path.join(current, "runtime", "tool", "bin"), { recursive: true });

  const chosen = path.join(root, "big drive");
  await move({ sourceRoot: source, workspaceRoot: current, directory: chosen });

  const moved = path.join(chosen, "location.bundle");
  await assert.rejects(stat(current), { code: "ENOENT" });
  assert.equal(await readFile(path.join(moved, "reference", "hg38.fa"), "utf8"), "ACGT");
  const config = await readFile(path.join(moved, "settings", "config.toml"), "utf8");

  const { parseFlatToml } = await import("../dist/shared/rendering.js");
  assert.deepEqual({ ...parseFlatToml(config) }, { output_directory: path.join(moved, "output"), elsewhere });
  const state = JSON.parse(await readFile(path.join(moved, "state.json"), "utf8"));
  assert.deepEqual(state.fieldValues, { bam: path.join(moved, "genomes", "a.bam"), other: elsewhere });
  assert.equal(state.setupRun, null, "setup has to run again");

  assert.equal(await prepareBundleWorkspace(manifest, source), moved);
  const info = await workspaceLocationInfo(manifest, moved);
  assert.equal(info.configuredDirectory, chosen);
  assert.equal(info.usingDefault, false);

  await move({ sourceRoot: source, workspaceRoot: moved, directory: null });
  assert.equal(await prepareBundleWorkspace(manifest, source), current);
  assert.equal(await readFile(path.join(current, "reference", "hg38.fa"), "utf8"), "ACGT");
  assert.equal((await workspaceLocationInfo(manifest, current)).usingDefault, true);
});

test("moving leaves the CLI runtime behind, except its record of what setup installed", async (t) => {
  const { root, source } = await fixture(t);
  const current = await prepareBundleWorkspace(manifest, source);
  const tool = path.join(current, "runtime", "wgsextract-cli");
  await mkdir(path.join(tool, "app", ".pixi"), { recursive: true });
  await writeFile(path.join(tool, "app", ".pixi", "env"), "absolute paths");
  await writeFile(path.join(tool, "install-manifest.json"), "{}");

  await move({ sourceRoot: source, workspaceRoot: current, directory: path.join(root, "chosen") });

  const movedTool = path.join(root, "chosen", "location.bundle", "runtime", "wgsextract-cli");
  await assert.rejects(stat(path.join(movedTool, "app")), { code: "ENOENT" });
  assert.equal(await readFile(path.join(movedTool, "install-manifest.json"), "utf8"), "{}");
});

test("folders that are unsafe to move to are refused and nothing changes", async (t) => {
  const { root, source } = await fixture(t);
  const current = await prepareBundleWorkspace(manifest, source);
  const occupied = path.join(root, "occupied");
  await mkdir(path.join(occupied, "location.bundle"), { recursive: true });
  await writeFile(path.join(occupied, "location.bundle", "keep.txt"), "someone else's");

  for (const [directory, message] of [
    ["relative/path", /absolute/],
    [path.join(source, "inside"), /cannot be inside/],
    [path.join(current, "inside"), /cannot be inside/],
    [occupied, /already has files/],
  ]) {
    await assert.rejects(move({ sourceRoot: source, workspaceRoot: current, directory }), message);
  }

  await stat(path.join(current, "manifest.json"));
  await assert.rejects(stat(appSettingsPath()), { code: "ENOENT" });
  assert.equal(await readFile(path.join(occupied, "location.bundle", "keep.txt"), "utf8"), "someone else's");
});

test("a chosen folder keeps its workspace across a changed bundle source", async (t) => {
  const { root, source } = await fixture(t);
  await saveWorkspacesDirectory(path.join(root, "chosen"));
  const workspace = await prepareBundleWorkspace(manifest, source);
  await writeFile(path.join(workspace, "state.json"), "{}\n");

  const updatedSource = path.join(root, "updated-source");
  await mkdir(updatedSource);
  await writeFile(path.join(updatedSource, "manifest.json"), JSON.stringify(manifest));

  assert.equal(await prepareBundleWorkspace(manifest, updatedSource), workspace);
  assert.equal(await readFile(path.join(workspace, "state.json"), "utf8"), "{}\n");
});

test("paths in the workspace are shown with the token, and real paths reach the settings file and commands", async (t) => {
  const { root } = await fixture(t);
  const workspace = path.join(root, "workspace");
  const output = path.join(workspace, "output");

  assert.equal(displayWorkspacePath(output, workspace), "{{bundleWorkspace}}/output");
  assert.equal(displayWorkspacePath(workspace, workspace), "{{bundleWorkspace}}");
  assert.equal(displayWorkspacePath(`${workspace}-other`, workspace), `${workspace}-other`);
  assert.equal(displayWorkspacePath("C:\\Users\\Me\\WGS-Extract\\output", "c:\\users\\me\\wgs-extract"), "{{bundleWorkspace}}/output");
  assert.equal(relocatePath("/home/me/w/a/b", "/home/me/w", "D:\\w"), "D:\\w\\a\\b");
  // PR #531 review: a sibling folder written through the workspace is outside it, so a move leaves it alone.
  assert.equal(relocatePath("/home/me/w/../shared-reference", "/home/me/w", "/mnt/disk/w"), "/home/me/w/../shared-reference");
  assert.equal(displayWorkspacePath(`${workspace}/./output/`, workspace), "{{bundleWorkspace}}/output");

  const { path: configFile } = await saveConfig(settingsControl, undefined, { output_directory: "{{bundleWorkspace}}/output", elsewhere: "" }, workspace);
  assert.match(await readFile(configFile, "utf8"), /output_directory = /);
  assert.doesNotMatch(await readFile(configFile, "utf8"), /bundleWorkspace/);

  const context = normalizeContext({ fieldValues: { out_dir: "{{bundleWorkspace}}/output", threads: 4 } }, workspace);
  assert.deepEqual(context.fieldValues, { out_dir: output, threads: 4 });
});

function runUninstallCli(source, ...flags) {
  return new Promise((resolve) => {
    execFile(process.execPath, [cli, "--app-data-name", "test.app", ...flags, source], { env: process.env },
      (error, stdout, stderr) => resolve({ code: error ? error.code : 0, stdout, stderr }));
  });
}

test("uninstall deletes no workspace without being asked, wherever it is", async (t) => {
  const { root, source } = await fixture(t);
  const chosen = path.join(root, "chosen");
  const appDataWorkspace = await prepareBundleWorkspace(manifest, source);
  await saveWorkspacesDirectory(chosen);
  const workspace = await prepareBundleWorkspace(manifest, source);
  await writeFile(path.join(chosen, "keep.txt"), "not the app's");

  const printed = await runUninstallCli(source, "--print-workspace");
  assert.deepEqual(printed.stdout.trim().split(/\r?\n/), [workspace, appDataWorkspace], printed.stderr);

  for (const flags of [[], ["--remove-app-data"]]) {
    const kept = await runUninstallCli(source, ...flags);
    assert.equal(kept.code, 0, `${kept.stdout}\n${kept.stderr}`);
    await stat(path.join(workspace, "manifest.json"));
    await stat(path.join(appDataWorkspace, "manifest.json"));
  }
  await assert.rejects(stat(appSettingsPath()), { code: "ENOENT" }, "the rest of the app's data is removed");

  await saveWorkspacesDirectory(chosen);
  const removed = await runUninstallCli(source, "--remove-workspace", "--remove-app-data");
  assert.equal(removed.code, 0, `${removed.stdout}\n${removed.stderr}`);
  await assert.rejects(stat(workspace), { code: "ENOENT" });
  await assert.rejects(stat(path.dirname(appSettingsPath())), { code: "ENOENT" });
  assert.equal(await readFile(path.join(chosen, "keep.txt"), "utf8"), "not the app's");
  assert.equal((await runUninstallCli(source, "--print-workspace")).stdout, "");
});

test("PR #531 review: uninstall runs no steps from, and deletes nothing of, a folder that isn't the app's workspace", async (t) => {
  const { root, source } = await fixture(t);
  const unrelated = path.join(appDataWorkspacesDirectory(), "location.bundle");
  await mkdir(unrelated, { recursive: true });
  await writeFile(path.join(unrelated, "keep.txt"), "not the app's");
  await saveWorkspacesDirectory(path.join(root, "chosen"));
  const workspace = await prepareBundleWorkspace(manifest, source);

  assert.deepEqual((await runUninstallCli(source, "--print-workspace")).stdout.trim().split(/\r?\n/), [workspace]);
  const removed = await runUninstallCli(source, "--remove-workspace", "--remove-app-data");
  assert.equal(removed.code, 0, `${removed.stdout}\n${removed.stderr}`);
  await assert.rejects(stat(workspace), { code: "ENOENT" });
  assert.equal(await readFile(path.join(unrelated, "keep.txt"), "utf8"), "not the app's");
  await assert.rejects(stat(appSettingsPath()), { code: "ENOENT" });
});

test("the settings of an app up to 0.8.6 come over from its identifier's folder, which is removed", async (t) => {
  await fixture(t);
  const { migrateLegacyAppData, legacyAppDataName } = await import("../dist/web/src/server/workspace-location.js");
  // As the desktop app starts its backend: naming its own data folder explicitly.
  process.env.WGSEXTRACT_GUI_APP_DATA_NAME = "WGSExtract-gui";
  const data = applicationSupportDirectory();
  const legacy = path.join(data, legacyAppDataName);
  await mkdir(path.join(legacy, "EBWebView"), { recursive: true });
  await writeFile(path.join(legacy, "settings.json"), '{"bundleWorkspacesDirectory":"D:\\Genomes"}\n');

  await migrateLegacyAppData();

  assert.equal(await readFile(path.join(data, "WGSExtract-gui", "settings.json"), "utf8"), '{"bundleWorkspacesDirectory":"D:\\Genomes"}\n');
  await assert.rejects(stat(legacy), { code: "ENOENT" });
});

test("migrating keeps settings already in the app's data folder, and an old folder that still holds a workspace", async (t) => {
  await fixture(t);
  const { migrateLegacyAppData, legacyAppDataName } = await import("../dist/web/src/server/workspace-location.js");
  delete process.env.WGSEXTRACT_GUI_APP_DATA_NAME;
  const data = applicationSupportDirectory();
  const legacy = path.join(data, legacyAppDataName);
  await mkdir(path.join(legacy, "BundleWorkspaces", "wgs-extract"), { recursive: true });
  await writeFile(path.join(legacy, "settings.json"), "{\"old\":true}\n");
  await mkdir(path.join(data, "WGSExtract-gui"), { recursive: true });
  await writeFile(path.join(data, "WGSExtract-gui", "settings.json"), "{\"new\":true}\n");

  await migrateLegacyAppData();

  assert.equal(await readFile(path.join(data, "WGSExtract-gui", "settings.json"), "utf8"), "{\"new\":true}\n");
  assert.equal(await readFile(path.join(legacy, "settings.json"), "utf8"), "{\"old\":true}\n");
});

test("a test's or dev run's own data folder name is never migrated into", async (t) => {
  await fixture(t);
  const { migrateLegacyAppData, legacyAppDataName } = await import("../dist/web/src/server/workspace-location.js");
  const legacy = path.join(applicationSupportDirectory(), legacyAppDataName);
  await mkdir(legacy, { recursive: true });
  await writeFile(path.join(legacy, "settings.json"), "{}\n");

  await migrateLegacyAppData();

  assert.equal(await readFile(path.join(legacy, "settings.json"), "utf8"), "{}\n");
  await assert.rejects(stat(path.join(applicationSupportDirectory(), "test.app")), { code: "ENOENT" });
});

test("migrating keeps an old data folder that the saved settings put the workspace in", async (t) => {
  await fixture(t);
  const { migrateLegacyAppData, legacyAppDataName } = await import("../dist/web/src/server/workspace-location.js");
  delete process.env.WGSEXTRACT_GUI_APP_DATA_NAME;
  const legacy = path.join(applicationSupportDirectory(), legacyAppDataName);
  await mkdir(path.join(legacy, "mine"), { recursive: true });
  await writeFile(path.join(legacy, "settings.json"), JSON.stringify({ bundleWorkspacesDirectory: path.join(legacy, "mine") }));

  await migrateLegacyAppData();

  await stat(path.join(legacy, "mine"));
  assert.match(await readFile(path.join(applicationSupportDirectory(), "WGSExtract-gui", "settings.json"), "utf8"), /mine/);
});
