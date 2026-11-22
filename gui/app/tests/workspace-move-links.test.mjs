import assert from "node:assert/strict";
import { lstat, mkdir, readFile, readlink, realpath, stat, symlink, writeFile } from "node:fs/promises";
import { createRequire, syncBuiltinESMExports } from "node:module";
import path from "node:path";
import test from "node:test";
import { fixture, manifest, move, settingsControl } from "./workspace-fixture.mjs";

const { saveConfig } = await import("../dist/web/src/server/config-store.js");
const { prepareBundleWorkspace } = await import("../dist/web/src/server/workspace.js");

/**
 * Makes renames fail as the OS would: of `folder` with EXDEV, as for a move to another volume, so the folder is
 * copied instead; onto a file named `failing` with an I/O error, to fail the move after its files moved.
 */
function failRenames(t, { folder, failing }) {
  const fsPromises = createRequire(import.meta.url)("node:fs/promises");
  const originalRename = fsPromises.rename;
  fsPromises.rename = async (from, to) => {
    if (folder && path.resolve(String(from)) === folder) {
      throw Object.assign(new Error(`EXDEV: cross-device link not permitted, rename '${from}'`), { code: "EXDEV" });
    }
    if (failing && path.basename(String(to)) === failing) {
      throw Object.assign(new Error(`EIO: i/o error, rename '${from}'`), { code: "EIO" });
    }
    return originalRename(from, to);
  };
  syncBuiltinESMExports();
  t.after(() => {
    fsPromises.rename = originalRename;
    syncBuiltinESMExports();
  });
}

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
  try {
    for (const [name, target] of Object.entries(links)) {
      await symlink(target, path.join(reference, name));
    }
  }
  catch (error) {
    if (process.platform === "win32" && error.code === "EPERM") {
      t.skip("Windows does not let this account create symbolic links");
      return null;
    }
    throw error;
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

test("1239 a move that fails after renaming the folder puts its symlinks back as they were", async (t) => {
  const { root, source, bundle, users } = await fixture(t);
  await prepareBundleWorkspace(manifest, source);
  const reference = path.join(users, "reference");
  const expected = await referenceWithLinks(t, root, reference);
  if (!expected) {
    return;
  }
  await writeFile(path.join(bundle, "state.json"), JSON.stringify({ fieldValues: { reference } }));
  failRenames(t, { failing: "state.json" });

  const chosen = path.join(root, "big drive", "wgs");
  await assert.rejects(move({ sourceRoot: source, bundleRoot: bundle, folder: chosen }), /EIO/);

  await assert.rejects(stat(chosen), { code: "ENOENT" });
  for (const name of ["current.fa", "current-real.fa", "external.fa"]) {
    assert.equal(await readlink(path.join(reference, name)), expected.links[name]);
  }
  await assertLinksFollowed(reference, expected);
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
    await symlink(target, path.join(reference, name), name.endsWith(".fa") ? "file" : "junction");
  }
  asWindowsWithoutLinkRights(t);

  await assert.rejects(move(request), /Developer Mode/);

  await assert.rejects(stat(request.folder), { code: "ENOENT" });
  for (const [name, target] of Object.entries(links)) {
    assert.equal(path.resolve(await readlink(path.join(reference, name))), target, `${name} is as it was`);
  }
  assert.equal(await readFile(path.join(reference, "b-current.fa"), "utf8"), ">chr1\nACGT\n");
});
