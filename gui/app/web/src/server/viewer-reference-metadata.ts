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

const rcrsLength = 16569;

/**
 * Maps a requested locus to the matched reference's contig naming (chr1 or 1, chrM or MT), so a
 * result row opens on any build's spelling. `rCRS:` positions (Haplogrep's mtDNA notation) map to
 * the mitochondrion; the caller first confirms by sequence that it is the rCRS (CHM13v2.0's chrM
 * is as long but starts elsewhere), and a length other than 16,569 bp is refused here too.
 */
export function referenceLocus(locus: string, reference: Map<string, number>): { locus: string } | { problem: "locusNotFound" | "mitoNotRcrs" } {
    const match = /^(.+):(\d+)-(\d+)$/.exec(locus);
    if (!match) {
        return { problem: "locusNotFound" };
    }
    const [, requested = "", rawStart = "", rawEnd = ""] = match;
    const rcrs = requested.toLowerCase() === "rcrs";
    const names = reference.has(requested) && !rcrs
        ? [requested]
        : [...new Set(viewerContigAliases(rcrs ? "chrM" : requested))].filter((name) => reference.has(name));
    if (names.length !== 1 || !names[0]) {
        return { problem: "locusNotFound" };
    }
    const contig = names[0];
    const length = reference.get(contig) ?? 0;
    if (rcrs && length !== rcrsLength) {
        return { problem: "mitoNotRcrs" };
    }
    const start = Number(rawStart);
    if (start > length) {
        return { problem: "locusNotFound" };
    }
    return { locus: `${contig}:${start}-${Math.min(Number(rawEnd), length)}` };
}
