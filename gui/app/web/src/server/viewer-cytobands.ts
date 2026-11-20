import { open } from "node:fs/promises";
import path from "node:path";
import { cytobandChromosomeLengths, type ViewerCytoband, type ViewerCytobands } from "../../../shared/viewer-cytobands.js";
import { errnoCode } from "./errors.js";
import { guardedViewerPath } from "./viewer-files.js";
import { viewerContigAliases, viewerReferenceMetadata } from "./viewer-reference-metadata.js";

const maxTableBytes = 1024 * 1024;
const stains = new Set(["gneg", "gpos25", "gpos50", "gpos75", "gpos100", "acen", "gvar", "stalk"]);

export async function installedViewerCytobands(fasta: string, referenceRoot: string): Promise<ViewerCytobands> {
    let fileName = "cytobands";
    let command = "wgsextract ref cytobands --build hg38 --download";
    const notice = (code: string): ViewerCytobands => ({ features: [], notices: [{ code, fileName, command }] });
    const metadata = await viewerReferenceMetadata(fasta);
    if (!metadata) return notice("cytobandReferenceInvalid");
    const { reference, build } = metadata;
    if (!build) return notice("cytobandBuildUnsupported");
    fileName = `cytobands_${build}.tsv`;
    command = `wgsextract ref cytobands --build ${build} --download`;
    const file = path.join(referenceRoot, "ref", fileName);
    let text: string;
    try {
        await guardedViewerPath(file, [referenceRoot]);
        const handle = await open(file, "r");
        try {
            if (!(await handle.stat()).isFile()) return notice("cytobandInvalid");
            const buffer = Buffer.alloc(maxTableBytes + 1);
            let offset = 0;
            while (offset < buffer.length) {
                const { bytesRead } = await handle.read(buffer, offset, buffer.length - offset, null);
                if (!bytesRead) break;
                offset += bytesRead;
            }
            if (offset > maxTableBytes) return notice("cytobandInvalid");
            text = new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, offset));
        } finally {
            await handle.close();
        }
    } catch (error) {
        return notice(["ENOENT", "ENOTDIR"].includes(errnoCode(error) ?? "") ? "cytobandMissing" : "cytobandInvalid");
    }
    if (!text.endsWith("\n")) return notice("cytobandInvalid");
    const bands: ViewerCytoband[] = [];
    const ends = new Map<string, number>();
    const names = new Set<string>();
    for (const line of text.replaceAll("\r\n", "\n").slice(0, -1).split("\n")) {
        const columns = line.split("\t");
        const [chr = "", rawStart = "", rawEnd = "", name = "", gieStain = ""] = columns;
        const start = Number(rawStart);
        const end = Number(rawEnd);
        if (columns.length !== 5 || !/^chr(?:[1-9]|1\d|2[0-2]|X|Y)$/.test(chr)
            || !/^[pq]\d{1,2}(?:\.\d{1,3})?$/.test(name) || !stains.has(gieStain)
            || !/^\d{1,10}$/.test(rawStart) || !/^\d{1,10}$/.test(rawEnd)
            || start !== (ends.get(chr) ?? 0) || end <= start || end > 1_000_000_000
            || names.has(`${chr}:${name}`) || bands.length >= 10_000) return notice("cytobandInvalid");
        names.add(`${chr}:${name}`);
        ends.set(chr, end);
        bands.push({ chr, start, end, name, gieStain });
    }
    if (ends.size !== 24 || [...cytobandChromosomeLengths(build)].some(([chr, length]) => ends.get(chr) !== length)) return notice("cytobandInvalid");
    const features: ViewerCytoband[] = [];
    let skipped = false;
    for (const band of bands) {
        const matches = viewerContigAliases(band.chr).filter((chr) => reference.has(chr));
        const chr = matches.length === 1 ? matches[0] : undefined;
        if (!chr) {
            skipped = true;
            continue;
        }
        // A complete chromosome, not a cropped/mismatched band table, must fit.
        if (ends.get(band.chr) !== reference.get(chr)) return notice("cytobandMismatch");
        features.push({ ...band, chr });
    }
    if (!features.length) return notice("cytobandMismatch");
    return { features, notices: skipped ? [{ code: "cytobandContigsSkipped", fileName, command }] : [] };
}
