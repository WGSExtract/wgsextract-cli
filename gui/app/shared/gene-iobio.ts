// Changing the licence or disclosure requires a new per-launch consent version.
export const geneIobioConsentVersion = 2;

export function geneIobioBuild(build: string): string | undefined {
    if (build === "hg19") return "GRCh37";
    if (build === "hg38") return "GRCh38";
    return undefined;
}
