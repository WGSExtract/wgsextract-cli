export interface ViewerCytoband {
    chr: string;
    start: number;
    end: number;
    name: string;
    gieStain: string;
}

export interface ViewerCytobands {
    features: ViewerCytoband[];
    notices: { code: string; fileName: string; command: string }[];
}

/** Primary chromosome lengths from the pinned UCSC hg19/hg38 assemblies. */
export function cytobandChromosomeLengths(build: string): Map<string, number> {
    const lengths: Record<string, number[]> = {
        hg19: [
            249250621, 243199373, 198022430, 191154276, 180915260, 171115067,
            159138663, 146364022, 141213431, 135534747, 135006516, 133851895,
            115169878, 107349540, 102531392, 90354753, 81195210, 78077248,
            59128983, 63025520, 48129895, 51304566, 155270560, 59373566,
        ],
        hg38: [
            248956422, 242193529, 198295559, 190214555, 181538259, 170805979,
            159345973, 145138636, 138394717, 133797422, 135086622, 133275309,
            114364328, 107043718, 101991189, 90338345, 83257441, 80373285,
            58617616, 64444167, 46709983, 50818468, 156040895, 57227415,
        ],
    };
    return new Map((lengths[build] ?? []).map((length, i) => [`chr${i < 22 ? i + 1 : i === 22 ? "X" : "Y"}`, length]));
}

export interface IgvCytoband {
    start: number;
    end: number;
    name: string;
    type: string;
    stain: number;
}

/** Native igv band records, grouped in memory; no URL parser or whole-genome mode. */
export function viewerCytobandSource(features: ViewerCytoband[]) {
    const chromosomes = new Map<string, IgvCytoband[]>();
    for (const band of features) {
        const bands = chromosomes.get(band.chr) ?? [];
        bands.push({
            start: band.start, end: band.end, name: band.name,
            type: band.gieStain === "acen" ? "c" : band.gieStain.charAt(1),
            stain: band.gieStain.startsWith("gpos") ? Number(band.gieStain.slice(4)) : 0,
        });
        chromosomes.set(band.chr, bands);
    }
    return { getCytobands: (chr: string) => chromosomes.get(chr) ?? [] };
}
