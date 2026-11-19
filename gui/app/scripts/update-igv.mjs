import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";

const source = new URL("../node_modules/igv/", import.meta.url);
const target = new URL("../web/vendor/igv/", import.meta.url);
const { version } = JSON.parse(await readFile(new URL("package.json", source), "utf8"));
const manifest = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
if (manifest.dependencies.igv !== version) {
  throw new Error("Pin the exact igv version in package.json before vendoring.");
}
await mkdir(target, { recursive: true });
const code = await readFile(new URL("dist/igv.min.js", source), "utf8");
if (Buffer.byteLength(code) > 2_000_000) {
  throw new Error("igv.js exceeds the viewer's 2 MB uncompressed asset budget.");
}
await writeFile(new URL("igv.min.js", target), `${code.replace(/^\/\/# sourceMappingURL=.*$/m, "").trimEnd()}\n`);
await copyFile(new URL("LICENSE", source), new URL("LICENSE", target));
console.log(`Vendored igv.js ${version} (${Buffer.byteLength(code)} bytes).`);
