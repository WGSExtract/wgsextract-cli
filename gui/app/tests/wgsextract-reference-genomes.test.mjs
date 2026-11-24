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
    genome({ code: "hs37d5", tag: "1K37g", label: "hs37d5 (Dante) (GitHub) (Rec)", file: "hs37d5.fa.gz", description: "hs37d5 (1K Genome; @US NIH)", sn_count: 86, status: "installed" }),
    genome({ code: "hs37d5", tag: "1K37g", label: "hs37d5 (Dante) (NIH) (Rec)", source: "NIH-Alt", file: "hs37d5.fa.gz", status: "installed" }),
    genome({ code: "hg38", tag: "hg38h", label: "hg38 (YSEQ)", file: "hg38.fa.gz", status: "unindexed" }),
    genome({ code: "THGySeqp", tag: "T2T HG002", label: "hg38+HG002 Y v2 (by YSEQ)", file: "hg38_CP086569.fasta.gz", status: "incomplete" }),
    genome({ code: "GSD", label: "UU_Cfam_GSD_1.0 (Canis lupus familiaris)", file: "GSD.fna.gz" }),
]);

test("the Reference Genomes table shows each catalog row with its model tag and without size", () => {
    const rows = items(catalog).items;

    assert.deepEqual(rows.map((row) => [row.id, row.title, row.status]), [
        ["hs37d5-GitHub-hs37d5.fa.gz", "hs37d5 (Dante)", "installed"],
        ["hs37d5-NIH-Alt-hs37d5.fa.gz", "hs37d5 (Dante)", "installed"],
        ["hg38-GitHub-hg38.fa.gz", "hg38 (YSEQ)", "unindexed"],
        ["THGySeqp-GitHub-hg38_CP086569.fasta.gz", "hg38+HG002 Y v2 (by YSEQ)", "incomplete"],
        ["GSD-GitHub-GSD.fna.gz", "UU_Cfam_GSD_1.0 (Canis lupus)", "missing"],
    ]);
    assert.deepEqual(rows[0].values, {
        name: "hs37d5 (Dante)",
        tag: "1K37g",
        source: "GitHub",
        code: "hs37d5",
        final: "hs37d5.fa.gz",
        ref: path.join(genomesDir, "hs37d5.fa.gz"),
        description: "hs37d5 (1K Genome; @US NIH)",
        sn: "86",
    });
    assert.deepEqual(rows[0].tags, [{ id: "recommended", title: "Recommended", style: "primary" }]);
    assert.deepEqual(rows[2].tags, []);
    assert.deepEqual(rows.map((row) => row.values.tag), ["1K37g", "1K37g", "hg38h", "T2T HG002", "Dog"]);
    assert.equal("size" in rows[0].values, false);
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

test("every Reference Genomes and Reference Data row has source, organization, and license in its tooltip", async () => {
    for (const row of items(catalog).items) {
        assert.match(row.tooltip, /Source: https?:\/\//, `${row.id} tooltip missing Source URL`);
        assert.match(row.tooltip, /Organization: .+/, `${row.id} tooltip missing Organization`);
        assert.match(row.tooltip, /License: .+/, `${row.id} tooltip missing License`);
    }
    const { items: refDataItems } = await import("../../examples/WGSExtract/transforms/reference-data.mjs");
    const refRows = refDataItems({ details: { referenceLibrary: { path: "/tmp/ref" } } }).items;
    assert.ok(refRows.length >= 19);
    for (const row of refRows) {
        assert.match(row.tooltip, /Source: https?:\/\//, `${row.id} tooltip missing Source URL`);
        assert.match(row.tooltip, /Organization: .+/, `${row.id} tooltip missing Organization`);
        assert.match(row.tooltip, /License: .+/, `${row.id} tooltip missing License`);
    }
});
