import { stat } from "node:fs/promises";
import path from "node:path";
import { GeneDocumentError, geneDocument, type GeneDocument, type GeneExport } from "../../../shared/gene-page.js";
import { parseResultDocumentText, resultNotice, type ResultDocumentSpec } from "../../../shared/result-document.js";
import { isViewerGeneSymbol } from "../../../shared/viewer-genes.js";
import type { ActionSpec, CommandContext, LooseRecord, MissingPrerequisite } from "../../../shared/types.js";
import type { ActionEmit, ActionRunResult } from "./action-runner.js";
import { errnoCode, HttpError } from "./errors.js";
import { bundleEnvironment, expandWorkspaceToken } from "./paths.js";
import { resolvePlatformScriptPath } from "./platform-scripts.js";
import { installPrerequisite, missingPrerequisites } from "./prerequisites.js";
import type { ResultPaths } from "./result-paths.js";
import type { RequestHandlerContext } from "./routes.js";
import { viewerLibraries } from "./viewer-session.js";

export type GenePageProblem = { problem: string; fileName?: string; message?: string };

export type GenePageResult =
    | { document: GeneDocument; exportPath?: string; exportRows?: number }
    | GenePageProblem;

export type GenePagePrerequisitesResult =
    | { missing: MissingPrerequisite[] }
    | GenePageProblem;

interface PreparedGeneRequest {
    args: string[];
    checkAction: ActionSpec;
    requested: string | undefined;
    env: Record<string, string>;
}

const geneExports: readonly GeneExport[] = ["all", "rare-damaging", "clinvar-plp", "acmg-plp"];
const alignmentPattern = /\.(bam|cram)$/i;
const vcfPattern = /\.(g?vcf(\.b?gz)?|bcf)$/i;
const emptyContext: CommandContext = { fieldValues: {}, checkedOptions: {}, configValues: {}, rowValues: {} };

async function prepareGeneRequest(body: LooseRecord, context: RequestHandlerContext): Promise<PreparedGeneRequest | GenePageProblem> {
    const gene = typeof body.gene === "string" ? body.gene.trim() : "";
    if (!isViewerGeneSymbol(gene)) return { problem: "invalidGene" };
    const alignment = await localFile(body.alignment, alignmentPattern, "unsupportedAlignment", context);
    if (typeof alignment === "object") return alignment;
    const vcf = await localFile(body.vcf, vcfPattern, "unsupportedVcf", context);
    if (typeof vcf === "object") return vcf;
    const minDepth = body.minDepth;
    if (minDepth != null && (typeof minDepth !== "number" || !Number.isFinite(minDepth) || minDepth < 0 || minDepth > 100_000)) {
        return { problem: "invalidDepth" };
    }
    const requested = body.export;
    if (requested != null && (typeof requested !== "string" || !geneExports.includes(requested as GeneExport))) {
        throw new HttpError(400, "Unknown gene export.");
    }
    if (requested != null && !vcf) return { problem: "exportNeedsVcf" };
    const library = await viewerLibraries(context);
    const baseArgs = [
        "info", "gene", "--gene", gene,
        ...(alignment ? ["--input", alignment] : []),
        ...(vcf ? ["--vcf-input", vcf] : []),
        ...(typeof minDepth === "number" ? ["--min-depth", String(minDepth)] : []),
    ];
    return {
        args: [...baseArgs, ...(typeof requested === "string" ? ["--export", requested] : []), "--json"],
        checkAction: { id: "gene-page", title: "Gene page", prerequisiteArguments: baseArgs },
        requested: typeof requested === "string" ? requested : undefined,
        env: { WGSEXTRACT_GUI_CONFIG_PATH: library.config },
    };
}

/** What reference data `info gene` needs for these inputs that isn't installed yet. */
export async function genePagePrerequisites(body: LooseRecord, context: RequestHandlerContext): Promise<GenePagePrerequisitesResult> {
    const prepared = await prepareGeneRequest(body, context);
    if ("problem" in prepared) return prepared;
    const missing = await missingPrerequisites(context.manifest, prepared.checkAction, emptyContext, context.bundleRoot, context.runProcess, prepared.env);
    return { missing };
}

/** Installs one missing prerequisite for the gene page, using the server's own fresh prerequisite check. */
export async function installGenePagePrerequisite(
    body: LooseRecord,
    id: string,
    context: RequestHandlerContext,
    signal: AbortSignal | undefined,
    emit: ActionEmit,
    resultSpecFor: (installAction: ActionSpec) => ResultDocumentSpec | undefined,
): Promise<ActionRunResult> {
    const prepared = await prepareGeneRequest(body, context);
    if ("problem" in prepared) {
        throw new HttpError(400, "Invalid gene page inputs.");
    }
    return installPrerequisite(context.manifest, prepared.checkAction, id, emptyContext, signal, context.bundleRoot, context.runProcess, emit, resultSpecFor, prepared.env);
}

/**
 * Runs `wgsextract info gene` through the bundle's CLI wrapper, as data sources do. The
 * CLI reads only local files and downloads nothing; the page gets its document.
 */
export async function openGenePage(body: LooseRecord, context: RequestHandlerContext, resultPaths: ResultPaths, signal: AbortSignal): Promise<GenePageResult> {
    const prepared = await prepareGeneRequest(body, context);
    if ("problem" in prepared) return prepared;
    const script = await resolvePlatformScriptPath(path.join(context.bundleRoot, "scripts", "run-wgsextract.sh"), context.bundleRoot);
    const result = await context.runProcess(script, prepared.args, {
        cwd: context.bundleRoot,
        env: { ...process.env, ...bundleEnvironment(context.bundleRoot), ...prepared.env },
        signal,
        timeoutMs: 300_000,
        maxOutputBytes: 64 * 1024 * 1024,
        maxErrorBytes: 64 * 1024,
    });
    const document = result.stdoutTruncated ? null : parseResultDocumentText(result.stdout, "wgsextract.result/1");
    if (!document || document.status !== "ok") {
        const notice = resultNotice(document, result.exitCode, result.stderr);
        const message = notice?.message || document?.error?.message || result.stderr.trim().split(/\r?\n/).at(-1) || "";
        return { problem: notice?.kind === "usage" ? "geneUsage" : notice ? "geneToolMissing" : "geneFailed", message };
    }
    let page: GeneDocument;
    try {
        page = geneDocument(document.raw.details);
    } catch (error) {
        if (error instanceof GeneDocumentError) return { problem: "geneFailed", message: error.message };
        throw error;
    }
    if (!prepared.requested) return { document: page };
    resultPaths.remember(document, context.bundleRoot);
    const output = document.outputs.find((file) => file.role === "variant_table");
    return { document: page, ...(output ? { exportPath: output.path, exportRows: Number(output.metrics.rows ?? 0) } : {}) };
}

/** An absolute local path to an existing file of the expected kind, "" when none was given. */
async function localFile(value: unknown, pattern: RegExp, unsupported: string, context: RequestHandlerContext): Promise<string | { problem: string; fileName?: string }> {
    if (value == null || value === "") return "";
    if (typeof value !== "string" || value.length > 4096) throw new HttpError(400, "Gene page inputs must be local file paths.");
    const file = expandWorkspaceToken(value.trim(), context.bundleRoot);
    const fileName = path.basename(file);
    if (!path.isAbsolute(file)) return { problem: "pathNotAbsolute", fileName };
    if (!pattern.test(file)) return { problem: unsupported, fileName };
    try {
        if (!(await stat(file)).isFile()) return { problem: "fileMissing", fileName };
    } catch (error) {
        if (["ENOENT", "ENOTDIR"].includes(errnoCode(error) ?? "")) return { problem: "fileMissing", fileName };
        throw error;
    }
    return file;
}
