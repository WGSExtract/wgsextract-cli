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
  ]) {
    assert.throws(() => localViewerFiles(reference, [file(data), file("other.bai")]), problem("indexMissing"));
    const result = localViewerFiles(reference, [file(data), file(index)]);
    assert.equal(result.tracks.length, 1);
    assert.equal(result.tracks[0].url.name, data);
  }
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
  assert.match(renderNavigation(), /href="\/viewer\?locale=fr">Visualiseur de génome/);
  state.manifest.id = "git-helper";
  assert.doesNotMatch(renderNavigation(), /href="\/viewer/);
});
