import assert from "node:assert/strict";
import test from "node:test";
import { localViewerFiles, localViewerLocus, ViewerFileError } from "../dist/shared/genome-viewer.js";

const file = (name) => new File(["fixture"], name);
const reference = [file("reference.fa"), file("reference.fa.fai")];
const problem = (code) => (error) => error instanceof ViewerFileError && error.code === code;

test("#1090: opening CRAM without the exact local reference is actionable before decoding", () => {
  const tracks = [file("sample.cram"), file("sample.cram.crai")];
  assert.throws(() => localViewerFiles([], tracks), problem("referenceMissing"));
  assert.throws(() => localViewerFiles([reference[0]], tracks), problem("referenceIndexMissing"));
  assert.equal(localViewerFiles(reference, tracks).tracks[0].format, "cram");
});

test("#1090: tracks require matching indexes, including alternate BAM and CRAM names and CSI", () => {
  for (const [data, index] of [
    ["sample.bam", "sample.bam.bai"],
    ["sample.bam", "sample.bai"],
    ["sample.bam", "sample.bam.csi"],
    ["sample.cram", "sample.crai"],
    ["sample.vcf.gz", "sample.vcf.gz.tbi"],
    ["sample.g.vcf.gz", "sample.g.vcf.gz.csi"],
    ["sample.gvcf.gz", "sample.gvcf.gz.tbi"],
    ["sample.vcf.bgz", "sample.vcf.bgz.tbi"],
    ["sample.gvcf.bgz", "sample.gvcf.bgz.csi"],
    ["sample.g.vcf.bgz", "sample.g.vcf.bgz.tbi"],
  ]) {
    assert.throws(() => localViewerFiles(reference, [file(data), file("other.bai")]), problem("indexMissing"));
    const result = localViewerFiles(reference, [file(data), file(index)]);
    assert.equal(result.tracks.length, 1);
    assert.equal(result.tracks[0].url.name, data);
  }
});

test("#1102: installed BGZF references require both indexes before CRAM decoding", () => {
  const tracks = [file("sample.cram"), file("sample.cram.crai")];
  for (const suffix of ["fa.gz", "fasta.gz", "fna.gz"]) {
    const fasta = file(`reference.${suffix}`);
    const fai = file(`${fasta.name}.fai`);
    const gzi = file(`${fasta.name}.gzi`);
    assert.throws(() => localViewerFiles([fasta, fai], tracks), problem("referenceCompressedIndexMissing"));
    assert.throws(() => localViewerFiles([fasta, gzi], tracks), problem("referenceIndexMissing"));
    const result = localViewerFiles([fasta, fai, gzi], tracks);
    assert.equal(result.reference.fastaURL, fasta);
    assert.equal(result.reference.indexURL, fai);
    assert.equal(result.reference.compressedIndexURL, gzi);
  }
  assert.equal(localViewerFiles(reference, tracks).reference.compressedIndexURL, undefined);
});

test("#1090: unindexed, remote and ambiguous selections cannot cause full-file or network fallback", () => {
  assert.throws(() => localViewerFiles(reference, [file("plain.vcf")]), problem("unsupportedFile"));
  assert.throws(() => localViewerFiles(reference, [file("session.json")]), problem("unsupportedFile"));
  assert.throws(() => localViewerFiles(reference, []), problem("tracksMissing"));
  assert.throws(() => localViewerFiles([...reference, file("second.fa")], []), problem("ambiguousFile"));
  assert.throws(() => localViewerFiles(reference, [file("sample.bam"), file("SAMPLE.BAM")]), problem("ambiguousFile"));
});

test("#1090: coordinates are one-based inclusive and online gene/rsID search is never a fallback", () => {
  assert.equal(localViewerLocus(" chr20:1,000-2,000 "), "chr20:1000-2000");
  assert.equal(localViewerLocus("chrM:1-1"), "chrM:1-1");
  for (const locus of ["APOE", "rs429358", "https://example.org", "chr1:0-100", "chr1:2-1", "chr1:1-9007199254740992"]) {
    assert.throws(() => localViewerLocus(locus), problem("invalidLocus"));
  }
});

test("#1090: the shipped genome bundle has a localized built-in viewer entry point", async () => {
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.window = { innerHeight: 900 };
  const { state, createInitialState } = await import("../dist/web/src/client/state.js");
  const { renderNavigation } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), {
    manifest: { id: "wgs-extract", pages: [] },
    labels: { viewerTitle: "Visualiseur de génome" },
    localizationCode: "fr",
  });
  assert.match(renderNavigation(), /data-genome-viewer-open>Visualiseur de génome/);
  assert.doesNotMatch(renderNavigation(), /href="\/viewer/);
  state.manifest.id = "git-helper";
  assert.doesNotMatch(renderNavigation(), /data-genome-viewer-open/);
});

test("#1090: a selected My Genomes alignment has a localized View reads entry without running a job", async () => {
  const { state, createInitialState } = await import("../dist/web/src/client/state.js");
  const { renderTextControl } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), {
    manifest: { id: "wgs-extract", pages: [] },
    labels: { viewerStrings: { viewReads: "Voir les lectures" } },
    fieldValues: { bam_path: "/genomes/HG002.bam" },
  });
  const rendered = renderTextControl({ id: "bam_path", kind: "path", label: "BAM" });
  assert.match(rendered, /data-view-reads="bam_path">Voir les lectures/);
  assert.doesNotMatch(rendered, /data-action-id/);
  state.fieldValues.bam_path = "/genomes/HG002.bam.bai";
  assert.doesNotMatch(renderTextControl({ id: "bam_path", kind: "path" }), /data-view-reads/);
});

test("BAM Info Commands Viewer button enables for BAM/CRAM inputs without running a job", async (t) => {
  const { readFileSync } = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const { exampleBundleCopy } = await import("./example-bundle.mjs");
  const { loadLocalizedBundle } = await import("../dist/web/src/server/bundle-loader.js");
  const { state, createInitialState } = await import("../dist/web/src/client/state.js");
  const { renderActions } = await import("../dist/web/src/client/view/actions.js");
  const bundleRoot = await exampleBundleCopy(t, "WGSExtract");
  const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
  const loaded = await loadLocalizedBundle("en", repoRoot, bundleRoot, bundleRoot);
  const infoBam = loaded.manifest.pages.find((page) => page.id === "info-bam");
  const infoCommands = infoBam.sections.find((section) => section.id === "info-commands");
  const viewerAction = infoCommands.actions.find((action) => action.id === "viewer");
  assert.ok(viewerAction, "BAM Info Commands must include the Viewer action.");
  assert.equal(viewerAction.title, "Viewer");
  assert.deepEqual(viewerAction.viewer, { input: "{{bam_path}}" });

  const schema = JSON.parse(readFileSync(new URL("../../docs/schema/page.schema.json", import.meta.url), "utf8"));
  const allowedKeys = Object.keys(schema.$defs.action.properties);
  assert.deepEqual(Object.keys(viewerAction).filter((key) => !allowedKeys.includes(key)), []);

  const renderFor = (bamPath, manifestID = "wgs-extract") => {
    const context = { fieldValues: { bam_path: bamPath }, configValues: {}, checkedOptions: {} };
    Object.assign(state, createInitialState(), {
      manifest: { ...loaded.manifest, id: manifestID },
      labels: loaded.labels,
      fieldValues: { bam_path: bamPath },
      fileStateValues: new Map([[JSON.stringify({ fieldValues: context.fieldValues, configValues: {}, rowValues: undefined, bundleRootPath: undefined }), {}]]),
    });
    return renderActions([viewerAction], context, { pageID: "info-bam", sectionID: "info-commands" });
  };

  const emptyHtml = renderFor("");
  assert.match(emptyHtml, /data-viewer-action="info-bam\/info-commands\/viewer"/);
  assert.match(emptyHtml, /Missing: BAM\/CRAM/);
  assert.match(emptyHtml, /disabled/);
  assert.doesNotMatch(emptyHtml, /data-action-id/);

  const baiHtml = renderFor("/genomes/HG002.bam.bai");
  assert.match(baiHtml, /Select a BAM or CRAM file/);
  assert.match(baiHtml, /disabled/);

  for (const file of ["/genomes/HG002.bam", "/genomes/HG002.cram"]) {
    const html = renderFor(file);
    assert.match(html, /data-viewer-action="info-bam\/info-commands\/viewer"/);
    assert.match(html, /data-viewer-input="\{\{bam_path\}\}"/);
    assert.match(html, />Viewer</);
    assert.doesNotMatch(html, /disabled/);
    assert.doesNotMatch(html, /data-action-id/);
  }

  assert.equal(renderFor("/genomes/HG002.bam", "git-helper"), "");
});
