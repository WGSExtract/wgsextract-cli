import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { once } from "node:events";
import { lstat, mkdir, readFile, readdir, readlink, realpath, rename, stat, symlink, unlink, utimes, writeFile } from "node:fs/promises";
import { createRequire, syncBuiltinESMExports } from "node:module";
import path from "node:path";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { fixture, manifest, move, settingsControl } from "./workspace-fixture.mjs";

const { saveConfig } = await import("../dist/web/src/server/config-store.js");
const { prepareBundleWorkspace } = await import("../dist/web/src/server/workspace.js");
const { workspaceActivityDirectory } = await import("../dist/web/src/server/workspace-activity.js");
const { appSettingsPath } = await import("../dist/web/src/server/workspace-location.js");
const { isSameOrInsidePath, relocatePath } = await import("../dist/shared/workspace-paths.js");

/**
 * Makes renames fail as the OS would: of `folder` with EXDEV, as for a move to another volume, so the folder is
 * copied instead; the first onto a file named `failing` with an I/O error, to fail the move after its files moved.
 * `then` runs after each rename that succeeds.
 */
function failRenames(t, { folder, failing, then }) {
  const fsPromises = createRequire(import.meta.url)("node:fs/promises");
  const originalRename = fsPromises.rename;
  let failed = false;
  fsPromises.rename = async (from, to) => {
    if (folder && path.resolve(String(from)) === folder) {
      throw Object.assign(new Error(`EXDEV: cross-device link not permitted, rename '${from}'`), { code: "EXDEV" });
    }
    if (failing && !failed && path.basename(String(to)) === failing) {
      failed = true;
      throw Object.assign(new Error(`EIO: i/o error, rename '${from}'`), { code: "EIO" });
    }
    await originalRename(from, to);
    await then?.(from, to);
  };
  syncBuiltinESMExports();
  t.after(() => {
    fsPromises.rename = originalRename;
    syncBuiltinESMExports();
  });
}

/** Makes the symlink, or skips the test where Windows doesn't let this account make one; returns whether it was made. */
async function trySymlink(t, target, link, type) {
  try {
    await symlink(target, link, type);
    return true;
  }
  catch (error) {
    if (process.platform === "win32" && error.code === "EPERM") {
      t.skip("Windows does not let this account create symbolic links");
      return false;
    }
    throw error;
  }
}

async function startMoveServer(t, root, source) {
  const server = fileURLToPath(new URL("../dist/web/src/server/main.js", import.meta.url));
  const apiToken = randomUUID();
  const portFile = path.join(root, "server-port");
  const child = spawn(process.execPath, [server, "--bundle", source, "--port", "0"], {
    env: { ...process.env, GFC_API_TOKEN: apiToken, GFC_PORT_FILE: portFile },
    stdio: ["ignore", "ignore", "inherit"],
  });
  const exited = once(child, "close");
  t.after(async () => {
    child.kill();
    await exited;
  });
  let port;
  while (!port) {
    assert.equal(child.exitCode, null, "GUI exited before publishing its port");
    assert.equal(child.signalCode, null, "GUI stopped before publishing its port");
    const text = await readFile(portFile, "utf8").catch((error) => {
      if (error.code === "ENOENT") return "";
      throw error;
    });
    if (/^\d+\n$/.test(text)) port = Number(text);
    else await delay(20, undefined, { signal: t.signal });
  }
  return async (body) => {
    const response = await fetch(`http://127.0.0.1:${port}/api/workspace-location/move`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-gfc-token": apiToken },
      body: JSON.stringify(body),
      signal: t.signal,
    });
    assert.equal(response.status, 200);
    return (await response.text()).trim().split("\n").map((line) => JSON.parse(line));
  };
}

async function snapshotTree(root) {
  const entries = [];
  for (const name of (await readdir(root)).sort()) {
    const entry = path.join(root, name);
    // Admission changes the coordination directory, not the user's files.
    if (entry === workspaceActivityDirectory()) continue;
    const info = await lstat(entry);
    const content = info.isSymbolicLink() ? await readlink(entry)
      : info.isDirectory() ? await snapshotTree(entry) : await readFile(entry);
    entries.push({ name, ino: info.ino, modified: info.mtimeMs, content });
  }
  return entries;
}

for (const kind of ["symlink", "junction"]) {
  for (const scenario of ["users workspace", "library folder", "destination"]) {
    const skip = kind === "junction" && process.platform !== "win32" && "Junctions require Windows";
    test(`#1340 refuses a ${kind} at the ${scenario} without changing the filesystem`, { skip, timeout: 15000 }, async (t) => {
      const { root, source, users } = await fixture(t);
      await prepareBundleWorkspace(manifest, source);
      const library = path.join(users, "reference-data");
      const destination = path.join(root, "new-parent", "moved");
      const target = path.join(root, "actual-folder");
      const link = scenario === "users workspace" ? users : scenario === "library folder" ? library : destination;
      if (scenario === "users workspace") {
        await rename(users, target);
      }
      else {
        await mkdir(target);
        if (scenario === "destination") await mkdir(path.dirname(destination));
      }
      let rawTarget = kind === "junction" ? target : path.relative(path.dirname(link), target);
      if (scenario === "library folder" && process.platform !== "win32") {
        const child = path.join(target, "child");
        await mkdir(child);
        await symlink(child, path.join(users, "hop"), "dir");
        rawTarget = "hop/..";
      }
      if (!await trySymlink(t, rawTarget, link, kind === "junction" ? "junction" : "dir")) return;
      if (scenario === "library folder") {
        await writeFile(path.join(users, "config.toml"), `reference_library = ${JSON.stringify(library + path.sep)}\n`);
      }
      const requestMove = await startMoveServer(t, root, source);
      // Leave a timestamp a runtime reload would change even if it rewrote the same bytes.
      await utimes(path.join(users, ".users-workspace"), 1, 1);
      const before = await snapshotTree(root);
      const events = await requestMove({
        folder: destination,
        ...(scenario === "users workspace" ? {} : { controlID: "paths", key: "reference_library" }),
      });
      assert.deepEqual(events.map(({ type }) => type), ["error"]);
      const [failure] = events;
      const reportedTarget = await realpath(target);
      assert.deepEqual(failure.linkedFolder, { link, target: reportedTarget });
      assert.ok(failure.error.includes(link));
      assert.ok(failure.error.includes(reportedTarget));
      assert.match(failure.error, /move the target yourself and update the setting/i);
      assert.deepEqual(await snapshotTree(root), before);
    });
  }
}

test("#1340 refuses a source replaced with a link while running actions stop", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  const target = path.join(root, "actual-workspace");
  const reportedTarget = path.join(await realpath(root), "actual-workspace");
  const folder = path.join(root, "new-parent", "workspace");
  let before;
  await assert.rejects(move({
    sourceRoot: source, bundleRoot: bundle, folder,
    beforeMove: async () => {
      await rename(users, target);
      await symlink(target, users, "junction");
      before = await snapshotTree(root);
    },
  }), { link: users, target: reportedTarget });
  assert.deepEqual(await snapshotTree(root), before);
});

test("#1340 refuses a linked destination written with .. after another link",
  { skip: process.platform === "win32" && "POSIX resolves .. after following the link", timeout: 15000 }, async (t) => {
    const { root, source, users } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const parent = path.join(root, "elsewhere");
    const target = path.join(root, "target");
    const link = path.join(parent, "moved");
    await mkdir(path.join(parent, "sub"), { recursive: true });
    await mkdir(target);
    await symlink(path.join(parent, "sub"), path.join(root, "via"), "dir");
    await symlink(target, link, "dir");
    const library = path.join(users, "reference-data");
    await mkdir(library);
    await writeFile(path.join(users, "config.toml"), `reference_library = ${JSON.stringify(library)}\n`);
    const requestMove = await startMoveServer(t, root, source);
    const folder = `${root}/via/../moved`;
    const before = await snapshotTree(root);
    for (const setting of [{}, { controlID: "paths", key: "reference_library" }]) {
      const [failure] = await requestMove({ folder, ...setting });
      assert.equal(failure.type, "error");
      assert.deepEqual(failure.linkedFolder, { link: folder, target: await realpath(target) });
      assert.deepEqual(await snapshotTree(root), before);
    }
  });

test("#1340 moves files written into an empty library while running actions stop", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  const current = path.join(users, "reference-data");
  const folder = path.join(root, "moved");
  await mkdir(current);
  await writeFile(path.join(users, "config.toml"), `reference_library = ${JSON.stringify(current)}\n`);
  await move({
    sourceRoot: source, bundleRoot: bundle, folder,
    setting: { controlID: "paths", key: "reference_library" },
    beforeMove: () => writeFile(path.join(current, "created-on-shutdown.txt"), "Keep me", { flag: "wx" }),
  });
  assert.equal(await readFile(path.join(folder, "created-on-shutdown.txt"), "utf8"), "Keep me");
  await assert.rejects(lstat(current), { code: "ENOENT" });
});

test("#1340 names the recorded target when a library link is dangling", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  const link = path.join(users, "reference-data");
  const target = path.join("..", "missing-library");
  if (!await trySymlink(t, target, link, "dir")) return;
  await writeFile(path.join(users, "config.toml"), `reference_library = ${JSON.stringify(link)}\n`);
  const before = await snapshotTree(root);
  await assert.rejects(move({
    sourceRoot: source, bundleRoot: bundle, folder: path.join(root, "moved"),
    setting: { controlID: "paths", key: "reference_library" },
  }), { link, target });
  assert.deepEqual(await snapshotTree(root), before);
});

/** A reference library with links into it (absolute, by its real path, relative, a folder) and out of it. */
async function referenceWithLinks(t, root, reference) {
  const shared = path.join(root, "shared");
  await mkdir(path.join(reference, "genomes"), { recursive: true });
  await mkdir(shared, { recursive: true });
  await writeFile(path.join(reference, "genomes", "hg38.fa"), ">chr1\nACGT\n");
  await writeFile(path.join(shared, "external.fa"), ">chrM\nGATC\n");
  const realReference = await realpath(reference);
  const links = {
    "current.fa": path.join(reference, "genomes", "hg38.fa"),
    "current-real.fa": path.join(realReference, "genomes", "hg38.fa"),
    "relative.fa": path.join("genomes", "hg38.fa"),
    "genomes/self.fa": path.join(reference, "genomes", "hg38.fa"),
    "external.fa": path.join(shared, "external.fa"),
    "external-relative.fa": path.relative(reference, path.join(shared, "external.fa")),
  };
  for (const [name, target] of Object.entries(links)) {
    if (!await trySymlink(t, target, path.join(reference, name))) {
      return null;
    }
  }
  // Junctions need no privilege on Windows; elsewhere they are folder symlinks.
  await symlink(path.join(reference, "genomes"), path.join(reference, "genomes-link"), "junction");
  await symlink(shared, path.join(reference, "shared-link"), "junction");
  return { shared, links };
}

async function assertLinksFollowed(reference, { shared, links }) {
  const inside = await realpath(reference);
  for (const name of ["current.fa", "current-real.fa", "relative.fa", "genomes/self.fa"]) {
    const resolved = await realpath(path.join(reference, name));
    assert.ok(resolved.startsWith(inside + path.sep), `${name} points into the moved folder, not at ${resolved}`);
    assert.equal(await readFile(path.join(reference, name), "utf8"), ">chr1\nACGT\n");
  }
  assert.equal(await readlink(path.join(reference, "relative.fa")), links["relative.fa"], "a relative link stays relative");
  assert.equal(await realpath(path.join(reference, "genomes-link")), path.join(inside, "genomes"));
  assert.equal(await readlink(path.join(reference, "external.fa")), links["external.fa"], "a link out of the folder is kept");
  assert.equal(await realpath(path.join(reference, "external-relative.fa")), await realpath(path.join(shared, "external.fa")));
  assert.ok((await lstat(path.join(reference, "shared-link"))).isSymbolicLink(), "a folder outside is linked, not copied in");
  assert.equal(await realpath(path.join(reference, "shared-link")), await realpath(shared));
}

for (const volume of ["the same volume", "another volume"]) {
  test(`1239 moving the users workspace to ${volume} keeps its internal symlinks pointing inside it`, async (t) => {
    const { root, source, bundle, users } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const expected = await referenceWithLinks(t, root, path.join(users, "reference"));
    if (!expected) {
      return;
    }

    if (volume === "another volume") {
      failRenames(t, { folder: users });
    }

    const chosen = path.join(root, "big drive", "deeper", "wgs");
    await move({ sourceRoot: source, bundleRoot: bundle, folder: chosen });

    await assert.rejects(stat(users), { code: "ENOENT" });
    await assertLinksFollowed(path.join(chosen, "reference"), expected);
  });

  test(`1239 moving the reference library to ${volume} keeps its internal symlinks pointing inside it`, async (t) => {
    const { root, source, bundle, users } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const reference = path.join(users, "reference");
    const expected = await referenceWithLinks(t, root, reference);
    if (!expected) {
      return;
    }
    await saveConfig(settingsControl, undefined, { reference_library: reference }, bundle);
    if (volume === "another volume") {
      failRenames(t, { folder: reference });
    }

    const fast = path.join(root, "fast", "disk", "reference");
    await move({ sourceRoot: source, bundleRoot: bundle, folder: fast, setting: { controlID: "paths", key: "reference_library" } });

    await assert.rejects(stat(reference), { code: "ENOENT" });
    await assertLinksFollowed(fast, expected);
  });
}

for (const linkConfig of [false, true]) {
  test(`#1322: cross-volume cleanup failure retires the old ${linkConfig ? "linked" : "regular"} config`, async (t) => {
    const { root, source, bundle, users } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const oldFile = path.join(users, "config.toml");
    const realFile = linkConfig ? path.join(users, "real-settings.toml") : oldFile;
    await writeFile(realFile, "cpu_threads = 3\n");
    if (linkConfig && !await trySymlink(t, path.basename(realFile), oldFile, "file")) return;
    // Redirects name a link's target by its real path (no 8.3 short names on Windows runners).
    const realFileKey = await realpath(realFile);
    const external = path.join(root, "external");
    await mkdir(external);
    const externalConfig = path.join(external, "chosen.toml");
    await writeFile(externalConfig, "cpu_threads = 4\n");
    await symlink(external, path.join(users, "settings"), "junction");
    await writeFile(path.join(bundle, "state.json"), JSON.stringify({
      configFilePaths: { paths: path.join(users, "settings", "chosen.toml") },
    }));
    const target = path.join(root, "new-drive");
    failRenames(t, { folder: users });
    const fsPromises = createRequire(import.meta.url)("node:fs/promises");
    const originalRm = fsPromises.rm;
    fsPromises.rm = async (candidate, options) => {
      if (candidate === users) {
        throw Object.assign(new Error("Windows kept a file open in the old folder"), { code: "EPERM" });
      }
      return originalRm(candidate, options);
    };
    syncBuiltinESMExports();
    t.after(() => {
      fsPromises.rm = originalRm;
      syncBuiltinESMExports();
    });

    await move({ sourceRoot: source, bundleRoot: bundle, folder: target });

    await stat(users);
    await assert.rejects(stat(oldFile), { code: "ENOENT" });
    const newFile = path.join(target, "config.toml");
    const newRealFile = path.join(target, path.basename(realFile));
    assert.match(await readFile(newFile, "utf8"), /cpu_threads = 3/);
    assert.match(await readFile(externalConfig, "utf8"), /cpu_threads = 4/);
    assert.match(await readFile(path.join(target, "settings", "chosen.toml"), "utf8"), /cpu_threads = 4/);
    const redirects = JSON.parse(await readFile(appSettingsPath(), "utf8")).movedConfigPaths;
    assert.equal(redirects[oldFile], newFile);
    assert.equal(redirects[realFileKey], newRealFile);
    await assert.rejects(stat(realFile), { code: "ENOENT" });
  });
}

for (const [step, failing] of [["saving the app's state", "state.json"], ["relinking", "current.fa"]]) {
  test(`1239 a move that fails ${step} after renaming the folder puts its symlinks back as they were`, async (t) => {
    const { root, source, bundle, users } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const reference = path.join(users, "reference");
    const expected = await referenceWithLinks(t, root, reference);
    if (!expected) {
      return;
    }
    await writeFile(path.join(bundle, "state.json"), JSON.stringify({ fieldValues: { reference } }));
    failRenames(t, { failing });

    const chosen = path.join(root, "big drive", "wgs");
    await assert.rejects(move({ sourceRoot: source, bundleRoot: bundle, folder: chosen }), /EIO/);

    await assert.rejects(stat(chosen), { code: "ENOENT" });
    for (const name of ["current.fa", "current-real.fa", "external.fa"]) {
      assert.equal(await readlink(path.join(reference, name)), expected.links[name]);
    }
    await assertLinksFollowed(reference, expected);
  });
}

test("1239 a move cancelled after its folder was renamed is rolled back, not saved", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  const reference = path.join(users, "reference");
  const expected = await referenceWithLinks(t, root, reference);
  if (!expected) {
    return;
  }
  const chosen = path.join(root, "big drive", "wgs");
  const client = new AbortController();
  // The client goes away once the folder is at its new place.
  failRenames(t, { then: (_from, to) => path.resolve(String(to)) === chosen && client.abort() });

  await assert.rejects(move({ sourceRoot: source, bundleRoot: bundle, folder: chosen, signal: client.signal }), { name: "AbortError" });

  await assert.rejects(stat(chosen), { code: "ENOENT" });
  await assertLinksFollowed(reference, expected);
});

for (const volume of ["the same volume", "another volume"]) {
  test(`1239 moving a library reached through a folder link to ${volume} keeps a link that climbs out of it on the same file`, { skip: process.platform === "win32" && "Windows follows .. in a link target by name" }, async (t) => {
    const { root, source, bundle } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const real = path.join(root, "a", "b", "real");
    await mkdir(path.join(real, "reference"), { recursive: true });
    await writeFile(path.join(root, "a", "b", "hg38.fa"), ">chr1\nACGT\n");
    await writeFile(path.join(root, "hg38.fa"), ">decoy\nNNNN\n");
    await symlink(real, path.join(root, "via"));
    // Set by way of the link: ../.. from the real folder is a/b, but spelled from via/reference it reads as the root.
    const reference = path.join(root, "via", "reference");
    await symlink(path.join("..", "..", "hg38.fa"), path.join(reference, "up.fa"));
    assert.equal(await readFile(path.join(reference, "up.fa"), "utf8"), ">chr1\nACGT\n");
    await saveConfig(settingsControl, undefined, { reference_library: reference }, bundle);
    if (volume === "another volume") {
      failRenames(t, { folder: reference });
    }

    const fast = path.join(root, "fast", "reference");
    await move({ sourceRoot: source, bundleRoot: bundle, folder: fast, setting: { controlID: "paths", key: "reference_library" } });

    assert.equal(await readFile(path.join(fast, "up.fa"), "utf8"), ">chr1\nACGT\n");
  });
}

test("1239 a link the CLI replaced with a file while the folder moved is kept, and the move rolled back", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  const reference = path.join(users, "reference");
  const expected = await referenceWithLinks(t, root, reference);
  if (!expected) {
    return;
  }
  const chosen = path.join(root, "big drive", "wgs");
  // Another writer, such as the CLI sharing the workspace, swaps a link for a file once the folder is at its new place.
  failRenames(t, {
    then: async (_from, to) => {
      if (path.resolve(String(to)) === chosen) {
        await unlink(path.join(chosen, "reference", "current.fa"));
        await writeFile(path.join(chosen, "reference", "current.fa"), ">new\nGGGG\n");
      }
    },
  });

  await assert.rejects(move({ sourceRoot: source, bundleRoot: bundle, folder: chosen }), /changed during the move/);

  await assert.rejects(stat(chosen), { code: "ENOENT" });
  assert.equal(await readFile(path.join(reference, "current.fa"), "utf8"), ">new\nGGGG\n");
  assert.equal(await readlink(path.join(reference, "current-real.fa")), expected.links["current-real.fa"]);
});

const posixOnly = { skip: process.platform === "win32" && "Windows reads .. in a path by name and takes no backslash in a file name" };

for (const volume of ["the same volume", "another volume"]) {
  test(`1239 moving a library to ${volume} keeps links whose meaning depends on how POSIX reads their spelling on the same file`, posixOnly, async (t) => {
    const { root, source, bundle, users } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const reference = path.join(users, "reference");
    await mkdir(path.join(reference, "genomes"), { recursive: true });
    await writeFile(path.join(reference, "genomes", "back\\slash.fa"), ">bs\nAAAA\n");
    await writeFile(path.join(users, "outside.fa"), ">out\nCCCC\n");
    const fast = path.join(root, "fast", "reference");
    await mkdir(path.dirname(fast), { recursive: true });
    await writeFile(path.join(path.dirname(fast), "outside.fa"), ">decoy\nNNNN\n");
    // A backslash is part of a POSIX file name; `alias/..` climbs from where alias leads; an absolute `..` walks through the folder.
    await symlink(path.join(reference, "genomes", "back\\slash.fa"), path.join(reference, "backslash.fa"));
    await symlink(".", path.join(reference, "alias"));
    await symlink("alias/../outside.fa", path.join(reference, "climb.fa"));
    assert.equal(await readFile(path.join(reference, "climb.fa"), "utf8"), ">out\nCCCC\n");
    await symlink(`${reference}${path.sep}..${path.sep}outside.fa`, path.join(reference, "absolute-climb.fa"));
    await saveConfig(settingsControl, undefined, { reference_library: reference }, bundle);
    if (volume === "another volume") {
      failRenames(t, { folder: reference });
    }

    await move({ sourceRoot: source, bundleRoot: bundle, folder: fast, setting: { controlID: "paths", key: "reference_library" } });

    assert.equal(await readFile(path.join(fast, "backslash.fa"), "utf8"), ">bs\nAAAA\n");
    assert.equal(await readFile(path.join(fast, "climb.fa"), "utf8"), ">out\nCCCC\n");
    assert.equal(await readFile(path.join(fast, "absolute-climb.fa"), "utf8"), ">out\nCCCC\n");
  });

  test(`1239 moving a library set with a .. after a folder link to ${volume} moves the folder the OS finds there`, posixOnly, async (t) => {
    const { root, source, bundle } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const real = path.join(root, "a", "b", "reference");
    await mkdir(path.join(real, "genomes"), { recursive: true });
    await writeFile(path.join(real, "genomes", "hg38.fa"), ">chr1\nACGT\n");
    await symlink(path.join(root, "a", "b", "c"), path.join(root, "via"));
    await mkdir(path.join(root, "a", "b", "c"));
    await symlink(path.join(await realpath(real), "genomes", "hg38.fa"), path.join(real, "current.fa"));
    // Read by the OS, via/.. is a/b; read by name, it is the root, whose own reference folder a link leads out to.
    await mkdir(path.join(root, "reference"));
    await writeFile(path.join(root, "reference", "hg38.fa"), ">elsewhere\nTTTT\n");
    await symlink(path.join(root, "reference", "hg38.fa"), path.join(real, "elsewhere.fa"));
    const reference = `${path.join(root, "via")}${path.sep}..${path.sep}reference`;
    await saveConfig(settingsControl, undefined, { reference_library: reference }, bundle);
    if (volume === "another volume") {
      failRenames(t, { folder: path.resolve(reference) });
    }

    const fast = path.join(root, "fast", "reference");
    await move({ sourceRoot: source, bundleRoot: bundle, folder: fast, setting: { controlID: "paths", key: "reference_library" } });

    assert.equal(await readFile(path.join(fast, "current.fa"), "utf8"), ">chr1\nACGT\n");
    assert.ok((await realpath(path.join(fast, "current.fa"))).startsWith(await realpath(fast)));
    assert.equal(await readFile(path.join(fast, "elsewhere.fa"), "utf8"), ">elsewhere\nTTTT\n");
  });

  test(`1239 moving a library to ${volume} under a new name keeps a link whose first .. leaves it and a later one lands back by name`, posixOnly, async (t) => {
    const { root, source, bundle, users } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const reference = path.join(users, "reference");
    await mkdir(path.join(reference, "tmp"), { recursive: true });
    await writeFile(path.join(reference, "hg38.fa"), ">chr1\nACGT\n");
    await symlink(".", path.join(reference, "alias"));
    await symlink(".", path.join(reference, "reference"));
    // alias/.. leaves for the parent, where reference is the folder itself by its old name; read by name, it never leaves.
    await symlink("alias/../reference/tmp/../hg38.fa", path.join(reference, "current.fa"));
    assert.equal(await readFile(path.join(reference, "current.fa"), "utf8"), ">chr1\nACGT\n");
    await saveConfig(settingsControl, undefined, { reference_library: reference }, bundle);
    if (volume === "another volume") {
      failRenames(t, { folder: reference });
    }

    const library = path.join(root, "fast", "library");
    await move({ sourceRoot: source, bundleRoot: bundle, folder: library, setting: { controlID: "paths", key: "reference_library" } });

    assert.equal(await readFile(path.join(library, "current.fa"), "utf8"), ">chr1\nACGT\n");
  });

  test(`1239 moving a library to ${volume} keeps links that reach a file through a .. and a version link following that link`, async (t) => {
    const { root, source, bundle, users } = await fixture(t);
    await prepareBundleWorkspace(manifest, source);
    const reference = path.join(users, "reference");
    for (const version of ["v2", "v3"]) {
      await mkdir(path.join(reference, version), { recursive: true });
      await writeFile(path.join(reference, version, "hg38.fa"), `>${version}\n`);
    }
    await mkdir(path.join(reference, "sub"));
    await mkdir(path.join(reference, "genomes"));
    await symlink("v2", path.join(reference, "current"), "junction");
    const links = { "sub/x.fa": "../current/hg38.fa", "absolute.fa": `${reference}${path.sep}genomes${path.sep}..${path.sep}current${path.sep}hg38.fa` };
    for (const [name, target] of Object.entries(links)) {
      if (!await trySymlink(t, target, path.join(reference, name))) {
        return;
      }
    }
    await saveConfig(settingsControl, undefined, { reference_library: reference }, bundle);
    if (volume === "another volume") {
      failRenames(t, { folder: reference });
    }

    const fast = path.join(root, "fast", "reference");
    await move({ sourceRoot: source, bundleRoot: bundle, folder: fast, setting: { controlID: "paths", key: "reference_library" } });
    await unlink(path.join(fast, "current"));
    await symlink(path.join(fast, "v3"), path.join(fast, "current"), "junction");

    for (const name of Object.keys(links)) {
      assert.equal(await readFile(path.join(fast, name), "utf8"), ">v3\n", `${name} still follows current`);
    }
  });
}

test("1239 files beside a link, named like a move's temporary entries, survive relinking it", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  const reference = path.join(users, "reference");
  const expected = await referenceWithLinks(t, root, reference);
  if (!expected) {
    return;
  }
  const bystanders = [`current.fa.relinked-${process.pid}`, `current.fa.relinking-${process.pid}`];
  for (const name of bystanders) {
    await writeFile(path.join(reference, name), `${name}\n`);
  }

  const chosen = path.join(root, "big drive", "wgs");
  await move({ sourceRoot: source, bundleRoot: bundle, folder: chosen });

  for (const name of bystanders) {
    assert.equal(await readFile(path.join(chosen, "reference", name), "utf8"), `${name}\n`);
  }
  await assertLinksFollowed(path.join(chosen, "reference"), expected);
});

test("1239 a path on a Windows share spelled in other letter case is inside the folder moving from it", () => {
  const share = "\\\\NAS\\share\\workspace";
  const target = "\\\\nas\\share\\WORKSPACE\\genomes\\hg38.fa";
  assert.ok(isSameOrInsidePath(share, target));
  assert.equal(relocatePath(target, share, "D:\\wgs"), "D:\\wgs\\genomes\\hg38.fa");
});

/** Windows for an account that may not make symbolic links: folder links must be junctions, and file links fail. */
function asWindowsWithoutLinkRights(t) {
  const fsPromises = createRequire(import.meta.url)("node:fs/promises");
  const originalSymlink = fsPromises.symlink;
  const platform = Object.getOwnPropertyDescriptor(process, "platform");
  fsPromises.symlink = async (target, link, type) => {
    if (type !== "junction") {
      throw Object.assign(new Error(`EPERM: operation not permitted, symlink '${target}' -> '${link}'`), { code: "EPERM" });
    }
    return originalSymlink(target, link, type);
  };
  syncBuiltinESMExports();
  Object.defineProperty(process, "platform", { ...platform, value: "win32" });
  t.after(() => {
    Object.defineProperty(process, "platform", platform);
    fsPromises.symlink = originalSymlink;
    syncBuiltinESMExports();
  });
}

async function referenceMovingTo(t, folder) {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  const reference = path.join(users, "reference");
  await mkdir(path.join(reference, "genomes", "v2"), { recursive: true });
  await saveConfig(settingsControl, undefined, { reference_library: reference }, bundle);
  const request = { sourceRoot: source, bundleRoot: bundle, folder: path.join(root, ...folder), setting: { controlID: "paths", key: "reference_library" } };
  return { reference, request };
}

test("1239 on Windows without link rights, a folder copied to another volume gets junctions into its new place", async (t) => {
  const { reference, request } = await referenceMovingTo(t, ["other volume", "reference"]);
  await symlink(path.join("genomes", "v2"), path.join(reference, "latest"), "junction");
  await symlink(path.join(reference, "genomes"), path.join(reference, "all"), "junction");
  failRenames(t, { folder: reference });
  asWindowsWithoutLinkRights(t);

  await move(request);

  const inside = await realpath(request.folder);
  assert.equal(await realpath(path.join(request.folder, "latest")), path.join(inside, "genomes", "v2"));
  assert.equal(await realpath(path.join(request.folder, "all")), path.join(inside, "genomes"));
});

test("1239 on Windows without link rights, a renamed folder whose file link can't be remade is put back whole", async (t) => {
  const { reference, request } = await referenceMovingTo(t, ["moved", "reference"]);
  await writeFile(path.join(reference, "genomes", "v2", "hg38.fa"), ">chr1\nACGT\n");
  const links = { "a-all": path.join(reference, "genomes"), "b-current.fa": path.join(reference, "genomes", "v2", "hg38.fa"), "c-latest": path.join(reference, "genomes", "v2") };
  for (const [name, target] of Object.entries(links)) {
    if (!await trySymlink(t, target, path.join(reference, name), name.endsWith(".fa") ? "file" : "junction")) {
      return;
    }
  }
  asWindowsWithoutLinkRights(t);

  await assert.rejects(move(request), /Developer Mode/);

  await assert.rejects(stat(request.folder), { code: "ENOENT" });
  for (const [name, target] of Object.entries(links)) {
    assert.equal(path.resolve(await readlink(path.join(reference, name))), target, `${name} is as it was`);
  }
  assert.equal(await readFile(path.join(reference, "b-current.fa"), "utf8"), ">chr1\nACGT\n");
});

test("1239 on Windows without link rights, a renamed folder's chains of folder links all become junctions", async (t) => {
  const { reference, request } = await referenceMovingTo(t, ["moved", "reference"]);
  // In either listing order, one chain's first link is remade while the link it names still holds the old place.
  const genomes = path.join(reference, "genomes");
  const links = { a: path.join(reference, "z"), z: genomes, y: path.join(reference, "b"), b: genomes };
  for (const [name, target] of Object.entries(links)) {
    await symlink(target, path.join(reference, name), "junction");
  }
  asWindowsWithoutLinkRights(t);

  await move(request);

  const inside = await realpath(request.folder);
  for (const name of Object.keys(links)) {
    assert.equal(await realpath(path.join(request.folder, name)), path.join(inside, "genomes"), name);
  }
});

test("1239 on Windows, a renamed folder holding a deduplicated file, listed as a link but not one, still moves", async (t) => {
  const { reference, request } = await referenceMovingTo(t, ["moved", "reference"]);
  await writeFile(path.join(reference, "genomes", "v2", "hg38.fa"), ">chr1\nACGT\n");
  if (!await trySymlink(t, path.join("genomes", "v2", "hg38.fa"), path.join(reference, "dedup.fa"))) {
    return;
  }
  // Windows lists every reparse point as a link, but reading one that is not a symlink or junction fails.
  const fsPromises = createRequire(import.meta.url)("node:fs/promises");
  const originalReadlink = fsPromises.readlink;
  fsPromises.readlink = async (link, options) => {
    if (path.basename(String(link)) === "dedup.fa") {
      throw Object.assign(new Error(`EINVAL: invalid argument, readlink '${link}'`), { code: "EINVAL" });
    }
    return originalReadlink(link, options);
  };
  syncBuiltinESMExports();
  t.after(() => {
    fsPromises.readlink = originalReadlink;
    syncBuiltinESMExports();
  });
  asWindowsWithoutLinkRights(t);

  await move(request);

  assert.equal(await readFile(path.join(request.folder, "dedup.fa"), "utf8"), ">chr1\nACGT\n");
});

test("1239 on Windows without link rights, a failed move puts back folder links it remade as junctions", async (t) => {
  const { reference, request } = await referenceMovingTo(t, ["moved", "reference"]);
  await mkdir(path.join(reference, "sub"));
  // Climbs out of the folder and back in by its name, so the move rewrites it; Windows can only make it a junction.
  await symlink(["..", "..", "reference", "genomes", "v2"].join(path.sep), path.join(reference, "sub", "latest"), "junction");
  failRenames(t, { failing: "config.toml" });
  asWindowsWithoutLinkRights(t);

  await assert.rejects(move(request), /EIO/);

  await assert.rejects(stat(request.folder), { code: "ENOENT" });
  assert.equal(await realpath(path.join(reference, "sub", "latest")), await realpath(path.join(reference, "genomes", "v2")));
});
