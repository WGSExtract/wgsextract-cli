import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const { moveWorkspace } = await import("../dist/web/src/server/workspace-move.js");
const { parseFlatToml } = await import("../dist/shared/rendering.js");

// A bundle with a users workspace and a settings editor whose folders the app can move, in a throwaway home folder.
export const settingsControl = {
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
export const manifest = {
  id: "location.bundle",
  displayName: "Location Fixture",
  summary: "Tests where workspaces live.",
  defaultUsersWorkspace: "~/visible-workspace",
  pages: [{ id: "main", title: "Main", summary: "Main page.", sections: [{ id: "main", controls: [settingsControl] }] }],
};

export function dataEnvironment(root) {
  return {
    HOME: root,
    USERPROFILE: root,
    XDG_DATA_HOME: path.join(root, "data"),
    LOCALAPPDATA: path.join(root, "data"),
    WGSEXTRACT_GUI_APP_DATA_NAME: "test.app",
  };
}

export async function fixture(t, fixtureManifest = manifest) {
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

export function move(request) {
  return moveWorkspace({ manifest, onProgress: () => undefined, signal: new AbortController().signal, ...request });
}

export async function settingsIn(file) {
  return { ...parseFlatToml(await readFile(file, "utf8")) };
}
