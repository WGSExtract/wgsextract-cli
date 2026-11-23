import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { exonNumbers, filteredVariants, GeneDocumentError, geneDocument, geneExportName } from "../dist/shared/gene-page.js";
import { openGenePage } from "../dist/web/src/server/gene-page.js";
import { ResultPaths } from "../dist/web/src/server/result-paths.js";
import { registerUsersWorkspace } from "../dist/web/src/server/users-workspace.js";

/** What `wgsextract info gene --json` prints: a result document whose details are the gene page. */
function cliResult(details, outputs = []) {
    return { schema: "wgsextract.result/1", command: "info gene", status: "ok", exit_code: 0, outputs, metrics: {}, warnings: [], details };
}

const page = {
    schema: "wgsextract.gene/1",
    query: "brca1",
    build: "hg38",
    gene: { symbol: "BRCA1", chrom: "chr17", start: 43044295, end: 43170327, locus: "chr17:43044295-43170327", source: "NCBI RefSeq" },
    transcripts: { source: "Transcripts of UCSC refGene (hg38)", items: [{
        accession: "NM_007294", strand: "-", chrom: "chr17", start: 43044295, end: 43125483, cds_start: 43045678, cds_end: 43124115,
        exons: [[43044295, 43045802], [43047643, 43047703], [43124017, 43125483]],
    }] },
    variants: {
        status: "ok", file: "/genomes/s.vcf.gz", sample: "S1", region: "chr17:43044295-43170327", annotations: ["vep", "clinvar", "gnomad"],
        provenance: [{ annotation: "clinvar", header: ["bcftools_annotateCommand=annotate -a clinvar.vcf.gz"] }], rare_af: 0.01, acmg_disclaimer: null,
        rows: [
            { chrom: "chr17", pos: 43045712, ref: "G", alt: "A", impact: "MODERATE", gnomad_af: 1e-5, filters: ["rare_damaging"] },
            { chrom: "chr17", pos: 43047650, ref: "C", alt: "T", clinvar_significance: "Pathogenic", filters: ["rare_damaging", "clinvar_plp", "made_up"] },
            { chrom: "chr17", pos: 43124096, ref: "A", alt: "G", clinvar_significance: "Benign", filters: [] },
        ],
        total: 3, truncated: false,
    },
    coverage: { status: "missing" },
    filters: ["rare_damaging", "clinvar_plp", "acmg_plp"],
    notices: [{ code: "alignment_missing", message: "No BAM or CRAM given.", command: "" }],
};

test("#1166: the gene page reads the CLI document and filters by the CLI's own tags", () => {
    const doc = geneDocument(page);
    assert.equal(doc.gene.locus, "chr17:43044295-43170327");
    assert.equal(doc.coverage.status, "missing");
    assert.deepEqual(filteredVariants(doc.variants.rows, "clinvar_plp").map((row) => row.values.pos), [43047650]);
    assert.deepEqual(filteredVariants(doc.variants.rows, "rare_damaging").length, 2);
    assert.deepEqual(doc.variants.rows[1].filters, ["rare_damaging", "clinvar_plp"], "Unknown tags are dropped, not shown as filters.");
    assert.equal(geneExportName("rare_damaging"), "rare-damaging");
    // A minus-strand gene's first exon is its highest-coordinate one.
    assert.equal(exonNumbers(doc.transcripts.items[0]).get("43124017-43125483"), 1);
    assert.throws(() => geneDocument({ ...page, schema: "wgsextract.lookup/1" }), GeneDocumentError);
    assert.throws(() => geneDocument({ ...page, gene: { ...page.gene, start: "43044295" } }), GeneDocumentError);
});

async function context(t, cli) {
    const output = path.resolve("../out/tests");
    await mkdir(output, { recursive: true });
    const root = await mkdtemp(path.join(output, "gene-page-"));
    t.after(() => rm(root, { force: true, recursive: true }));
    const users = path.join(root, "users");
    await mkdir(path.join(root, "scripts"), { recursive: true });
    await mkdir(path.join(users, "mygenomes"), { recursive: true });
    await writeFile(path.join(root, "scripts", "run-wgsextract.sh"), "");
    registerUsersWorkspace(root, users);
    const config = path.join(root, "selected.toml");
    await writeFile(config, "");
    await writeFile(path.join(root, "state.json"), JSON.stringify({ configFilePaths: { wgs_settings: config } }));
    const vcf = path.join(users, "mygenomes", "s.vcf.gz");
    await writeFile(vcf, "variants");
    const calls = [];
    return {
        vcf, config, calls,
        context: {
            bundleRoot: root,
            manifest: { id: "wgs-extract", pages: [{ id: "settings", sections: [{ id: "settings", controls: [{
                id: "wgs_settings", kind: "configEditor", configFile: { path: "{{usersWorkspace}}/config.toml" },
            }] }] }] },
            // The CLI is a process boundary; this stands in for it.
            async runProcess(script, args, options) {
                calls.push({ args, config: options.env.WGSEXTRACT_GUI_CONFIG_PATH });
                return cli(args);
            },
        },
    };
}

test("#1166: the gene page runs the local CLI with the selected settings and lets exports be revealed", async (t) => {
    const filename = "s_BRCA1_clinvar-plp_variants.tsv";
    const f = await context(t, (args) => ({
        exitCode: 0, stderr: "",
        stdout: JSON.stringify(cliResult(page, args.includes("--export")
            ? [{ path: table, role: "variant_table", kind: "tsv", metrics: { rows: 1 } }] : [])),
    }));
    const table = path.join(path.dirname(f.vcf), "wgse", filename);
    const paths = new ResultPaths();
    const signal = new AbortController().signal;
    const shown = await openGenePage({ gene: "brca1", vcf: "{{usersWorkspace}}/mygenomes/s.vcf.gz", minDepth: 20 }, f.context, paths, signal);
    assert.equal(shown.document.gene.symbol, "BRCA1");
    assert.equal(f.calls[0].config, f.config, "The CLI reads the settings file the app shows.");
    // contract: info gene's flags and --json are the app's interface to the CLI.
    assert.deepEqual(f.calls[0].args, ["info", "gene", "--gene", "brca1", "--vcf-input", f.vcf, "--min-depth", "20", "--json"]);
    assert.equal(paths.has(table), false);

    const exported = await openGenePage({ gene: "BRCA1", vcf: f.vcf, export: "clinvar-plp" }, f.context, paths, signal);
    assert.equal(exported.exportPath, table);
    assert.equal(exported.exportRows, 1);
    assert.equal(paths.has(table), true, "Show in folder may reveal only the table the CLI reported.");
    assert.equal(paths.has(path.join(path.dirname(f.vcf), filename)), false, "A matching filename in another folder cannot be revealed.");
});

test("#1166: bad input and CLI failures are problems the page can explain", async (t) => {
    const f = await context(t, () => ({
        exitCode: 2, stderr: "",
        stdout: JSON.stringify({ ...cliResult(null), status: "failed", exit_code: 2, error: { kind: "usage", message: "Unknown genes:\n  BRCAX: not a gene" } }),
    }));
    const signal = new AbortController().signal;
    const paths = new ResultPaths();
    assert.deepEqual(await openGenePage({ gene: "BR CA1" }, f.context, paths, signal), { problem: "invalidGene" });
    assert.deepEqual(await openGenePage({ gene: "BRCA1", vcf: "s.vcf.gz" }, f.context, paths, signal), { problem: "pathNotAbsolute", fileName: "s.vcf.gz" });
    assert.deepEqual(await openGenePage({ gene: "BRCA1", alignment: f.vcf }, f.context, paths, signal), { problem: "unsupportedAlignment", fileName: "s.vcf.gz" });
    assert.deepEqual(await openGenePage({ gene: "BRCA1", vcf: `${f.vcf}.missing.vcf.gz` }, f.context, paths, signal), { problem: "fileMissing", fileName: "s.vcf.gz.missing.vcf.gz" });
    assert.deepEqual(await openGenePage({ gene: "BRCA1", export: "all" }, f.context, paths, signal), { problem: "exportNeedsVcf" });
    assert.equal(f.calls.length, 0, "Nothing runs until the inputs are valid.");
    const failed = await openGenePage({ gene: "BRCAX", vcf: f.vcf }, f.context, paths, signal);
    assert.equal(failed.problem, "geneUsage");
    assert.match(failed.message, /BRCAX: not a gene/);
});
