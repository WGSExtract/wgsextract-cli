import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import type { LibraryViewerSession } from "../../../shared/genome-viewer.js";
import { localViewerLocus, ViewerFileError } from "../../../shared/genome-viewer.js";
import { loadBundleState } from "./config-store.js";
import { parseConfigToml } from "./config-toml.js";
import { errnoCode, HttpError } from "./errors.js";
import { findConfigEditorControl } from "./manifest-lookup.js";
import { bundleEnvironment, configPath, expandWorkspaceToken, resolveUserPath } from "./paths.js";
import { resolvePlatformScriptPath } from "./platform-scripts.js";
import type { RequestHandlerContext } from "./routes.js";
import { usersWorkspaceFor } from "./users-workspace.js";
import { guardedViewerPath, type ViewerFiles } from "./viewer-files.js";
import { supplementalViewerTracks } from "./viewer-tracks.js";
import { installedViewerGenes } from "./viewer-genes.js";
import { installedViewerCytobands } from "./viewer-cytobands.js";

/** Persistent library settings, never request-provided roots or a whole home directory. */
export async function viewerLibraries(context: RequestHandlerContext) {
    if (context.manifest.id !== "wgs-extract") {
        throw new HttpError(403, "The viewer requires the WGS Extract bundle.");
    }
    const workspace = usersWorkspaceFor(context.bundleRoot);
    const saved = await loadBundleState(context.bundleRoot);
    const config = configPath(findConfigEditorControl(context.manifest, "wgs_settings"), saved.configFilePaths.wgs_settings, context.bundleRoot);
    let values: Record<string, unknown> = {};
    try {
        values = parseConfigToml(await readFile(config, "utf8"));
    } catch (error) {
        if (errnoCode(error) !== "ENOENT") {
            throw error;
        }
    }
    const configured = (key: string, fallback: string) => {
        const value = values[key];
        return resolveUserPath(typeof value === "string" && value.trim() ? value : fallback, workspace);
    };
    const genomes = configured("mygenomes_library", path.join(workspace, "mygenomes"));
    const reference = configured("reference_library", path.join(workspace, "reference"));
    const referenceGenomes = configured("reference_genome_library", path.join(reference, "genomes"));
    const roots = [genomes, reference, referenceGenomes];
    return { config, roots, referenceRoot: reference, genomeRoots: [genomes], key: JSON.stringify([context.bundleRoot, config, ...roots]) };
}

export async function openLibraryViewer(input: string, locus: string, context: RequestHandlerContext, files: ViewerFiles, trackPaths: string[] = []): Promise<LibraryViewerSession | { problem: string; fileName?: string }> {
    let position: string | undefined;
    try {
        position = locus ? localViewerLocus(locus) : undefined;
    } catch (error) {
        if (error instanceof ViewerFileError) {
            return { problem: error.code };
        }
        throw error;
    }
    if (input.split(/[\\/]/).includes("..")) {
        throw new HttpError(403, "Viewer path is outside the configured libraries.");
    }
    input = expandWorkspaceToken(input, context.bundleRoot);
    const library = await viewerLibraries(context);
    await guardedViewerPath(input, library.genomeRoots);
    const lower = input.toLowerCase();
    const format = lower.endsWith(".bam") ? "bam" : lower.endsWith(".cram") ? "cram" : /\.(g\.?vcf|vcf)\.(gz|bgz)$/.test(lower) ? "vcf" : undefined;
    if (!format) {
        return { problem: "unsupportedFile", fileName: path.basename(input) };
    }
    const candidates = format === "bam"
        ? [`${input}.bai`, input.replace(/\.bam$/i, ".bai"), `${input}.csi`]
        : format === "cram" ? [`${input}.crai`, input.replace(/\.cram$/i, ".crai")] : [`${input}.tbi`, `${input}.csi`];
    const index = await firstExisting(candidates);
    if (!index) {
        return { problem: "indexMissing", fileName: path.basename(input) };
    }
    await guardedViewerPath(index, library.genomeRoots);
    const script = await resolvePlatformScriptPath(path.join(context.bundleRoot, "scripts", "viewer-reference.sh"), context.bundleRoot);
    const result = await context.runProcess(script, [input, library.config, ...(position ? ["--no-locus"] : [])], {
        cwd: context.bundleRoot,
        env: { ...process.env, ...bundleEnvironment(context.bundleRoot) },
        timeoutMs: 30_000,
        maxOutputBytes: 65_536,
        maxErrorBytes: 4096,
    });
    if (result.exitCode !== 0 || result.stdoutTruncated) {
        return { problem: "headerToolMissing" };
    }
    const resolved: unknown = JSON.parse(result.stdout);
    if (!resolved || typeof resolved !== "object") {
        throw new HttpError(500, "Invalid local reference result.");
    }
    if ("problem" in resolved && typeof resolved.problem === "string") {
        return { problem: resolved.problem };
    }
    if (!("fasta" in resolved) || typeof resolved.fasta !== "string" || !("locus" in resolved) || typeof resolved.locus !== "string") {
        throw new HttpError(500, "Invalid local reference result.");
    }
    const fasta = resolved.fasta;
    const fai = `${fasta}.fai`;
    const gzi = /\.gz$/i.test(fasta) ? `${fasta}.gzi` : undefined;
    if (!await firstExisting([fai])) {
        return { problem: "referenceIndexMissing", fileName: path.basename(fasta) };
    }
    if (gzi && !await firstExisting([gzi])) {
        return { problem: "referenceCompressedIndexMissing", fileName: path.basename(fasta) };
    }
    position ??= localViewerLocus(resolved.locus);
    await guardedViewerPath(fasta, library.roots);
    await guardedViewerPath(fai, library.roots);
    const genes = await installedViewerGenes(fasta, library.referenceRoot);
    const cytobands = await installedViewerCytobands(fasta, library.referenceRoot);
    let supplemental;
    try {
        supplemental = await supplementalViewerTracks(trackPaths, fasta, library.genomeRoots, context.bundleRoot);
    } catch (error) {
        if (error instanceof ViewerFileError) return { problem: error.code, fileName: error.fileName };
        throw error;
    }
    const grant = await files.grant([input, index, fasta, fai, ...(gzi ? [gzi] : []),
        ...supplemental.flatMap((track) => [track.url, ...(track.indexURL ? [track.indexURL] : [])])], library.roots, library.key);
    const url = (file: string) => {
        const value = grant.urls.get(file);
        if (!value) {
            throw new HttpError(500, "Missing viewer file grant.");
        }
        return value;
    };
    return {
        id: grant.id,
        locus: position,
        files: {
            genes,
            cytobands,
            reference: {
                id: "local", name: path.basename(fasta), fastaURL: url(fasta), indexURL: url(fai),
                ...(gzi ? { compressedIndexURL: url(gzi) } : {}), headers: grant.headers, wholeGenomeView: false,
            },
            tracks: [{
                name: path.basename(input).replace(/[&<>"']/g, (character) => `&#${character.charCodeAt(0)};`),
                format, type: format === "vcf" ? "variant" : "alignment", indexed: true,
                url: url(input), indexURL: url(index), headers: grant.headers,
            }, ...supplemental.map((track) => ({
                ...track, url: url(track.url), ...(track.indexURL ? { indexURL: url(track.indexURL) } : {}), headers: grant.headers,
            }))],
        },
    };
}

async function firstExisting(candidates: string[]) {
    for (const candidate of candidates) {
        try {
            if ((await stat(candidate)).isFile()) {
                return candidate;
            }
        } catch (error) {
            if (!["ENOENT", "ENOTDIR"].includes(errnoCode(error) ?? "")) {
                throw error;
            }
        }
    }
    return undefined;
}
