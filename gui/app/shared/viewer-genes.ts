import { localViewerLocus, ViewerFileError } from "./genome-viewer.js";

export interface ViewerGene {
    name: string;
    chr: string;
    start: number;
    end: number;
}

export interface ViewerGenes {
    features: ViewerGene[];
    notices: { code: string; fileName: string }[];
}

export function isViewerGeneSymbol(value: string): boolean {
    return /^[A-Za-z0-9][A-Za-z0-9_./-]{0,63}$/.test(value);
}

/** One prebuilt exact-symbol lookup per opened reference; typing never reads a file. */
export function viewerGeneLookup(features: ViewerGene[]): Map<string, string> {
    return new Map(features.map((gene) => [gene.name.toUpperCase(), `${gene.chr}:${gene.start + 1}-${gene.end}`]));
}

export function viewerNavigationLocus(value: string, lookup: Map<string, string>): string {
    const query = value.trim();
    if (query.includes(":")) return localViewerLocus(query);
    if (!isViewerGeneSymbol(query) || /^rs\d+$/i.test(query)) {
        throw new ViewerFileError("invalidLocus");
    }
    const locus = lookup.get(query.toUpperCase());
    if (!locus) throw new ViewerFileError(lookup.size ? "geneNotFound" : "geneSearchUnavailable");
    return locus;
}
