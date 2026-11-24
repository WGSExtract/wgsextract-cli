import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { items } from "../../examples/WGSExtract/transforms/reference-data.mjs";

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
