import { readdir, stat } from "node:fs/promises";
import path from "node:path";
import type { LocalTrack } from "../../../shared/genome-viewer.js";
import { errnoCode } from "./errors.js";
import { guardedViewerPath } from "./viewer-files.js";
import { viewerReferenceMetadata } from "./viewer-reference-metadata.js";
import { validateReferenceAnnotation } from "./viewer-track-metadata.js";

interface AnnotationSpec {
    name: string;
    type: "annotation" | "variant" | "wig";
    format: "rsid-tab" | "pharmgkb-tsv" | "vcf" | "bigwig";
    pattern: (tokens: string) => RegExp;
}

const buildTokens: Record<string, string> = {
    hg19: "hg19|grch37",
    hg38: "hg38|grch38",
    t2t: "t2t|chm13",
};

const annotationSpecs: AnnotationSpec[] = [
    { name: "dbSNP", type: "variant", format: "vcf", pattern: (t) => new RegExp(`^dbsnp.*(?:${t}).*\\.vcf\\.(?:gz|bgz)$`, "i") },
    { name: "rsIDs", type: "annotation", format: "rsid-tab", pattern: (t) => new RegExp(`^all_snps_(?:${t})_ref\\.tab\\.gz$`, "i") },
    { name: "ClinVar", type: "variant", format: "vcf", pattern: (t) => new RegExp(`^clinvar.*(?:${t}).*\\.vcf\\.(?:gz|bgz)$`, "i") },
    { name: "PharmGKB", type: "annotation", format: "pharmgkb-tsv", pattern: (t) => new RegExp(`^pharmgkb.*(?:${t}).*\\.tsv\\.gz$`, "i") },
    { name: "PharmGKB", type: "variant", format: "vcf", pattern: (t) => new RegExp(`^pharmgkb.*(?:${t}).*\\.vcf\\.(?:gz|bgz)$`, "i") },
    { name: "gnomAD", type: "variant", format: "vcf", pattern: (t) => new RegExp(`^gnomad.*(?:${t}).*\\.vcf\\.(?:bgz|gz)$`, "i") },
    { name: "PhyloP", type: "wig", format: "bigwig", pattern: (t) => new RegExp(`^phylop.*(?:${t}).*\\.(?:bw|bigwig)$`, "i") },
    { name: "GERP", type: "wig", format: "bigwig", pattern: (t) => new RegExp(`^gerp.*(?:${t}).*\\.(?:bw|bigwig)$`, "i") },
];

function annotationBuild(reference: Map<string, number>, knownBuild: string | undefined): string | undefined {
    if (knownBuild) return knownBuild;
    const chr1 = [...reference].filter(([name]) => name === "chr1" || name === "1");
    return chr1.length === 1 && chr1[0]?.[1] === 248387328 ? "t2t" : undefined;
}

async function listDirectoryNames(directory: string): Promise<string[]> {
    try {
        return (await readdir(directory)).sort();
    } catch (error) {
        if (["ENOENT", "ENOTDIR", "EACCES"].includes(errnoCode(error) ?? "")) return [];
        throw error;
    }
}

async function existingIndex(file: string): Promise<string | undefined> {
    for (const candidate of [`${file}.tbi`, `${file}.csi`]) {
        try {
            if ((await stat(candidate)).isFile()) return candidate;
        } catch (error) {
            if (!["ENOENT", "ENOTDIR"].includes(errnoCode(error) ?? "")) throw error;
        }
    }
    return undefined;
}

/** Auto-discovers installed, indexed, build-matched annotation tracks in the reference library. */
export async function installedViewerAnnotations(fasta: string, referenceRoot: string): Promise<LocalTrack<string>[]> {
    const metadata = await viewerReferenceMetadata(fasta);
    if (!metadata) return [];
    const build = annotationBuild(metadata.reference, metadata.build);
    const tokens = build ? buildTokens[build] : undefined;
    if (!build || !tokens) return [];

    const searchDirs = [
        path.join(referenceRoot, "downloads"),
        path.join(referenceRoot, "ref"),
        path.join(referenceRoot, "microarray"),
        referenceRoot,
        path.join(referenceRoot, "downloads", build),
        path.join(referenceRoot, "ref", build),
        path.join(referenceRoot, "microarray", build),
    ];
    const dirEntries = await Promise.all(searchDirs.map(async (dir) => ({ dir, names: await listDirectoryNames(dir) })));
    const tracks: LocalTrack<string>[] = [];
    const seenNames = new Set<string>();

    for (const spec of annotationSpecs) {
        if (seenNames.has(spec.name)) continue;
        const regex = spec.pattern(tokens);
        let found: LocalTrack<string> | undefined;
        for (const { dir, names } of dirEntries) {
            for (const entry of names) {
                if (!regex.test(entry)) continue;
                const file = path.join(dir, entry);
                try {
                    await guardedViewerPath(file, [referenceRoot]);
                    let index: string | undefined;
                    if (spec.format !== "bigwig") {
                        index = await existingIndex(file);
                        if (!index) continue;
                        await guardedViewerPath(index, [referenceRoot]);
                    }
                    if (!await validateReferenceAnnotation(file, spec.format, index, metadata.reference)) continue;
                    found = {
                        name: spec.name,
                        type: spec.type,
                        format: spec.format,
                        url: file,
                        ...(index ? { indexURL: index } : {}),
                        indexed: true,
                    };
                    break;
                } catch {
                    continue;
                }
            }
            if (found) break;
        }
        if (found) {
            seenNames.add(spec.name);
            tracks.push(found);
        }
    }
    return tracks;
}
