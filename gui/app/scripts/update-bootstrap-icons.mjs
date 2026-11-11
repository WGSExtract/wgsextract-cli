import { copyFile, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseIconMapToml } from "../dist/shared/icon-map.js";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const webuiRoot = path.resolve(scriptDir, "..");
const packageRoot = path.join(webuiRoot, "node_modules", "bootstrap-icons");
const sourceFontRoot = path.join(packageRoot, "font");
const vendorRoot = path.join(webuiRoot, "web", "vendor", "bootstrap-icons");
const vendorFontRoot = path.join(vendorRoot, "fonts");
const fullStylesheet = path.join(sourceFontRoot, "bootstrap-icons.css");
const criticalStylesheet = path.join(vendorRoot, "bootstrap-icons-critical.css");

const packageJSON = JSON.parse(await readFile(path.join(packageRoot, "package.json"), "utf8"));

await rm(vendorRoot, { recursive: true, force: true });
await mkdir(vendorFontRoot, { recursive: true });

await copyFile(fullStylesheet, path.join(vendorRoot, "bootstrap-icons.css"));
await copyFile(path.join(sourceFontRoot, "fonts", "bootstrap-icons.woff"), path.join(vendorFontRoot, "bootstrap-icons.woff"));
await copyFile(path.join(sourceFontRoot, "fonts", "bootstrap-icons.woff2"), path.join(vendorFontRoot, "bootstrap-icons.woff2"));
await copyFile(path.join(packageRoot, "LICENSE"), path.join(vendorRoot, "LICENSE"));
await writeFile(
  path.join(vendorRoot, "VERSION.json"),
  `${JSON.stringify({ package: "bootstrap-icons", version: packageJSON.version }, null, 2)}\n`,
  "utf8",
);

const fullCSS = await readFile(fullStylesheet, "utf8");
// The Web UI only loads the critical subset: every Bootstrap icon the built-in and example icon maps name.
const guiRoot = path.resolve(webuiRoot, "..");
const examplesRoot = path.join(guiRoot, "examples");
const iconMapPaths = [
  path.join(guiRoot, "resources", "BuiltinIconMap", "iconmap.toml"),
  ...(await readdir(examplesRoot, { withFileTypes: true }))
    .filter((entry) => entry.isDirectory())
    .map((entry) => path.join(examplesRoot, entry.name, "iconmap.toml")),
];
const iconNames = new Set(["copy"]);
for (const iconMapPath of iconMapPaths) {
  const text = await readFile(iconMapPath, "utf8").catch((error) => (error.code === "ENOENT" ? "" : Promise.reject(error)));
  for (const name of Object.values(parseIconMapToml(text).bootstrap ?? {})) {
    iconNames.add(name);
  }
}

const firstIconRule = /^\.bi-[\w-]+::before \{/m.exec(fullCSS);
if (!firstIconRule?.index) {
  throw new Error("Failed to locate Bootstrap Icons rule prelude");
}

const prelude = fullCSS
  .slice(0, firstIconRule.index)
  .replace(
    /\/\*![\s\S]*?\*\//,
    `/*!
 * Bootstrap Icons v${packageJSON.version} critical subset for GUI for CLI.
 * Copyright 2019-2024 The Bootstrap Authors
 * Licensed under MIT (https://github.com/twbs/icons/blob/main/LICENSE)
 */`,
  );

const fullLines = fullCSS.split("\n");
const criticalRules = [];
for (const iconName of [...iconNames].sort()) {
  const rule = fullLines.find((line) => line.startsWith(`.bi-${iconName}::before `));
  if (!rule) {
    throw new Error(`Failed to locate Bootstrap Icon rule for ${iconName}`);
  }
  criticalRules.push(rule);
}

await writeFile(criticalStylesheet, `${prelude}${criticalRules.join("\n")}\n`, "utf8");

console.log(`Vendored Bootstrap Icons ${packageJSON.version} into ${path.relative(webuiRoot, vendorRoot)}`);
