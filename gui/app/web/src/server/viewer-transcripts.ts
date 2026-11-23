import { stat } from "node:fs/promises";
import path from "node:path";
import type { ViewerTranscripts } from "../../../shared/genome-viewer.js";
import { shellQuote } from "../../../shared/rendering.js";
import { guardedViewerPath, isMissingViewerPath } from "./viewer-files.js";
import { viewerContigAliases, viewerReferenceMetadata } from "./viewer-reference-metadata.js";
import { tabixContigs } from "./viewer-tabix-metadata.js";

/**
 * The RefSeq transcript and exon models `ref gene-map` installs beside the gene map, as an
 * indexed BED12 track igv reads in slices. Missing models are a notice; nothing is downloaded.
 */
export async function installedViewerTranscripts(fasta: string, referenceRoot: string): Promise<ViewerTranscripts<string>> {
    const command = `wgsextract ref gene-map --ref ${shellQuote(referenceRoot)}`;
    let fileName = "transcripts";
    const notice = (code: string) => ({ notices: [{ code, fileName, command }] });
    const metadata = await viewerReferenceMetadata(fasta);
    if (!metadata?.build) return notice("transcriptsBuildUnsupported");
    fileName = `transcripts_${metadata.build}.bed.gz`;
    let file: string | undefined;
    for (const folder of ["downloads", "ref"]) {
        const candidate = path.join(referenceRoot, folder, fileName);
        try {
            await guardedViewerPath(candidate, [referenceRoot]);
            await guardedViewerPath(`${candidate}.tbi`, [referenceRoot]);
            file = candidate;
            break;
        } catch (error) {
            if (!isMissingViewerPath(error)) return notice("transcriptsInvalid");
        }
    }
    if (!file) return notice("transcriptsMissing");
    const index = `${file}.tbi`;
    let contigs: string[];
    try {
        contigs = await tabixContigs(index, (await stat(file)).size);
    } catch {
        return notice("transcriptsInvalid");
    }
    const { reference } = metadata;
    if (!contigs.some((contig) => viewerContigAliases(contig).some((alias) => reference.has(alias)))) return notice("transcriptsMismatch");
    return { url: file, indexURL: index, notices: [] };
}
