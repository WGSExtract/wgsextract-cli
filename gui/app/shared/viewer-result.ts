import { localViewerLocus } from "./genome-viewer.js";
import type { ResultDocument, ResultFile } from "./result-document.js";

/** Result links carry explicit coordinates, never a gene/rsID that could trigger an online lookup. */
export function viewerResultLocus(document: ResultDocument, output: ResultFile): string {
    const candidate = output.metrics.locus ?? document.metrics.locus;
    if (typeof candidate !== "string") {
        return "";
    }
    try {
        return localViewerLocus(candidate);
    } catch {
        return "";
    }
}

export function isViewerTrack(filePath: string): boolean {
    return /\.(bam|cram|(g\.?vcf|vcf)\.(gz|bgz))$/i.test(filePath);
}
