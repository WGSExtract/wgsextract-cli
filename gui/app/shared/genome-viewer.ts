export type ViewerProblem = "referenceMissing" | "referenceIndexMissing" | "tracksMissing" | "indexMissing" | "unsupportedFile" | "ambiguousFile" | "invalidLocus" | "locusNotFound";

export class ViewerFileError extends Error {
    constructor(readonly code: ViewerProblem, readonly fileName = "") {
        super(code);
    }
}

export interface LocalTrack {
    name: string;
    type: "alignment" | "variant";
    format: "bam" | "cram" | "vcf";
    url: File;
    indexURL: File;
    indexed: true;
}

export interface LocalViewerFiles {
    reference: { id: string; name: string; fastaURL: File; indexURL: File; wholeGenomeView: false };
    tracks: LocalTrack[];
}

/** Only explicit File objects reach igv: no inferred URLs, remote lookups, or whole-file reads. */
export function localViewerFiles(referenceFiles: File[], trackFiles: File[]): LocalViewerFiles {
    const references = uniqueFiles(referenceFiles);
    const fasta = referenceFiles.filter((file) => /\.(fa|fasta|fna)$/i.test(file.name));
    const reference = fasta[0];
    if (!reference) {
        throw new ViewerFileError("referenceMissing");
    }
    if (fasta.length !== 1) {
        throw new ViewerFileError("ambiguousFile");
    }
    const fai = references.get(`${reference.name.toLowerCase()}.fai`);
    if (!fai) {
        throw new ViewerFileError("referenceIndexMissing", reference.name);
    }
    const files = uniqueFiles(trackFiles);
    const tracks: LocalTrack[] = [];
    for (const file of trackFiles) {
        const name = file.name.toLowerCase();
        if (/\.(bai|csi|crai|tbi)$/.test(name)) {
            continue;
        }
        const format = name.endsWith(".bam") ? "bam" : name.endsWith(".cram") ? "cram" : /\.g?vcf\.gz$/.test(name) ? "vcf" : null;
        if (!format) {
            throw new ViewerFileError("unsupportedFile", file.name);
        }
        const indexNames = format === "bam"
            ? [`${name}.bai`, name.replace(/\.bam$/, ".bai"), `${name}.csi`]
            : format === "cram"
                ? [`${name}.crai`, name.replace(/\.cram$/, ".crai")]
                : [`${name}.tbi`, `${name}.csi`];
        const indexURL = indexNames.map((candidate) => files.get(candidate)).find((candidate) => candidate !== undefined);
        if (!indexURL) {
            throw new ViewerFileError("indexMissing", file.name);
        }
        tracks.push({
            name: file.name.replace(/[&<>"']/g, (character) => `&#${character.charCodeAt(0)};`),
            format,
            type: format === "vcf" ? "variant" : "alignment",
            url: file,
            indexURL,
            indexed: true,
        });
    }
    if (tracks.length === 0) {
        throw new ViewerFileError("tracksMissing");
    }
    return {
        reference: { id: "local", name: reference.name, fastaURL: reference, indexURL: fai, wholeGenomeView: false },
        tracks,
    };
}

function uniqueFiles(files: File[]): Map<string, File> {
    const result = new Map<string, File>();
    for (const file of files) {
        const key = file.name.toLowerCase();
        if (result.has(key)) {
            throw new ViewerFileError("ambiguousFile", file.name);
        }
        result.set(key, file);
    }
    return result;
}

/** Coordinate-only navigation deliberately never falls back to igv's public gene/rsID search. */
export function localViewerLocus(value: string): string {
    const match = /^([A-Za-z0-9_.-]+):([\d,]+)-([\d,]+)$/.exec(value.trim());
    const start = Number(match?.[2]?.replaceAll(",", ""));
    const end = Number(match?.[3]?.replaceAll(",", ""));
    if (!match || !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 1 || end < start) {
        throw new ViewerFileError("invalidLocus");
    }
    return `${match[1]}:${start}-${end}`;
}
