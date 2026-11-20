import { spawnSync } from "node:child_process";
import { copyFile, mkdir, open, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

/** Tiny synthetic reference from the CLI's VEP tests; all derived files stay in the verification output directory. */
export async function prepareViewerFixtures(root, output, tools) {
    const tool = (name) => path.join(tools, `${name}${process.platform === "win32" ? ".exe" : ""}`);
    const run = (name, args) => {
        const result = spawnSync(tool(name), args, { encoding: "utf8", windowsHide: true });
        if (result.error || result.status !== 0) {
            throw new Error(`${name}: ${result.error?.message ?? result.stderr}`);
        }
    };
    for (const name of ["samtools", "bcftools"]) run(name, ["--version"]);
    const library = path.join(output, "mygenomes");
    const reference = path.join(output, "reference", "genomes");
    await mkdir(library, { recursive: true });
    await mkdir(reference, { recursive: true });
    const fasta = path.join(reference, "reference.fa");
    await copyFile(path.join(root, "tests/fixtures/vep_rs/genome.fa"), fasta);
    run("samtools", ["faidx", fasta]);
    run("samtools", ["dict", "-o", path.join(reference, "reference.dict"), fasta]);
    const source = (await readFile(fasta, "utf8")).replaceAll("\r\n", "\n");
    const record = source.split(/^>/m).find((entry) => entry.startsWith("1\n"));
    if (!record) throw new Error("Synthetic viewer fixture requires contig 1.");
    const sequence = record.split("\n").slice(1).join("").trim();
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
##contig=<ID=1,length=${sequence.length}>
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
    return { library, reference, bam, cram, vcf: `${vcf}.gz`, large };
}
