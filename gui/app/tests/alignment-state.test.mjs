import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { fileStateValues } from "../dist/web/src/server/action-runner.js";
import { isCoordinateSortedHeader, isIndexedAlignment, isSortedAlignment } from "../dist/web/src/server/alignment-state.js";
import { localizeManifest, parseTomlStrings } from "../dist/shared/localization.js";
import { commandContextFromState, disabledReason, isActionVisible } from "../dist/shared/rendering.js";
import { visibleActions } from "../dist/shared/ui-mode.js";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(appRoot, "..", "..");
const wgsExtract = path.resolve(appRoot, "..", "examples", "WGSExtract");

/** Builds a valid single-block BGZF BAM file with the given SAM header text. */
function makeBamBuffer(headerText) {
  const textBytes = Buffer.from(headerText, "utf8");
  const payload = Buffer.alloc(8 + textBytes.length + 4);
  payload.write("BAM\x01", 0, "binary");
  payload.writeUInt32LE(textBytes.length, 4);
  textBytes.copy(payload, 8);
  payload.writeUInt32LE(0, 8 + textBytes.length); // n_ref = 0

  const gzip = gzipSync(payload);
  // Insert BGZF FEXTRA flag (0x04) and 6-byte BC subfield (SI1='B', SI2='C', SLEN=2, BSIZE=total-1).
  const totalLen = gzip.length + 8;
  const bgzf = Buffer.alloc(totalLen);
  gzip.copy(bgzf, 0, 0, 10);
  bgzf[3] = (bgzf[3] ?? 0) | 0x04;
  bgzf.writeUInt16LE(6, 10); // XLEN
  bgzf[12] = 66; // 'B'
  bgzf[13] = 67; // 'C'
  bgzf.writeUInt16LE(2, 14); // SLEN
  bgzf.writeUInt16LE(totalLen - 1, 16); // BSIZE
  gzip.copy(bgzf, 18, 10);
  return bgzf;
}

/** Encodes a small non-negative integer as CRAM ITF8. */
function encodeItf8(value) {
  if (value < 0x80) return Buffer.from([value]);
  if (value < 0x4000) return Buffer.from([0x80 | (value >> 8), value & 0xff]);
  if (value < 0x200000) return Buffer.from([0xc0 | (value >> 16), (value >> 8) & 0xff, value & 0xff]);
  throw new Error(`Unsupported test ITF8 value: ${value}`);
}

/** Builds a minimal CRAM v2.1, v3.0, or v3.1 file whose Container 0 holds the SAM header. */
function makeCramBuffer(headerText, { major = 3, minor = 0, gzip = false } = {}) {
  const def = Buffer.alloc(26);
  def.write("CRAM", 0, "ascii");
  def[4] = major;
  def[5] = minor;

  const textBytes = Buffer.from(headerText, "utf8");
  const rawBlock = Buffer.alloc(4 + textBytes.length);
  rawBlock.writeInt32LE(textBytes.length, 0);
  textBytes.copy(rawBlock, 4);
  const blockPayload = gzip ? gzipSync(rawBlock) : rawBlock;

  const blockHeader = Buffer.concat([
    Buffer.from([gzip ? 1 : 0, 0]), // method (0=RAW, 1=GZIP), content_type (0=FILE_HEADER)
    encodeItf8(0), // content_id
    encodeItf8(blockPayload.length),
    encodeItf8(rawBlock.length),
  ]);
  const blockCrc = major >= 3 ? Buffer.alloc(4) : Buffer.alloc(0);
  const block = Buffer.concat([blockHeader, blockPayload, blockCrc]);

  const containerLen = Buffer.alloc(4);
  containerLen.writeInt32LE(block.length, 0);
  // ref_seq_id = -1 (5-byte ITF8: 0xff 0xff 0xff 0xff 0x0f), start=0, span=0, records=0, counter=0, bases=0, num_blocks=1, landmarks=[0]
  const containerHeader = Buffer.concat([
    containerLen,
    Buffer.from([0xff, 0xff, 0xff, 0xff, 0x0f]),
    encodeItf8(0),
    encodeItf8(0),
    encodeItf8(0),
    encodeItf8(0), // record_counter (0 is 1 byte in both ITF8 and LTF8)
    encodeItf8(0), // bases (0 is 1 byte in both ITF8 and LTF8)
    encodeItf8(1), // num_blocks
    encodeItf8(1), // landmark count
    encodeItf8(0), // landmark[0]
    ...(major >= 3 ? [Buffer.alloc(4)] : []), // container CRC32
  ]);

  return Buffer.concat([def, containerHeader, block]);
}

async function localizedBamCommands() {
  const manifestRaw = (await readFile(path.join(wgsExtract, "manifest.json"), "utf8")).replace(/^\s*\/\/.*$/gm, "");
  const manifest = JSON.parse(manifestRaw);
  manifest.pages = await Promise.all(
    manifest.pages.map(async (name) => JSON.parse(await readFile(path.join(wgsExtract, "pages", name), "utf8"))),
  );
  const strings = parseTomlStrings(await readFile(path.join(wgsExtract, "strings", "strings.en.toml"), "utf8"));
  const localized = localizeManifest(manifest, strings);
  return {
    manifest: localized,
    section: localized.pages.find((page) => page.id === "info-bam").sections.find((section) => section.id === "bam-commands"),
  };
}

test("isCoordinateSortedHeader checks @HD SO:coordinate exactly", () => {
  assert.equal(isCoordinateSortedHeader("@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr1\tLN:100\n"), true);
  assert.equal(isCoordinateSortedHeader("@HD\tVN:1.6\tSO:unsorted\n@SQ\tSN:chr1\tLN:100\n"), false);
  assert.equal(isCoordinateSortedHeader("@HD\tVN:1.6\tSO:queryname\n"), false);
  assert.equal(isCoordinateSortedHeader("@SQ\tSN:chr1\tLN:100\n"), false);
  assert.equal(isCoordinateSortedHeader("@CO\tSO:coordinate\n"), false);
});

test("alignment header and index inspection reads actual BAM/CRAM headers instead of filename guesses", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "wgse-align-state-"));
  try {
    // Coordinate-sorted BAM with NO '.sorted.' in its name and NO index yet.
    const sortedNoIndex = path.join(dir, "sample.bam");
    await writeFile(sortedNoIndex, makeBamBuffer("@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr1\tLN:1000\n"));
    assert.equal(await isSortedAlignment(sortedNoIndex), true);
    assert.equal(await isIndexedAlignment(sortedNoIndex), false);

    // Unsorted BAM whose filename contains '.sorted.' AND has a .bai alongside it:
    // must still report isSorted=false from the actual @HD header, and isIndexed=true.
    const unsortedMisleading = path.join(dir, "sample.sorted.bam");
    await writeFile(unsortedMisleading, makeBamBuffer("@HD\tVN:1.6\tSO:unsorted\n@SQ\tSN:chr1\tLN:1000\n"));
    await writeFile(`${unsortedMisleading}.bai`, "index");
    assert.equal(await isSortedAlignment(unsortedMisleading), false);
    assert.equal(await isIndexedAlignment(unsortedMisleading), true);

    // Stray .crai next to a .bam does not mark the .bam as indexed.
    await writeFile(`${sortedNoIndex}.crai`, "wrong-index");
    assert.equal(await isIndexedAlignment(sortedNoIndex), false);

    // CRAM v2.1 (raw), v3.0 (raw), and v3.1 (gzip) headers.
    const cram21 = path.join(dir, "v21.cram");
    const cram30 = path.join(dir, "v30.cram");
    const cram31 = path.join(dir, "v31.cram");
    await writeFile(cram21, makeCramBuffer("@HD\tVN:1.6\tSO:coordinate\n", { major: 2, minor: 1, gzip: false }));
    await writeFile(cram30, makeCramBuffer("@HD\tVN:1.6\tSO:unsorted\n", { major: 3, minor: 0, gzip: false }));
    await writeFile(cram31, makeCramBuffer("@HD\tVN:1.6\tSO:coordinate\n", { major: 3, minor: 1, gzip: true }));
    await writeFile(`${cram31}.crai`, "index");
    assert.equal(await isSortedAlignment(cram21), true);
    assert.equal(await isSortedAlignment(cram30), false);
    assert.equal(await isSortedAlignment(cram31), true);
    assert.equal(await isIndexedAlignment(cram31), true);
    assert.equal(await isIndexedAlignment(cram21), false);

    // fileStateValues reports false for missing files even if named '.sorted.bam'.
    const missing = path.join(dir, "missing.sorted.bam");
    const state = await fileStateValues({ fieldValues: { bam_path: missing, sorted_bam: sortedNoIndex, unsorted_bam: unsortedMisleading } }, dir);
    assert.equal(state["bam_path.exists"], "false");
    assert.equal(state["bam_path.isSorted"], "false");
    assert.equal(state["bam_path.isIndexed"], "false");
    assert.equal(state["sorted_bam.isSorted"], "true");
    assert.equal(state["sorted_bam.isIndexed"], "false");
    assert.equal(state["unsorted_bam.isSorted"], "false");
    assert.equal(state["unsorted_bam.isIndexed"], "true");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("alignment header inspection reads the real repo BAM fixture", async () => {
  const realBam = path.join(repoRoot, "tests", "fixtures", "imputation_hg002", "reads.bam");
  assert.equal(await isSortedAlignment(realBam), true);
});

test("BAM page Sort/Index buttons reflect actual status in Basic (disabled when done) and Advanced (Unsort/Unindex when done)", async () => {
  const { manifest, section } = await localizedBamCommands();

  const sortAndIndexButtons = (uiMode, { bamPath = "/tmp/sample.bam", isSorted = "false", isIndexed = "false" }) => {
    const context = {
      ...commandContextFromState({ manifest, uiMode, fieldValues: { bam_path: bamPath } }),
      fileStateValues: {
        "bam_path.isSorted": isSorted,
        "bam_path.isIndexed": isIndexed,
        "bam_path.pathExtension": bamPath ? "bam" : "",
      },
    };
    return visibleActions(section.actions, uiMode)
      .filter((action) => isActionVisible(action, context))
      .filter((action) => ["bam-sort", "bam-sort-done", "bam-unsort", "bam-index", "bam-index-done", "bam-unindex"].includes(action.id))
      .map((action) => ({
        id: action.id,
        title: action.title,
        disabled: Boolean(disabledReason(action, context)),
        disabledReason: disabledReason(action, context) ?? null,
      }));
  };

  // Basic mode: unsorted & unindexed -> active Sort and Index.
  assert.deepEqual(sortAndIndexButtons("basic", { isSorted: "false", isIndexed: "false" }), [
    { id: "bam-sort", title: "Sort", disabled: false, disabledReason: null },
    { id: "bam-index", title: "Index", disabled: false, disabledReason: null },
  ]);

  // Basic mode: coordinate sorted & unindexed -> disabled Sort, active Index.
  assert.deepEqual(sortAndIndexButtons("basic", { isSorted: "true", isIndexed: "false" }), [
    { id: "bam-sort-done", title: "Sort", disabled: true, disabledReason: "This file is already coordinate sorted." },
    { id: "bam-index", title: "Index", disabled: false, disabledReason: null },
  ]);

  // Basic mode: unsorted & indexed -> active Sort, disabled Index.
  assert.deepEqual(sortAndIndexButtons("basic", { isSorted: "false", isIndexed: "true" }), [
    { id: "bam-sort", title: "Sort", disabled: false, disabledReason: null },
    { id: "bam-index-done", title: "Index", disabled: true, disabledReason: "This file is already indexed." },
  ]);

  // Basic mode: sorted & indexed -> both greyed out / disabled, never Unsort or Unindex.
  assert.deepEqual(sortAndIndexButtons("basic", { isSorted: "true", isIndexed: "true" }), [
    { id: "bam-sort-done", title: "Sort", disabled: true, disabledReason: "This file is already coordinate sorted." },
    { id: "bam-index-done", title: "Index", disabled: true, disabledReason: "This file is already indexed." },
  ]);

  // Advanced mode: unsorted & unindexed -> active Sort and Index.
  assert.deepEqual(sortAndIndexButtons("advanced", { isSorted: "false", isIndexed: "false" }), [
    { id: "bam-sort", title: "Sort", disabled: false, disabledReason: null },
    { id: "bam-index", title: "Index", disabled: false, disabledReason: null },
  ]);

  // Advanced mode: sorted & unindexed -> active Unsort and active Index.
  assert.deepEqual(sortAndIndexButtons("advanced", { isSorted: "true", isIndexed: "false" }), [
    { id: "bam-unsort", title: "Unsort", disabled: false, disabledReason: null },
    { id: "bam-index", title: "Index", disabled: false, disabledReason: null },
  ]);

  // Advanced mode: unsorted & indexed -> active Sort and active Unindex.
  assert.deepEqual(sortAndIndexButtons("advanced", { isSorted: "false", isIndexed: "true" }), [
    { id: "bam-sort", title: "Sort", disabled: false, disabledReason: null },
    { id: "bam-unindex", title: "Unindex", disabled: false, disabledReason: null },
  ]);

  // Advanced mode: sorted & indexed -> active Unsort and active Unindex.
  assert.deepEqual(sortAndIndexButtons("advanced", { isSorted: "true", isIndexed: "true" }), [
    { id: "bam-unsort", title: "Unsort", disabled: false, disabledReason: null },
    { id: "bam-unindex", title: "Unindex", disabled: false, disabledReason: null },
  ]);
});
