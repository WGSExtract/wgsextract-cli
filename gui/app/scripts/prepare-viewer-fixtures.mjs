import { spawnSync } from "node:child_process";
import { appendFile, copyFile, mkdir, open, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

/** Tiny synthetic reference from the CLI's VEP tests; all derived files stay in the verification output directory. */
export async function prepareViewerFixtures(root, output, tools, geneMaps = false, memory = false) {
    const tool = (name) => path.join(tools, `${name}${process.platform === "win32" ? ".exe" : ""}`);
    const run = (name, args) => {
        const result = spawnSync(tool(name), args, { encoding: "utf8", windowsHide: true });
        if (result.error || result.status !== 0) {
            throw new Error(`${name}: ${result.error?.message ?? result.stderr}`);
        }
    };
    for (const name of ["samtools", "bcftools", "bgzip", "tabix"]) run(name, ["--version"]);
    const library = path.join(output, "mygenomes");
    const reference = path.join(output, "reference", "genomes");
    await mkdir(library, { recursive: true });
    await mkdir(reference, { recursive: true });
    const fasta = path.join(reference, "reference.fa");
    await copyFile(path.join(root, "tests/fixtures/vep_rs/genome.fa"), fasta);
    const source = (await readFile(fasta, "utf8")).replaceAll("\r\n", "\n");
    const record = source.split(/^>/m).find((entry) => entry.startsWith("1\n"));
    if (!record) throw new Error("Synthetic viewer fixture requires contig 1.");
    const sequence = record.split("\n").slice(1).join("").trim();
    const referenceLength = geneMaps ? 248956422 : memory ? 25000 : sequence.length;
    if (memory && !geneMaps) {
        const padded = sequence + "N".repeat(referenceLength - sequence.length);
        await writeFile(fasta, `>1\n${padded.match(/.{1,60}/g).join("\n")}\n`);
    }
    if (geneMaps) {
        let remaining = referenceLength - sequence.length;
        const chunk = `${"N".repeat(60)}\n`.repeat(10_000);
        while (remaining >= 600_000) {
            await appendFile(fasta, chunk);
            remaining -= 600_000;
        }
        await appendFile(fasta, `${"N".repeat(60)}\n`.repeat(Math.floor(remaining / 60))
            + (remaining % 60 ? `${"N".repeat(remaining % 60)}\n` : ""));
        const maps = path.join(output, "reference", "downloads");
        await mkdir(maps, { recursive: true });
        await writeFile(path.join(maps, "genes_hg38.tsv"), "symbol\tchrom\tstart\tend\nREFGENE_DEMO\tchr1\t200\t400\n");
    }
    run("samtools", ["faidx", fasta]);
    run("samtools", ["dict", "-o", path.join(reference, "reference.dict"), fasta]);
    const ref = sequence[249];
    const alt = ref === "A" ? "C" : "A";
    const sam = path.join(library, "reads.sam");
    const header = await readFile(path.join(reference, "reference.dict"), "utf8");
    const reads = Array.from({ length: 40 }, (_, i) => {
        const start = 201 + i;
        const bases = sequence.slice(start - 1, start + 149).split("");
        if (i % 2) bases[250 - start] = alt;
        return `synthetic_${i}\t${i % 2 ? 16 : 0}\t1\t${start}\t60\t150M\t*\t0\t0\t${bases.join("")}\t${"I".repeat(150)}\tRG:Z:demo`;
    });
    await writeFile(sam, `${header}@RG\tID:demo\tSM:synthetic\n${reads.join("\n")}\n`);
    const bam = path.join(library, "reads.bam");
    run("samtools", ["view", "-b", "-o", bam, sam]);
    run("samtools", ["index", bam]);
    const cram = path.join(library, "reads.cram");
    run("samtools", ["view", "-C", "-T", fasta, "-o", cram, bam]);
    run("samtools", ["index", cram]);
    const vcf = path.join(library, "variants.vcf");
    await writeFile(vcf, `##fileformat=VCFv4.2
##contig=<ID=1,length=${referenceLength}>
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsynthetic
1\t250\tdemo\t${ref}\t${alt}\t60\tPASS\t.\tGT\t0/1
`);
    run("bcftools", ["view", "-Oz", "-o", `${vcf}.gz`, vcf]);
    run("bcftools", ["index", "-t", `${vcf}.gz`]);
    const large = path.join(library, "large.bam");
    await copyFile(bam, large);
    await copyFile(`${bam}.bai`, `${large}.bai`);
    const handle = await open(large, "r+");
    try {
        await handle.truncate(4 * 1024 ** 3);
    } finally {
        await handle.close();
    }
    const tracks = [];
    for (const source of ["panels.bed", "coverage.bedgraph"]) {
        const file = path.join(library, source);
        await copyFile(path.join(root, "tests/fixtures/viewer_tracks", source), file);
        tracks.push(file);
        for (const csi of [false, true]) {
            const compressed = path.join(library, `${csi ? "csi-" : ""}${source}.gz`);
            if (compressed.slice(0, -3) !== file) await copyFile(file, compressed.slice(0, -3));
            run("bgzip", ["-k", "-f", compressed.slice(0, -3)]);
            run("tabix", [...(csi ? ["-C"] : []), "-p", "bed", compressed]);
            tracks.push(compressed);
        }
    }
    const bigwig = path.join(library, "coverage.bw");
    await copyFile(path.join(root, "tests/fixtures/viewer_tracks/coverage.bw"), bigwig);
    tracks.push(bigwig);
    for (const name of ["malformed.bedgraph.gz", "malformed.bedgraph.gz.tbi", "csi-malformed.bedgraph.gz", "csi-malformed.bedgraph.gz.csi"]) {
        await copyFile(path.join(root, "tests/fixtures/viewer_tracks", name), path.join(library, name));
    }
    return { library, reference, bam, cram, vcf: `${vcf}.gz`, large, tracks };
}
