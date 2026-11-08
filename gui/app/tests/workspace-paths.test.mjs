import assert from "node:assert/strict";
import test from "node:test";

const { displayWorkspacePath, expandWorkspacePath } = await import("../dist/shared/workspace-paths.js");

const windowsRoot = "C:\\Users\\me\\AppData\\Local\\gui-for-cli\\BundleWorkspaces\\wgs-extract";

test("paths inside the bundle workspace show as {{bundleWorkspace}}", () => {
  assert.equal(displayWorkspacePath(`${windowsRoot}\\output`, windowsRoot), "{{bundleWorkspace}}\\output");
  assert.equal(displayWorkspacePath(windowsRoot, windowsRoot), "{{bundleWorkspace}}");
  assert.equal(displayWorkspacePath("/home/me/ws/genomes/a.bam", "/home/me/ws/"), "{{bundleWorkspace}}/genomes/a.bam");
  // Windows paths compare without case, as the file system does.
  assert.equal(displayWorkspacePath(`${windowsRoot.toLowerCase()}\\reference`, windowsRoot), "{{bundleWorkspace}}\\reference");
});

test("paths outside the bundle workspace are shown as they are", () => {
  assert.equal(displayWorkspacePath(`${windowsRoot}-old\\output`, windowsRoot), `${windowsRoot}-old\\output`);
  assert.equal(displayWorkspacePath("D:\\genomes\\a.bam", windowsRoot), "D:\\genomes\\a.bam");
  assert.equal(displayWorkspacePath("/Home/me/ws/a", "/home/me/ws"), "/Home/me/ws/a");
  assert.equal(displayWorkspacePath("", windowsRoot), "");
  assert.equal(displayWorkspacePath(`${windowsRoot}\\output`, ""), `${windowsRoot}\\output`);
});

test("a {{bundleWorkspace}} path typed or shown in a field becomes the real folder", () => {
  assert.equal(expandWorkspacePath("{{bundleWorkspace}}/settings/config.toml", windowsRoot), `${windowsRoot}\\settings\\config.toml`);
  assert.equal(expandWorkspacePath("{{bundleWorkspace}}/genomes", "/home/me/ws"), "/home/me/ws/genomes");
  assert.equal(expandWorkspacePath("D:\\genomes", windowsRoot), "D:\\genomes");
  for (const value of [`${windowsRoot}\\output`, "/home/me/ws/genomes/a.bam"]) {
    const root = value.startsWith("/") ? "/home/me/ws" : windowsRoot;
    assert.equal(expandWorkspacePath(displayWorkspacePath(value, root), root), value);
  }
});
