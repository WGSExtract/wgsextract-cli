import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { items } from "../../examples/WGSExtract/transforms/reference-data.mjs";

test("Library Bootstrap shows the CLI release rather than a hard-coded version or partial disk size", async () => {
  const cli = new URL("../../../src/wgsextract_cli/", import.meta.url);
  const constants = await readFile(new URL("core/constants.py", cli), "utf8");
  const availableVersion = constants.match(/^BOOTSTRAP_VERSION = "([^"]+)"/m)?.[1];
  assert.ok(availableVersion);
  const catalog = JSON.parse(await readFile(new URL("assets/resource_mirrors.json", cli), "utf8"));
  const downloadBytes = catalog.resources.find((entry) => entry.id === "bootstrap-current").upstream_size_at_audit;
  const expectedSize = `${(downloadBytes / 2 ** 20).toFixed(1)} MiB`;
  const bootstrapRow = (bootstrap) => items({ details: {
    referenceLibrary: { path: path.resolve("../out") },
    bootstrap: { availableVersion, downloadBytes, ...bootstrap },
  } }).items.find((row) => row.id === "bootstrap");

  const current = bootstrapRow({ installed: true, installedVersion: availableVersion });
  assert.equal(current.status, "installed");
  assert.equal(current.values.version, `v${availableVersion}`);
  assert.equal(current.values.size, expectedSize);
  const outdated = bootstrapRow({ installed: true, installedVersion: "0.0.1", updateAvailable: true });
  assert.equal(outdated.status, "update-available");
  assert.equal(outdated.values.version, `v0.0.1 / v${availableVersion}`);
  assert.equal(outdated.values.size, expectedSize);
  const missing = bootstrapRow({ installed: false, availableVersion: "12.34.56" });
  assert.equal(missing.status, "missing");
  assert.equal(missing.values.version, "v12.34.56");
  assert.equal(missing.values.size, expectedSize);
});

test("reference-data transform maps installed and missing resources with sizes, versions, and UI modes", async (t) => {
  const output = path.resolve("../out/tests");
  await mkdir(output, { recursive: true });
  const root = await mkdtemp(path.join(output, "ref-data-transform-"));
  t.after(() => rm(root, { force: true, recursive: true }));

  const downloads = path.join(root, "downloads");
  await mkdir(downloads, { recursive: true });
  const dbsnpVcf = path.join(downloads, "dbsnp_common_hg38.vcf.gz");
  const dbsnpTbi = `${dbsnpVcf}.tbi`;
  const dbsnpSqlite = path.join(downloads, "dbsnp_common_hg38.rsid.sqlite");
  await writeFile(dbsnpVcf, Buffer.alloc(2048));
  await writeFile(dbsnpTbi, Buffer.alloc(512));
  await writeFile(dbsnpSqlite, Buffer.alloc(1024));
  await writeFile(path.join(downloads, "genes_hg38.tsv"), Buffer.alloc(4096));
  await writeFile(path.join(downloads, "cytobands_hg38.tsv"), Buffer.alloc(2048));

  const payload = items({
    details: {
      referenceLibrary: { path: root, exists: true },
      bootstrap: { installed: true },
      geneMap: { installed: true },
      dbsnp: {
        hg38: { vcf: dbsnpVcf, indexed: true, rsidIndex: dbsnpSqlite, rsidIndexValid: true, installed: true },
        hg19: { vcf: "", indexed: false, rsidIndex: "", rsidIndexValid: false, installed: false },
      },
      annotationVcf: { installed: false, file: "" },
      aadr: { installed: false, file: "" },
      ybrowse: { installed: false, file: "" },
      vepCache: { versions: ["116_GRCh38"] },
      spliceai: { installed: false, file: "" },
      alphamissense: { installed: false, file: "" },
      pharmgkb: { installed: false, file: "" },
      annotations: {
        clinvar: { hg38: { file: "", installed: false }, hg19: { file: "", installed: false } },
        revel: { hg38: { file: "", installed: false }, hg19: { file: "", installed: false } },
        gnomad: { hg38: { file: "", installed: false }, hg19: { file: "", installed: false } },
        cadd: { hg38: { file: "", installed: false }, hg19: { file: "", installed: false } },
        gerp: { hg38: { file: "", installed: false }, hg19: { file: "", installed: false } },
        phylop: { hg38: { file: "", installed: false }, hg19: { file: "", installed: false } },
      },
    },
  });

  const byId = new Map(payload.items.map((row) => [row.id, row]));
  assert.equal(payload.displayPaths.ref_path, root);
  assert.equal(byId.get("bootstrap").status, "installed");
  assert.equal(byId.get("bootstrap").tags[0].id, "required");
  assert.equal(byId.get("gene-map").status, "installed");
  assert.equal(byId.get("gene-map").values.build, "37 / 38");
  assert.equal(byId.get("gene-map").values.size, "4.0 KB");
  assert.equal(byId.get("dbsnp-hg38").status, "installed");
  assert.equal(byId.get("dbsnp-hg38").title, "dbSNP Common rsIDs (GRCh38)");
  assert.equal(byId.get("dbsnp-hg38").values.build, "38");
  assert.equal(byId.get("dbsnp-hg38").values.version, "b151 (~37M SNPs)");
  assert.equal(byId.get("dbsnp-hg38").values.size, "3.5 KB");
  assert.equal(byId.get("dbsnp-hg19").status, "missing");
  assert.equal(byId.get("dbsnp-hg19").title, "dbSNP Common rsIDs (GRCh37)");
  assert.equal(byId.get("dbsnp-hg19").values.build, "37");
  assert.equal(byId.get("dbsnp-hg19").values.size, "~1.5 GB");
  assert.equal(byId.get("cytobands-hg38").status, "installed");
  assert.equal(byId.get("cytobands-hg38").title, "Cytobands (GRCh38)");
  assert.equal(byId.get("cytobands-hg38").values.build, "38");
  assert.equal(byId.get("cytobands-hg19").status, "missing");
  assert.equal(byId.get("cytobands-hg19").title, "Cytobands (GRCh37)");
  assert.equal(byId.get("cytobands-hg19").values.build, "37");
  assert.equal(byId.get("vep").status, "installed");
  assert.equal(byId.get("vep").values.version, "116_GRCh38");
  assert.equal(byId.get("spliceai").status, "missing");
  assert.equal(byId.get("spliceai").values.size, "~1.4 GB");
  assert.equal(byId.get("clinvar").advancedOnly, true);
  assert.equal(byId.get("dbsnp-hg38").advancedOnly, undefined);
  assert.equal(byId.get("dbsnp-hg19").advancedOnly, undefined);
});

test("Reference Data rows list installed resources first, then missing ones, each alphabetical", () => {
  const rows = items({ details: {
    referenceLibrary: { path: path.resolve("../out") },
    bootstrap: { installed: false },
    geneMap: { installed: true },
    pharmgkb: { installed: true },
    annotations: { revel: { hg38: { installed: true } } },
  } }).items;
  const installed = rows.filter((row) => row.status !== "missing").map((row) => row.title);
  const missing = rows.filter((row) => row.status === "missing").map((row) => row.title);
  const alphabetical = (titles) => [...titles].sort((a, b) => a.localeCompare(b, "en", { sensitivity: "base" }));

  assert.deepEqual(rows.map((row) => row.title), [...installed, ...missing]);
  assert.deepEqual(installed, ["Gene Map", "PharmGKB Annotations", "REVEL"]);
  assert.deepEqual(missing, alphabetical(missing));
  assert.ok(missing.includes("Library Bootstrap"));
});
