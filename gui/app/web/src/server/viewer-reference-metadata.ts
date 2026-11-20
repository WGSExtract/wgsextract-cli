import { readFile } from "node:fs/promises";

export async function viewerReferenceMetadata(fasta: string) {
    const reference = new Map<string, number>();
    for (const line of (await readFile(`${fasta}.fai`, "utf8")).trim().split(/\r?\n/)) {
        const [name = "", rawLength = ""] = line.split("\t");
        const length = Number(rawLength);
        if (!name || !/^\d+$/.test(rawLength) || !Number.isSafeInteger(length) || length < 1 || reference.has(name)) return undefined;
        reference.set(name, length);
    }
    // Sequence lengths, never filenames or directories, identify the assembly.
    const builds: Record<number, string> = { 249250621: "hg19", 248956422: "hg38" };
    const chr1 = [...reference].filter(([name]) => name === "chr1" || name === "1");
    const build = chr1.length === 1 ? builds[chr1[0]?.[1] ?? 0] : undefined;
    return { reference, build };
}

export function viewerContigAliases(contig: string): string[] {
    const primary = contig.replace(/^chr/, "");
    return primary === "M" || primary === "MT"
        ? ["chrM", "M", "MT", "chrMT"]
        : /^(?:[1-9]|1\d|2[0-2]|X|Y)$/.test(primary) ? [primary, `chr${primary}`] : [contig];
}
