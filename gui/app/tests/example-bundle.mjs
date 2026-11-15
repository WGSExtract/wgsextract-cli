import { cp, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const examplesRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../examples");
// What running the bundle makes in its folder (see gui/.gitignore): never copied, so a copy starts clean.
const generated = new Set(["config.toml", "state.json", "runtime", "genomes", "output", "reference", "settings", "temp", "logs"]);

/**
 * A copy of an example bundle in a temporary folder, removed after the test. Loading a bundle writes
 * its default config files into it, and test files run in parallel processes, so tests that load or
 * run a bundle use a copy: on Windows, two processes replacing the same config.toml fail with EPERM.
 */
export async function exampleBundleCopy(t, name = "WGSExtract") {
  const root = await mkdtemp(path.join(tmpdir(), `wgsextract-gui-example-${name}-`));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = path.join(examplesRoot, name);
  const copy = path.join(root, name);
  await cp(source, copy, {
    recursive: true,
    filter: (entry) => path.dirname(entry) !== source || !generated.has(path.basename(entry)),
  });
  return copy;
}
