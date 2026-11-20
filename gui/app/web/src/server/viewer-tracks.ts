import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { type LocalTrack, ViewerFileError } from "../../../shared/genome-viewer.js";
import { errnoCode } from "./errors.js";
import { expandWorkspaceToken } from "./paths.js";
import { guardedViewerPath } from "./viewer-files.js";
import { validateViewerTrack } from "./viewer-track-metadata.js";

export async function supplementalViewerTracks(paths: string[], fasta: string, roots: string[], bundleRoot: string): Promise<LocalTrack<string>[]> {
    if (!paths.length) return [];
    const reference = new Map((await readFile(`${fasta}.fai`, "utf8")).trim().split(/\r?\n/).map((line): [string, number] => {
        const columns = line.split("\t");
        return [columns[0] ?? "", Number(columns[1])];
    }));
    const tracks: LocalTrack<string>[] = [];
    for (const requested of paths) {
        const file = expandWorkspaceToken(requested, bundleRoot);
        const name = path.basename(file);
        try {
            await guardedViewerPath(file, roots);
        } catch (error) {
            if (["ENOENT", "ENOTDIR"].includes(errnoCode(error) ?? "")) throw new ViewerFileError("trackMissing", name);
            throw error;
        }
        const lower = name.toLowerCase();
        const format = /\.(bw|bigwig)$/.test(lower) ? "bigwig" : /\.bed(\.gz)?$/.test(lower) ? "bed" : /\.bedgraph(\.gz)?$/.test(lower) ? "bedgraph" : undefined;
        if (!format) throw new ViewerFileError(/\.(gz|bgz|zip|bz2|xz)$/.test(lower) ? "trackCompression" : "trackUnsupported", name);
        let index: string | undefined;
        if (lower.endsWith(".gz")) {
            for (const candidate of [`${file}.tbi`, `${file}.csi`]) {
                try {
                    if ((await stat(candidate)).isFile()) {
                        index = candidate;
                        break;
                    }
                } catch (error) {
                    if (!["ENOENT", "ENOTDIR"].includes(errnoCode(error) ?? "")) throw error;
                }
            }
            if (!index) throw new ViewerFileError("trackIndexMissing", name);
            await guardedViewerPath(index, roots);
        }
        try {
            await validateViewerTrack(file, format, index, reference);
        } catch (error) {
            if (error instanceof ViewerFileError) throw new ViewerFileError(error.code, name);
            throw error;
        }
        tracks.push({
            name: name.replace(/[&<>"']/g, (character) => `&#${character.charCodeAt(0)};`),
            type: format === "bed" ? "annotation" : "wig", format, url: file,
            ...(index ? { indexURL: index } : {}), indexed: !!index || format === "bigwig",
            ...(format === "bedgraph" && index ? { referenceLengths: Object.fromEntries(reference) } : {}),
        });
    }
    return tracks;
}
