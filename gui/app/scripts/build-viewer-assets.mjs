import { copyFile, mkdir } from "node:fs/promises";
import "./update-igv.mjs";

// dist is already a Tauri resource; new app documents must be included there, not left in the source tree.
const target = new URL("../dist/web/", import.meta.url);
await mkdir(target, { recursive: true });
for (const name of ["viewer.html", "viewer.css"]) {
  await copyFile(new URL(`../web/${name}`, import.meta.url), new URL(name, target));
}
