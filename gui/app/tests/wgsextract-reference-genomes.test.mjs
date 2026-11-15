import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

// The WGSExtract bundle's transform from `wgsextract --json ref library --list` to the
// Reference Genomes table and the reference genome dropdowns (#825).
const { items, options } = await import("../../examples/WGSExtract/transforms/reference-genomes.mjs");

const genomesDir = path.join(path.sep, "refs", "genomes");

function genome(fields) {
    return { source: "GitHub", description: "", sn_count: null, status: "missing", size_bytes: 0, remote_bytes: null, ...fields };
}

function libraryList(genomes) {
    return { schema: "wgsextract.result/1", details: { library: path.dirname(genomesDir), genomes_dir: genomesDir, genomes } };
}

const catalog = libraryList([
    genome({ code: "hs37d5", label: "hs37d5 (Dante) (GitHub) (Rec)", file: "hs37d5.fa.gz", description: "hs37d5 (1K Genome; @US NIH)", sn_count: 86, status: "installed", size_bytes: 1_610_612_736 + 1 }),
    genome({ code: "hs37d5", label: "hs37d5 (Dante) (NIH) (Rec)", source: "NIH-Alt", file: "hs37d5.fa.gz", status: "installed", size_bytes: 3_932_160 }),
    genome({ code: "hg38", label: "hg38 (YSEQ)", file: "hg38.fa.gz", status: "unindexed", size_bytes: 1_310_720 }),
    genome({ code: "THGySeqp", label: "hg19 (yseq)", file: "hg19_yseq.fa.gz", status: "incomplete", remote_bytes: 948_918_295 }),
    genome({ code: "GSD", label: "UU_Cfam_GSD_1.0 (Canis lupus familiaris)", file: "GSD.fna.gz" }),
]);

test("the Reference Genomes table shows each catalog row with its build, size and tags", () => {
    const rows = items(catalog).items;

    assert.deepEqual(rows.map((row) => [row.id, row.title, row.status]), [
        ["hs37d5-GitHub-hs37d5.fa.gz", "hs37d5 (Dante)", "installed"],
        ["hs37d5-NIH-Alt-hs37d5.fa.gz", "hs37d5 (Dante)", "installed"],
        ["hg38-GitHub-hg38.fa.gz", "hg38 (YSEQ)", "unindexed"],
        ["THGySeqp-GitHub-hg19_yseq.fa.gz", "hg19 (yseq)", "incomplete"],
        ["GSD-GitHub-GSD.fna.gz", "UU_Cfam_GSD_1.0 (Canis lupus)", "missing"],
    ]);
    assert.deepEqual(rows[0].values, {
        name: "hs37d5 (Dante)",
        build: "GRCh37 / hg19",
        source: "GitHub",
        code: "hs37d5",
        final: "hs37d5.fa.gz",
        ref: path.join(genomesDir, "hs37d5.fa.gz"),
        size: "1.5 GB",
        description: "hs37d5 (1K Genome; @US NIH)",
        sn: "86",
    });
    assert.deepEqual(rows[0].tags, [{ id: "recommended", title: "Recommended", style: "primary" }]);
    assert.deepEqual(rows[2].tags, []);
    // Sizes on disk, else the download size, with tenths rounded half to even.
    assert.deepEqual(rows.map((row) => row.values.size), ["1.5 GB", "3.8 MB", "1.2 MB", "905.0 MB", ""]);
    assert.deepEqual(rows.map((row) => row.values.build), ["GRCh37 / hg19", "GRCh37 / hg19", "GRCh38 / hg38", "GRCh37 / hg19", "Dog"]);
    // Rows after YSEQ's THGySeqp are for Advanced mode only.
    assert.deepEqual(rows.map((row) => row.advancedOnly === true), [false, false, false, false, true]);
});

test("the reference genome dropdowns offer each installed genome once, the first selected", () => {
    assert.deepEqual(options(catalog).options, [
        { id: path.join(genomesDir, "hs37d5.fa.gz"), title: "hs37d5 (Dante) (GitHub) (Rec)", status: "installed", selected: true },
        { id: path.join(genomesDir, "hg38.fa.gz"), title: "hg38 (YSEQ)", status: "unindexed" },
    ]);
});

test("a listing without genomes fails the data source instead of showing an empty library", () => {
    assert.throws(() => options({ schema: "wgsextract.result/1", status: "error", details: {} }), /printed no genome list/);
});
