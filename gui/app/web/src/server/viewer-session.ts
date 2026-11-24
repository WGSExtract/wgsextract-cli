import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import type { LibraryViewerSession } from "../../../shared/genome-viewer.js";
import { localViewerLocus, ViewerFileError } from "../../../shared/genome-viewer.js";
import { rcrsContig, type ViewerBuild } from "../../../shared/viewer-rows.js";
import { loadBundleState } from "./bundle-state.js";
import { parseConfigToml } from "./config-toml.js";
import { errnoCode, HttpError } from "./errors.js";
import { findConfigEditorControl } from "./manifest-lookup.js";
import { bundleEnvironment, configPath, expandWorkspaceToken, resolveUserPath } from "./paths.js";
import { resolvePlatformScriptPath } from "./platform-scripts.js";
import type { RequestHandlerContext } from "./routes.js";
import { usersWorkspaceFor } from "./users-workspace.js";
import { guardedViewerPath, isMissingViewerPath, localViewerPath, type ViewerFiles } from "./viewer-files.js";
import { referenceLocus, viewerReferenceMetadata } from "./viewer-reference-metadata.js";
import { supplementalViewerTracks } from "./viewer-tracks.js";
import { installedViewerAnnotations } from "./viewer-annotations.js";
import { installedViewerGenes } from "./viewer-genes.js";
import { installedViewerCytobands } from "./viewer-cytobands.js";
import { installedViewerTranscripts } from "./viewer-transcripts.js";
import { installedViewerRsidIndex, lookupViewerRsid } from "./viewer-rsids.js";

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
    const referenceRoots = [reference, referenceGenomes];
    const roots = [genomes, ...referenceRoots];
    return { config, roots, referenceRoot: reference, referenceRoots, genomeRoots: [genomes], key: JSON.stringify([context.bundleRoot, config, ...roots]) };
}

type ViewerProblemResult = { problem: string; fileName?: string };

interface LibraryTrack {
    input: string;
    index: string;
    format: "bam" | "cram" | "vcf";
    library: Awaited<ReturnType<typeof viewerLibraries>>;
}

/** A BAM, CRAM or bgzip VCF with its index beside it; rejects traversal and (unless allowExternal) paths outside My Genomes. */
async function libraryTrack(input: string, context: RequestHandlerContext, allowExternal = false): Promise<LibraryTrack | ViewerProblemResult> {
    if (input.split(/[\\/]/).includes("..")) {
        throw new HttpError(403, "Viewer path is outside the configured libraries.");
    }
    input = expandWorkspaceToken(input, context.bundleRoot);
    const library = await viewerLibraries(context);
    if (allowExternal) {
        await localViewerPath(input);
    } else {
        await guardedViewerPath(input, library.genomeRoots);
    }
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
    if (allowExternal) {
        await localViewerPath(index);
    } else {
        await guardedViewerPath(index, library.genomeRoots);
    }
    return { input, index, format, library };
}

/**
 * Whether result-row links can open each sample file, before anyone clicks: the same library,
 * existence and index checks as opening, without resolving a reference or granting any bytes.
 */
export async function checkLibraryTracks(inputs: string[], context: RequestHandlerContext): Promise<Record<string, { ok: true } | ViewerProblemResult>> {
    const results: Record<string, { ok: true } | ViewerProblemResult> = {};
    for (const input of inputs) {
        const fileName = path.basename(input);
        try {
            const track = await libraryTrack(input, context, false);
            results[input] = "problem" in track
                ? { problem: track.problem === "indexMissing" ? "rowIndexMissing" : track.problem, fileName }
                : { ok: true };
        } catch (error) {
            if (isMissingViewerPath(error)) {
                results[input] = { problem: "rowTrackMissing", fileName };
            } else if (error instanceof HttpError && error.status === 403) {
                results[input] = { problem: "rowOutsideLibrary", fileName };
            } else {
                throw error;
            }
        }
    }
    return results;
}

export async function openLibraryViewer(requested: string, locus: string, context: RequestHandlerContext, files: ViewerFiles, trackPaths: string[] = [], build?: ViewerBuild): Promise<LibraryViewerSession | ViewerProblemResult> {
    const trimmedLocus = locus.trim();
    const initialRsid = /^rs\d+$/i.test(trimmedLocus) ? trimmedLocus : undefined;
    let position: string | undefined;
    if (!initialRsid) {
        try {
            position = trimmedLocus ? localViewerLocus(trimmedLocus) : undefined;
        } catch (error) {
            if (error instanceof ViewerFileError) {
                return { problem: error.code };
            }
            throw error;
        }
    }
    const track = await libraryTrack(requested, context, true);
    if ("problem" in track) {
        return track;
    }
    const { input, index, format, library } = track;
    const script = await resolvePlatformScriptPath(path.join(context.bundleRoot, "scripts", "viewer-reference.sh"), context.bundleRoot);
    // rCRS-numbered rows need the reference's mitochondrion identified by sequence, not length.
    const rcrs = position?.toLowerCase().startsWith(`${rcrsContig.toLowerCase()}:`) ?? false;
    const result = await context.runProcess(script, [input, library.config, ...(rcrs ? ["--mito-origin"] : [])], {
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
    if (rcrs && (!("mitoOrigin" in resolved) || resolved.mitoOrigin !== "rCRS")) {
        return { problem: "mitoNotRcrs" };
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
    await guardedViewerPath(fasta, library.referenceRoots);
    await guardedViewerPath(fai, library.referenceRoots);
    const genes = await installedViewerGenes(fasta, library.referenceRoot);
    const cytobands = await installedViewerCytobands(fasta, library.referenceRoot);
    const transcripts = await installedViewerTranscripts(fasta, library.referenceRoot);
    const annotations = await installedViewerAnnotations(fasta, library.referenceRoot);
    const rsid = await installedViewerRsidIndex(fasta, library.referenceRoot);
    if (initialRsid) {
        if (!rsid.index) {
            return { problem: "invalidLocus" };
        }
        const found = await lookupViewerRsid(rsid.index, initialRsid, library.referenceRoots);
        if ("problem" in found) {
            return { problem: found.problem };
        }
        position = found.locus;
    } else if (position) {
        // Result rows name contigs as their table did; open them in this reference's spelling.
        const metadata = await viewerReferenceMetadata(fasta);
        // Contig aliases never convert coordinates: a position on another build would point elsewhere.
        if (build && metadata?.build !== build) {
            return { problem: "buildMismatch" };
        }
        if (metadata) {
            const mapped = referenceLocus(position, metadata.reference);
            if ("problem" in mapped) {
                return mapped;
            }
            position = mapped.locus;
        }
    }
    position ??= localViewerLocus(resolved.locus);
    let supplemental;
    try {
        supplemental = await supplementalViewerTracks(trackPaths, fasta, context.bundleRoot);
    } catch (error) {
        if (error instanceof ViewerFileError) return { problem: error.code, fileName: error.fileName };
        throw error;
    }
    const extraTracks = [...supplemental, ...annotations];
    const guardedFiles = [fasta, fai, ...(gzi ? [gzi] : []),
        ...(transcripts.url && transcripts.indexURL ? [transcripts.url, transcripts.indexURL] : []),
        ...annotations.flatMap((track) => [track.url, ...(track.indexURL ? [track.indexURL] : [])])];
    const localFiles = [input, index, ...supplemental.flatMap((track) => [track.url, ...(track.indexURL ? [track.indexURL] : [])])];
    const grant = await files.grant(guardedFiles, library.referenceRoots, library.key, localFiles, rsid.index);
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
            transcripts: transcripts.url && transcripts.indexURL
                ? { url: url(transcripts.url), indexURL: url(transcripts.indexURL), headers: grant.headers, notices: [] }
                : { notices: transcripts.notices },
            rsids: rsid.status,
            reference: {
                id: "local", name: path.basename(fasta), fastaURL: url(fasta), indexURL: url(fai),
                ...(gzi ? { compressedIndexURL: url(gzi) } : {}), headers: grant.headers, wholeGenomeView: false,
            },
            tracks: [{
                name: path.basename(input).replace(/[&<>"']/g, (character) => `&#${character.charCodeAt(0)};`),
                format, type: format === "vcf" ? "variant" : "alignment", indexed: true,
                url: url(input), indexURL: url(index), headers: grant.headers,
            }, ...extraTracks.map((track) => ({
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
