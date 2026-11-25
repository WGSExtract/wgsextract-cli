// Offline real-browser proof of the gene page (#1166): synthetic hg38-sized fixtures, the
// local CLI, Chromium with every non-local request aborted. Evidence goes under --out.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cp, mkdir, readdir, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import { parseArgs } from "node:util";
import { chromium } from "playwright";
import { loadLocalizedBundle } from "../dist/web/src/server/bundle-loader.js";
import { createProcessManager } from "../dist/web/src/server/process-runner.js";
import { createRequestHandler } from "../dist/web/src/server/routes.js";
import { registerUsersWorkspace } from "../dist/web/src/server/users-workspace.js";

const root = path.resolve(import.meta.dirname, "../../..");
const { values } = parseArgs({ options: {
    out: { type: "string" }, "cli-root": { type: "string", default: root }, chromium: { type: "string" },
} });
if (!values.out) throw new Error("Usage: node scripts/verify-gene-page.mjs --out REPO/out/gene-page [--cli-root PATH] [--chromium PATH]");
const output = path.resolve(values.out);
assert.ok(output.startsWith(`${path.join(root, "out")}${path.sep}`), "Evidence and fixtures must stay under this checkout's out/.");
await mkdir(output, { recursive: true });
const cli = path.resolve(values["cli-root"]);
const bin = path.join(cli, ".pixi/envs/default", process.platform === "win32" ? "" : "bin");
const python = path.join(bin, process.platform === "win32" ? "python.exe" : "python");

// BRCA1 on hg38-length chr1 and chr17 (N bases), its RefSeq model, reads over two of its
// exons and an annotated VCF, all written by the CLI's own code.
const fixture = spawnSync(python, ["-c", `
import gzip, subprocess, sys
from pathlib import Path
from wgsextract_cli.core.gene_models import Transcript, write_transcripts
out = Path(sys.argv[1])
ref = out / "reference"; genomes = ref / "genomes"; downloads = ref / "downloads"; mine = out / "mygenomes"
for d in (genomes, downloads, mine): d.mkdir(parents=True, exist_ok=True)
lengths = {"chr1": 248956422, "chr17": 83257441}
fasta = genomes / "synthetic_hg38.fa.gz"
if not Path(str(fasta) + ".gzi").exists():
    with open(fasta, "wb") as f:
        proc = subprocess.Popen(["bgzip", "-@", "4", "-c"], stdin=subprocess.PIPE, stdout=f)
        line = b"N" * 60 + b"\\n"
        for name, length in lengths.items():
            proc.stdin.write(f">{name}\\n".encode())
            full, rest = divmod(length, 60)
            block = line * 100000
            for _ in range(full // 100000): proc.stdin.write(block)
            proc.stdin.write(line * (full % 100000))
            if rest: proc.stdin.write(b"N" * rest + b"\\n")
        proc.stdin.close(); assert proc.wait() == 0
    subprocess.run(["samtools", "faidx", str(fasta)], check=True)
exons = ((43044294, 43045802), (43047642, 43047703), (43124016, 43125483))
write_transcripts([Transcript("BRCA1", "NM_007294", "chr17", "-", 43044294, 43125483, (43045677, 43124115), exons)],
    downloads / "transcripts_hg38.bed.gz", ["Transcripts of a synthetic refGene (hg38) for the gene page proof (#1166)."])
(downloads / "genes_hg38.tsv").write_text("symbol\\tchrom\\tstart\\tend\\nBRCA1\\tchr17\\t43044295\\t43170327\\n")
sam = ["@HD\\tVN:1.6\\tSO:unsorted"] + [f"@SQ\\tSN:{n}\\tLN:{l}" for n, l in lengths.items()]
for (start, end), depth in zip(exons[:2], (30, 5)):
    for first in range(start, end, 50):
        sam += [f"r{first}_{i}\\t0\\tchr17\\t{first + 1}\\t60\\t50M\\t*\\t0\\t0\\t{'A' * 50}\\t{'I' * 50}" for i in range(depth)]
(mine / "sample.sam").write_text("\\n".join(sam) + "\\n")
subprocess.run(["samtools", "sort", "-o", str(mine / "sample.bam"), str(mine / "sample.sam")], check=True)
subprocess.run(["samtools", "index", str(mine / "sample.bam")], check=True)
(mine / "sample.sam").unlink()
csq = "Allele|Consequence|IMPACT|SYMBOL|Feature|HGVSc|HGVSp"
head = ["##fileformat=VCFv4.2"] + [f"##contig=<ID={n},length={l}>" for n, l in lengths.items()] + [
    f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {csq}">',
    '##INFO=<ID=CLNSIG,Number=.,Type=String,Description="ClinVar significance">',
    '##INFO=<ID=GNOMAD_AF,Number=A,Type=Float,Description="gnomAD Allele Frequency">',
    '##INFO=<ID=ACMG_CLASS,Number=A,Type=String,Description="fastVEP ACMG class">',
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
    "#CHROM\\tPOS\\tID\\tREF\\tALT\\tQUAL\\tFILTER\\tINFO\\tFORMAT\\tS1"]
rows = [("43045712", "G", "A", "CSQ=A|missense_variant|MODERATE|BRCA1|NM_007294|c.1A>G|p.M1V;GNOMAD_AF=0.00001;ACMG_CLASS=VUS", "0/1"),
        ("43047650", "C", "T", "CSQ=T|stop_gained|HIGH|BRCA1|NM_007294|c.2C>T|p.Q2*;CLNSIG=Pathogenic;ACMG_CLASS=P", "0/1"),
        ("43124096", "A", "G", "CSQ=G|synonymous_variant|LOW|BRCA1|NM_007294|c.3A>G|p.=;CLNSIG=Benign;GNOMAD_AF=0.35;ACMG_CLASS=B", "1/1")]
plain = mine / "sample.vcf"
plain.write_text("\\n".join(head + [f"chr17\\t{p}\\t.\\t{r}\\t{a}\\t50\\tPASS\\t{i}\\tGT\\t{g}" for p, r, a, i, g in rows]) + "\\n")
subprocess.run(["bgzip", "-f", str(plain)], check=True)
subprocess.run(["tabix", "-f", "-p", "vcf", str(plain) + ".gz"], check=True)
`, output], { encoding: "utf8", env: { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH}` }, windowsHide: true });
assert.equal(fixture.status, 0, fixture.stderr);

const library = path.join(output, "mygenomes");
const bam = path.join(library, "sample.bam");
const vcf = path.join(library, "sample.vcf.gz");
const bundleRoot = path.join(output, "bundle");
await cp(path.join(root, "gui/examples/WGSExtract"), bundleRoot, { recursive: true });
const users = path.join(output, "users");
await mkdir(users, { recursive: true });
registerUsersWorkspace(bundleRoot, users);
const config = path.join(output, "config.toml");
await writeFile(config, [
    `mygenomes_library = ${JSON.stringify(library)}`,
    `reference_library = ${JSON.stringify(path.join(output, "reference"))}`,
    `reference_genome_library = ${JSON.stringify(path.join(output, "reference", "genomes"))}`,
    `state_directory = ${JSON.stringify(path.join(output, "state"))}`, "",
].join("\n"));
await writeFile(path.join(bundleRoot, "state.json"), JSON.stringify({
    configFilePaths: { wgs_settings: config }, fieldValues: { bam_path: bam, vcf_path: vcf },
    uiMode: "advanced", setupExpanded: false, setupRun: { status: "succeeded", results: [] },
}));
const manager = createProcessManager({ maxOutputBytes: 1048576, maxErrorBytes: 65536 });
const load = () => loadLocalizedBundle("en", path.join(root, "gui"), bundleRoot, bundleRoot);
const loaded = await load();
const handler = createRequestHandler({
    ...loaded, apiToken: "offline-verification-only", bundleRoot, repoRoot: path.join(root, "gui"),
    distRoot: path.join(root, "gui/app/dist"), webRoot: path.join(root, "gui/app/web"),
    maxBodyBytes: 1048576, dynamicActions: new Map(),
    runProcess: (script, args, options) => manager.runProcess(script, args, { ...options, env: { ...options.env, WGSEXTRACT_APP_DIR: cli } }),
    localizedBundleLoader: { load }, enableDevReload: false, addDevReloadClient() {},
});
const server = createServer(handler);
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const url = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: values.chromium, headless: true });
const external = [];
const errors = [];
try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, serviceWorkers: "block" });
    await context.route("**/*", (route) => {
        if (new URL(route.request().url()).origin !== url) {
            external.push(route.request().url());
            return route.abort();
        }
        return route.continue();
    });
    await context.addInitScript(() => {
        window.policyViolations = [];
        document.addEventListener("securitypolicyviolation", (event) => window.policyViolations.push(`${event.violatedDirective} ${event.blockedURI}`));
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    await page.goto(url);
    await page.locator("[data-gene-page-open]").click();
    await page.locator("#gene-gene").fill("brca1");
    assert.equal(await page.locator("#gene-alignment").inputValue(), bam, "The page starts from the genome chosen in the app.");
    await page.locator("#gene-vcf").fill(vcf);
    await page.locator("#gene-minDepth").fill("20");
    await page.locator("[data-gene-form] button[type=submit]").click();
    await page.locator(".gene-summary h2").filter({ hasText: "BRCA1 on hg38" }).waitFor({ timeout: 120_000 });
    const proof = { exonRows: await page.locator(".gene-coverage tbody tr").count(), belowRows: await page.locator(".gene-coverage tbody tr.below").count() };
    proof.variants = await page.locator(".gene-variants tbody tr").count();
    await page.locator("[data-gene-filter]").selectOption("clinvar_plp");
    proof.clinvarRows = await page.locator(".gene-variants tbody tr").count();
    proof.notices = await page.locator(".gene-notices li").allInnerTexts();
    await page.screenshot({ path: path.join(output, "gene-page.png"), fullPage: true });
    await page.locator("[data-gene-export]").click();
    await page.locator("[data-gene-export-status]").filter({ hasText: "Saved 1 rows" }).waitFor({ timeout: 120_000 });
    proof.export = (await readdir(path.join(library, "WGSEv6"))).filter((name) => name.endsWith("_variants.tsv"));
    assert.deepEqual(proof.export, ["sample_BRCA1_clinvar-plp_variants.tsv"]);
    assert.ok((await stat(path.join(library, "WGSEv6", proof.export[0]))).size > 0);
    assert.deepEqual(proof, { ...proof, exonRows: 3, belowRows: 2, variants: 3, clinvarRows: 1, notices: [] });

    await page.locator("[data-gene-viewer]").click();
    const viewer = page.frameLocator(".genome-viewer-shell iframe");
    for (const label of ["RefSeq gene spans", "RefSeq transcripts", "sample.bam"]) {
        await viewer.locator(".igv-track-label").filter({ hasText: label }).waitFor({ timeout: 120_000 });
    }
    proof.viewerLocus = await viewer.locator('[data-locus] [name="locus"]').inputValue();
    assert.equal(proof.viewerLocus, "chr17:43044295-43170327");
    await viewer.locator("[data-igv]").screenshot({ path: path.join(output, "gene-viewer.png") });
    await page.locator(".genome-viewer-shell > .genome-viewer-close").filter({ hasText: "Back to the gene page" }).click();
    await page.locator(".gene-summary").waitFor();
    const violations = [...await page.evaluate(() => window.policyViolations)];
    for (const frame of page.frames()) violations.push(...await frame.evaluate(() => window.policyViolations ?? []).catch(() => []));
    assert.deepEqual(external, [], "No request may leave this computer.");
    assert.deepEqual(violations, [], "No content-security-policy violations.");
    assert.deepEqual(errors, [], "No page or console errors.");
    await writeFile(path.join(output, "gene-page-proof.json"), `${JSON.stringify({ ...proof, external, violations, errors }, null, 2)}\n`);
    console.log(JSON.stringify(proof));
} catch (error) {
    console.error(JSON.stringify({ errors, external }, null, 2));
    throw error;
} finally {
    await browser.close();
    await manager.terminateAllProcesses();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
}
