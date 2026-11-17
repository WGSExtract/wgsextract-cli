import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, stat, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { parse as parseToml } from "smol-toml";
import { fileURLToPath } from "node:url";

const cli = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist/web/src/server/bundle-uninstall-cli.js");
const { saveConfig } = await import("../dist/web/src/server/config-store.js");
const { applicationSupportDirectory, normalizeContext } = await import("../dist/web/src/server/paths.js");
const { prepareBundleWorkspace } = await import("../dist/web/src/server/workspace.js");
const { appDataWorkspacesDirectory, appSettingsPath } = await import("../dist/web/src/server/workspace-location.js");
const { saveUsersWorkspace, usersWorkspaceFor, usersWorkspaceLocationInfo } = await import("../dist/web/src/server/users-workspace.js");
const { moveWorkspace } = await import("../dist/web/src/server/workspace-move.js");
const { displayWorkspacePath, relocatePath } = await import("../dist/shared/workspace-paths.js");
const { parseFlatToml } = await import("../dist/shared/rendering.js");

const settingsControl = {
  id: "paths",
  label: "Paths",
  kind: "configEditor",
  configFile: { path: "{{usersWorkspace}}/config.toml", previousPaths: ["{{bundleWorkspace}}/config.toml"], format: "toml" },
  settings: [
    { id: "output", key: "output_directory", label: "Output", kind: "path", moveContents: true },
    { id: "reference", key: "reference_library", label: "Reference", kind: "path", moveContents: true },
    { id: "genomes", key: "reference_genome_library", label: "Genomes", kind: "path", moveContents: true },
    { id: "elsewhere", key: "elsewhere", label: "Elsewhere", kind: "path" },
  ],
};
const manifest = {
  id: "location.bundle",
  displayName: "Location Fixture",
  summary: "Tests where workspaces live.",
  defaultUsersWorkspace: "~/visible-workspace",
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
  const root = await mkdtemp(path.join(tmpdir(), "wgsextract-gui-workspace-location-"));
  const original = {};
  for (const [key, value] of Object.entries(dataEnvironment(root))) {
    original[key] = process.env[key];
    process.env[key] = value;
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
  return {
    root,
    source,
    bundle: path.join(
      process.platform === "darwin" ? path.join(root, "Library", "Application Support") : path.join(root, "data"),
      "test.app", "BundleWorkspaces", "location.bundle",
    ),
    users: path.join(root, "visible-workspace"),
  };
}

function move(request) {
  return moveWorkspace({ manifest, onProgress: () => undefined, signal: new AbortController().signal, ...request });
}

async function settingsIn(file) {
  return { ...parseFlatToml(await readFile(file, "utf8")) };
}

test("the bundle workspace is the app's, in its data folder; the users workspace is a visible folder in the home folder", async (t) => {
  const { root, source, bundle, users } = await fixture(t);

  assert.equal(await prepareBundleWorkspace(manifest, source), bundle);
  assert.equal(path.dirname(bundle), appDataWorkspacesDirectory());
  assert.equal(usersWorkspaceFor(bundle), users);
  assert.equal(await readFile(path.join(users, ".users-workspace"), "utf8"), "WGS Extract users workspace\n");
  // The app's copy of the bundle stays out of the user's folder.
  await stat(path.join(bundle, "manifest.json"));
  await assert.rejects(stat(path.join(users, "manifest.json")), { code: "ENOENT" });

  const info = await usersWorkspaceLocationInfo(manifest, users);
  assert.deepEqual(info, { workspaceRoot: users, defaultRoot: users, usingDefault: true });

  // A bundle without one uses its bundle workspace for {{usersWorkspace}}, and has no location to show.
  const plain = { ...manifest, id: "plain.bundle", defaultUsersWorkspace: undefined };
  const plainSource = path.join(root, "plain");
  await mkdir(plainSource);
  await writeFile(path.join(plainSource, "manifest.json"), JSON.stringify(plain));
  const plainBundle = await prepareBundleWorkspace(plain, plainSource);
  assert.equal(usersWorkspaceFor(plainBundle), plainBundle);
  assert.equal(await usersWorkspaceLocationInfo(plain, plainBundle), undefined);

  for (const defaultUsersWorkspace of ["/etc/app", "~/.hidden", "~/a/b", "~/../up"]) {
    await assert.rejects(prepareBundleWorkspace({ ...manifest, defaultUsersWorkspace }, source), /defaultUsersWorkspace/);
  }
});

test("a folder of the user's with the default name is left alone and the next free name is used from then on", async (t) => {
  const { source, users } = await fixture(t);
  await mkdir(users);
  await writeFile(path.join(users, "notes.txt"), "mine");

  const bundle = await prepareBundleWorkspace(manifest, source);

  assert.equal(usersWorkspaceFor(bundle), `${users}-2`);
  assert.equal(await readFile(path.join(users, "notes.txt"), "utf8"), "mine");
  await rm(path.join(users, "notes.txt"));
  await prepareBundleWorkspace(manifest, source);
  assert.equal(usersWorkspaceFor(bundle), `${users}-2`, "the recorded folder stays the default");
});

test("a users workspace the CLI made is shared, not passed over", async (t) => {
  const { source, users } = await fixture(t);
  await mkdir(path.join(users, "output"), { recursive: true });
  await writeFile(path.join(users, ".users-workspace"), "WGS Extract users workspace\n");
  await writeFile(path.join(users, "config.toml"), 'cpu_threads = 4\nserver_allowed_paths = ["/allowed", "/shared"]\n[dev]\nreal_genome_tests_enabled = true\n');

  const bundle = await prepareBundleWorkspace(manifest, source);

  assert.equal(usersWorkspaceFor(bundle), users);
});

test("a folder chosen in Settings that isn't a users workspace fails saying how to recover, and is left alone", async (t) => {
  const { root, source } = await fixture(t);
  const chosen = path.join(root, "chosen");
  await mkdir(chosen);
  await writeFile(path.join(chosen, "keep.txt"), "someone else's");
  await saveUsersWorkspace(manifest, chosen);

  await assert.rejects(prepareBundleWorkspace(manifest, source), /usersWorkspaces/);
  assert.equal(await readFile(path.join(chosen, "keep.txt"), "utf8"), "someone else's");
});

test("the home folder workspace of 0.8.5 to 0.8.7 becomes the users workspace, keeping the user's files", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await mkdir(path.join(source, "pages"));
  await writeFile(path.join(source, "pages", "main.json"), "{}");
  // As 0.8.7 left it: the bundle's copy, its state and the user's folders, all in ~/visible-workspace.
  await mkdir(path.join(users, "pages"), { recursive: true });
  await writeFile(path.join(users, "manifest.json"), JSON.stringify(manifest));
  await writeFile(path.join(users, "pages", "main.json"), "{}");
  await writeFile(path.join(users, ".bundle-workspace"), "WGS Extract GUI workspace\n");
  // A dev bundle's sync record can also name the user's logs folder, which the bundle itself doesn't have.
  await writeFile(path.join(users, ".workspace-sync.json"), JSON.stringify({ entries: [{ path: "manifest.json" }, { path: "pages" }, { path: path.join("pages", "main.json") }, { path: "logs" }] }));
  await mkdir(path.join(users, "logs"), { recursive: true });
  await writeFile(path.join(users, "logs", "run.log"), "kept");
  await writeFile(path.join(users, "state.json"), JSON.stringify({ fieldValues: { bam: path.join(users, "datasets", "a.bam"), tokenBam: "{{bundleWorkspace}}/datasets/a.bam" } }));
  await mkdir(path.join(users, "reference"), { recursive: true });
  await writeFile(path.join(users, "reference", "hg38.fa"), "ACGT");
  await writeFile(path.join(users, "config.toml"), `reference_library = ${JSON.stringify(path.join(users, "reference"))}\n`);
  await mkdir(path.dirname(appSettingsPath()), { recursive: true });
  await writeFile(appSettingsPath(), JSON.stringify({ unrelated: true }));

  assert.equal(await prepareBundleWorkspace(manifest, source), bundle);

  assert.equal(usersWorkspaceFor(bundle), users);
  assert.equal(await readFile(path.join(users, "reference", "hg38.fa"), "utf8"), "ACGT");
  assert.equal(await readFile(path.join(users, "logs", "run.log"), "utf8"), "kept");
  assert.equal((await settingsIn(path.join(users, "config.toml"))).reference_library, path.join(users, "reference"));
  for (const name of ["manifest.json", "pages", "state.json", ".workspace-sync.json", ".bundle-workspace"]) {
    await assert.rejects(stat(path.join(users, name)), { code: "ENOENT" }, name);
  }
  assert.deepEqual(JSON.parse(await readFile(path.join(bundle, "state.json"), "utf8")).fieldValues, { bam: path.join(users, "datasets", "a.bam"), tokenBam: "{{usersWorkspace}}/datasets/a.bam" });
  assert.equal(JSON.parse(await readFile(appSettingsPath(), "utf8")).unrelated, true);

  // Where 0.8.5 to 0.8.7 recorded a moved workspace, that folder is the users workspace.
  const other = await fixture(t);
  const moved = path.join(other.root, "big drive", "location.bundle");
  await mkdir(moved, { recursive: true });
  await writeFile(path.join(moved, ".bundle-workspace"), "WGS Extract GUI workspace\n");
  await mkdir(path.dirname(appSettingsPath()), { recursive: true });
  await writeFile(appSettingsPath(), JSON.stringify({ bundleWorkspacesDirectory: path.join(other.root, "big drive") }));
  const otherBundle = await prepareBundleWorkspace(manifest, other.source);
  assert.equal(usersWorkspaceFor(otherBundle), moved);
  assert.equal(JSON.parse(await readFile(appSettingsPath(), "utf8")).bundleWorkspacesDirectory, undefined);
});

test("a settings file an earlier release kept in the bundle workspace moves to the users workspace; its folders stay", async (t) => {
  const { source, bundle, users } = await fixture(t);
  const reference = path.join(bundle, "reference");
  await mkdir(reference, { recursive: true });
  await writeFile(path.join(bundle, ".bundle-workspace"), "WGS Extract GUI workspace\n");
  await writeFile(path.join(bundle, "config.toml"), `reference_library = ${JSON.stringify(reference)}\n`);
  const { bootstrapConfigFiles } = await import("../dist/web/src/server/config-store.js");

  await prepareBundleWorkspace(manifest, source);
  await bootstrapConfigFiles(manifest, bundle, {});

  assert.equal((await settingsIn(path.join(users, "config.toml"))).reference_library, reference);
  await assert.rejects(stat(path.join(bundle, "config.toml")), { code: "ENOENT" });
  await stat(reference);
});

test("moving the users workspace takes everything in it along, and saved paths follow unless set elsewhere", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  const elsewhere = path.join(root, "shared", "reference");
  await mkdir(path.join(users, "reference"), { recursive: true });
  await writeFile(path.join(users, "reference", "hg38.fa"), "ACGT");
  await saveConfig(settingsControl, undefined, {
    output_directory: "{{usersWorkspace}}/output",
    reference_library: path.join(users, "reference"),
    reference_genome_library: path.join(users, "reference", "genomes"),
    elsewhere,
  }, bundle);
  await writeFile(path.join(bundle, "state.json"), JSON.stringify({ fieldValues: { bam: path.join(users, "mygenomes", "a.bam"), other: elsewhere } }));

  const chosen = path.join(root, "big drive", "wgs");
  await move({ sourceRoot: source, bundleRoot: bundle, folder: chosen });

  await assert.rejects(stat(users), { code: "ENOENT" });
  assert.equal(await readFile(path.join(chosen, "reference", "hg38.fa"), "utf8"), "ACGT");
  assert.deepEqual(await settingsIn(path.join(chosen, "config.toml")), {
    output_directory: path.join(chosen, "output"),
    reference_library: path.join(chosen, "reference"),
    reference_genome_library: path.join(chosen, "reference", "genomes"),
    elsewhere,
  });
  const state = JSON.parse(await readFile(path.join(bundle, "state.json"), "utf8"));
  assert.deepEqual(state.fieldValues, { bam: path.join(chosen, "mygenomes", "a.bam"), other: elsewhere });

  assert.equal(await prepareBundleWorkspace(manifest, source), bundle);
  assert.equal(usersWorkspaceFor(bundle), chosen);
  assert.equal((await usersWorkspaceLocationInfo(manifest, chosen)).usingDefault, false);

  await move({ sourceRoot: source, bundleRoot: bundle, folder: null });
  await prepareBundleWorkspace(manifest, source);
  assert.equal(usersWorkspaceFor(bundle), users);
  assert.equal(await readFile(path.join(users, "reference", "hg38.fa"), "utf8"), "ACGT");
});

test("changing one folder setting moves what is in it, and the settings inside it follow", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  const reference = path.join(users, "reference");
  await mkdir(path.join(reference, "genomes"), { recursive: true });
  await writeFile(path.join(reference, "genomes", "hg38.fa"), "ACGT");
  await writeFile(path.join(users, "config.toml"), 'cpu_threads = 4\nserver_allowed_paths = ["/allowed", "/shared"]\n[dev]\nreal_genome_tests_enabled = true\n');
  await saveConfig(settingsControl, undefined, {
    output_directory: path.join(users, "output"),
    reference_library: reference,
    reference_genome_library: path.join(reference, "genomes"),
    elsewhere: "",
  }, bundle);

  const fast = path.join(users, "reference-new");
  await move({ sourceRoot: source, bundleRoot: bundle, folder: "{{usersWorkspace}}/reference-new", setting: { controlID: "paths", key: "reference_library" } });

  await assert.rejects(stat(reference), { code: "ENOENT" });
  assert.equal(await readFile(path.join(fast, "genomes", "hg38.fa"), "utf8"), "ACGT");
  const settings = await settingsIn(path.join(users, "config.toml"));
  assert.equal(settings.reference_library, fast);
  assert.equal(settings.reference_genome_library, path.join(fast, "genomes"));
  assert.equal(settings.output_directory, path.join(users, "output"));
  assert.equal(settings.cpu_threads, 4, "a setting the editor doesn't show is kept");
  const complete = parseToml(await readFile(path.join(users, "config.toml"), "utf8"));
  assert.deepEqual(complete.server_allowed_paths, ["/allowed", "/shared"], "1087: save and move retain unowned arrays");
  assert.deepEqual({ ...complete.dev }, { real_genome_tests_enabled: true }, "1087: save and move retain TOML tables");

  // A folder that doesn't exist yet only changes the setting.
  const output = path.join(root, "outputs");
  await move({ sourceRoot: source, bundleRoot: bundle, folder: output, setting: { controlID: "paths", key: "output_directory" } });
  assert.equal((await settingsIn(path.join(users, "config.toml"))).output_directory, output);
  await assert.rejects(move({ sourceRoot: source, bundleRoot: bundle, folder: output, setting: { controlID: "paths", key: "elsewhere" } }), /not a folder the app can move/);
});

for (const location of ["users", "home", "source", "bundle", "bundle-parent", "settings", "symlink"]) {
  test(`1087 refuses a setting-folder move containing protected ${location} data without resetting config`, async (t) => {
    const { root, source, bundle, users } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const destinationRoot = await mkdtemp(path.join(tmpdir(), "wgsextract-gui-protected-move-"));
    t.after(() => rm(destinationRoot, { recursive: true, force: true }));
    const destination = path.join(destinationRoot, "moved");
    const external = path.join(root, "external-settings");
    const alias = path.join(root, "users-alias");
    const from = { users, home: root, source, bundle, "bundle-parent": path.dirname(bundle), settings: external, symlink: alias }[location];
    let settingsFile = path.join(users, "config.toml");
    if (location === "settings") {
      await mkdir(external);
      settingsFile = path.join(external, "config.toml");
      await writeFile(path.join(bundle, "state.json"), JSON.stringify({ configFilePaths: { paths: settingsFile } }));
    }
    if (location === "symlink") {
      await symlink(users, alias, "junction");
    }
    const original = `output_directory = ${JSON.stringify(from)}\ncpu_threads = 3\nserver_allowed_paths = ["/allowed"]\n[dev]\nreal_genome_tests_enabled = true\n`;
    await writeFile(settingsFile, original);

    await assert.rejects(move({
      sourceRoot: source, bundleRoot: bundle, folder: destination,
      setting: { controlID: "paths", key: "output_directory" },
    }), /Cannot move/);

    assert.equal(await readFile(settingsFile, "utf8"), original);
    await stat(path.join(source, "manifest.json"));
    await stat(path.join(bundle, "manifest.json"));
    await stat(path.join(users, ".users-workspace"));
    await assert.rejects(stat(destination), { code: "ENOENT" });
  });
}

test("1087 loads an old My Genomes library key and respects a subsequently cleared canonical key", async (t) => {
  const { source, bundle, users } = await fixture(t);
  const { loadConfig } = await import("../dist/web/src/server/config-store.js");
  await prepareBundleWorkspace(manifest, source);
  const control = {
    ...settingsControl,
    settings: [{ id: "mygenomes", key: "mygenomes_library", kind: "path", value: "" }],
  };
  await writeFile(path.join(users, "config.toml"), 'dataset_library = "/saved/datasets"\n');
  assert.equal((await loadConfig(control, undefined, bundle)).values.mygenomes_library, "/saved/datasets");
  await saveConfig(control, undefined, { mygenomes_library: "" }, bundle);
  const saved = parseToml(await readFile(path.join(users, "config.toml"), "utf8"));
  assert.equal(saved.mygenomes_library, "");
  assert.equal(saved.dataset_library, undefined);
});

test("folders that are unsafe to move to are refused and nothing changes", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  await writeFile(path.join(users, "config.toml"), "");
  const occupied = path.join(root, "occupied");
  await mkdir(occupied, { recursive: true });
  await writeFile(path.join(occupied, "keep.txt"), "someone else's");

  for (const [folder, message] of [
    ["relative/path", /absolute/],
    [path.join(source, "inside"), /cannot be inside/],
    [path.join(bundle, "inside"), /cannot be inside/],
    [path.join(users, "inside"), /cannot be inside/],
    [root, /cannot hold/],
    [occupied, /already has files/],
  ]) {
    await assert.rejects(move({ sourceRoot: source, bundleRoot: bundle, folder }), message);
  }

  await stat(path.join(users, "config.toml"));
  assert.equal(await readFile(path.join(occupied, "keep.txt"), "utf8"), "someone else's");
});

test("paths in a workspace are shown with its token, and real paths reach the settings file and commands", async (t) => {
  const { root } = await fixture(t);
  const bundle = path.join(root, "bundle");
  const users = path.join(root, "users");
  const roots = { bundle, users };
  assert.equal(displayWorkspacePath(path.join(users, "output"), roots), "{{usersWorkspace}}/output");
  assert.equal(displayWorkspacePath(path.join(bundle, "logs"), roots), "{{bundleWorkspace}}/logs");
  assert.equal(displayWorkspacePath(users, roots), "{{usersWorkspace}}");
  assert.equal(displayWorkspacePath(`${users}-other`, roots), `${users}-other`);
  assert.equal(displayWorkspacePath("C:\\Users\\Me\\WGS-Extract\\output", { bundle, users: "c:\\users\\me\\wgs-extract" }), "{{usersWorkspace}}/output");
  // The innermost workspace names a path in both.
  assert.equal(displayWorkspacePath(path.join(bundle, "users", "x"), { bundle, users: path.join(bundle, "users") }), "{{usersWorkspace}}/x");
  assert.equal(displayWorkspacePath(path.join(bundle, "x"), { bundle }), "{{bundleWorkspace}}/x");
  assert.equal(relocatePath("/home/me/w/a/b", "/home/me/w", "D:\\w"), "D:\\w\\a\\b");
  assert.equal(relocatePath("/home/me/w/../shared-reference", "/home/me/w", "/mnt/disk/w"), "/home/me/w/../shared-reference");

  const { registerUsersWorkspace } = await import("../dist/web/src/server/users-workspace.js");
  registerUsersWorkspace(bundle, users);
  const context = normalizeContext({ fieldValues: { out: "{{usersWorkspace}}/output", log: "{{bundleWorkspace}}/logs" } }, bundle);
  assert.deepEqual(context.fieldValues, { out: path.join(users, "output"), log: path.join(bundle, "logs") });
  assert.equal(context.usersWorkspacePath, users);
});

function uninstall(root, ...flags) {
  return new Promise((resolve) => {
    execFile(process.execPath, [cli, ...flags, path.join(root, "source")], { env: { ...process.env, ...dataEnvironment(root) } }, (error, stdout, stderr) => {
      resolve({ code: error ? error.code : 0, stdout, stderr });
    });
  });
}

test("uninstall removes the bundle workspace, and the users workspace only when asked", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  // An earlier release's library, kept in the app's data folder, that the settings file still names.
  const oldReference = path.join(bundle, "reference");
  await mkdir(oldReference, { recursive: true });
  await writeFile(path.join(oldReference, "hg38.fa"), "ACGT");
  await writeFile(path.join(users, "config.toml"), `reference_library = ${JSON.stringify(oldReference)}\n`);

  const printed = await uninstall(root, "--print-workspace");
  assert.equal(printed.code, 0, printed.stderr);
  assert.deepEqual(printed.stdout.trim().split(/\r?\n/), [users, oldReference]);

  const kept = await uninstall(root, "--remove-app-data");
  assert.equal(kept.code, 0, kept.stderr);
  await assert.rejects(stat(path.join(bundle, "manifest.json")), { code: "ENOENT" });
  assert.equal(await readFile(path.join(oldReference, "hg38.fa"), "utf8"), "ACGT");
  await stat(path.join(users, "config.toml"));
  await assert.rejects(stat(path.join(applicationSupportDirectory(), "test.app", "settings.json")), { code: "ENOENT" });

  const removed = await uninstall(root, "--remove-workspace", "--remove-app-data");
  assert.equal(removed.code, 0, removed.stderr);
  await assert.rejects(stat(users), { code: "ENOENT" });
  await assert.rejects(stat(oldReference), { code: "ENOENT" });
});

test("uninstall runs no steps from, and deletes nothing of, a folder that isn't the app's", async (t) => {
  const { root, users } = await fixture(t);
  await mkdir(users, { recursive: true });
  await writeFile(path.join(users, "keep.txt"), "mine");

  const result = await uninstall(root, "--remove-workspace", "--remove-app-data");

  assert.equal(result.code, 0, result.stderr);
  assert.match(result.stdout, /setup never ran/);
  assert.equal(await readFile(path.join(users, "keep.txt"), "utf8"), "mine");
});
