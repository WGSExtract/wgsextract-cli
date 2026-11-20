import { open, readFile } from "node:fs/promises";
import path from "node:path";
import { isViewerGeneSymbol, type ViewerGene, type ViewerGenes } from "../../../shared/viewer-genes.js";
import { errnoCode } from "./errors.js";
import { guardedViewerPath } from "./viewer-files.js";

const maxTableBytes = 8 * 1024 * 1024;
const maxGenes = 100_000;

/** Installed gene-map tables are read once, bounded, never downloaded or searched on each keystroke. */
export async function installedViewerGenes(fasta: string, referenceRoot: string): Promise<ViewerGenes> {
    let fileName = "gene-map";
    const notice = (code: string): ViewerGenes => ({ features: [], notices: [{ code, fileName }] });
    const reference = new Map<string, number>();
    for (const line of (await readFile(`${fasta}.fai`, "utf8")).trim().split(/\r?\n/)) {
        const [name = "", rawLength = ""] = line.split("\t");
        const length = Number(rawLength);
        if (!name || !/^\d+$/.test(rawLength) || !Number.isSafeInteger(length) || length < 1 || reference.has(name)) {
            return notice("geneReferenceInvalid");
        }
        reference.set(name, length);
    }
    // The sequence dictionary, not a FASTA filename or user's folder, identifies the assembly.
    const builds: Record<number, string> = { 249250621: "hg19", 248956422: "hg38" };
    const chr1 = [...reference].filter(([name]) => name === "chr1" || name === "1");
    const build = chr1.length === 1 ? builds[chr1[0]?.[1] ?? 0] : undefined;
    if (!build) return notice("geneBuildUnsupported");
    fileName = `genes_${build}.tsv`;
    let text: string | undefined;
    for (const directory of ["ref", "microarray"]) {
        const file = path.join(referenceRoot, directory, fileName);
        try {
            await guardedViewerPath(file, [referenceRoot]);
            const handle = await open(file, "r");
            try {
                if ((await handle.stat()).size > maxTableBytes) return notice("geneMapInvalid");
                const buffer = Buffer.alloc(maxTableBytes + 1);
                let offset = 0;
                while (offset < buffer.length) {
                    const { bytesRead } = await handle.read(buffer, offset, buffer.length - offset, null);
                    if (!bytesRead) break;
                    offset += bytesRead;
                }
                if (offset > maxTableBytes) return notice("geneMapInvalid");
                text = new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, offset));
            } finally {
                await handle.close();
            }
            break;
        } catch (error) {
            if (["ENOENT", "ENOTDIR"].includes(errnoCode(error) ?? "")) continue;
            return notice("geneMapInvalid");
        }
    }
    if (text === undefined) return notice("geneMapMissing");
    if (!text.endsWith("\n")) return notice("geneMapInvalid");
    const lines = text.replaceAll("\r\n", "\n").trimEnd().split("\n");
    if (lines.shift() !== "symbol\tchrom\tstart\tend" || lines.length > maxGenes) return notice("geneMapInvalid");
    const features: ViewerGene[] = [];
    const symbols = new Set<string>();
    let unmatched = false;
    for (const line of lines) {
        const columns = line.split("\t");
        const [symbol = "", contig = "", rawStart = "", rawEnd = ""] = columns;
        const name = symbol.toUpperCase();
        const start = Number(rawStart);
        const end = Number(rawEnd);
        if (columns.length !== 4 || !isViewerGeneSymbol(symbol)
            || !/^[A-Za-z0-9_.-]+$/.test(contig) || symbols.has(name)
            || !/^\d+$/.test(rawStart) || !/^\d+$/.test(rawEnd)
            || !Number.isSafeInteger(start) || !Number.isSafeInteger(end)
            || start < 1 || end < start || end - start > 3_000_000) return notice("geneMapInvalid");
        symbols.add(name);
        const primary = contig.replace(/^chr/, "");
        const aliases = primary === "M" || primary === "MT"
            ? ["chrM", "M", "MT", "chrMT"]
            : /^(?:[1-9]|1\d|2[0-2]|X|Y)$/.test(primary) ? [primary, `chr${primary}`] : [contig];
        const matches = aliases.filter((candidate) => reference.has(candidate));
        if (matches.length !== 1) {
            unmatched = true;
            continue;
        }
        const chr = matches[0];
        if (!chr || end > (reference.get(chr) ?? 0)) return notice("geneMapMismatch");
        features.push({ name, chr, start: start - 1, end });
    }
    if (!features.length) return notice("geneMapMismatch");
    return { features, notices: unmatched ? [{ code: "geneContigsSkipped", fileName }] : [] };
}
