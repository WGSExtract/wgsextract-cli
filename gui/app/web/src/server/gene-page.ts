import { stat } from "node:fs/promises";
import path from "node:path";
import { GeneDocumentError, geneDocument, type GeneDocument, type GeneExport } from "../../../shared/gene-page.js";
import { parseResultDocumentText, resultNotice } from "../../../shared/result-document.js";
import { isViewerGeneSymbol } from "../../../shared/viewer-genes.js";
import type { LooseRecord } from "../../../shared/types.js";
import { errnoCode, HttpError } from "./errors.js";
import { bundleEnvironment, expandWorkspaceToken } from "./paths.js";
import { resolvePlatformScriptPath } from "./platform-scripts.js";
import type { ResultPaths } from "./result-paths.js";
import type { RequestHandlerContext } from "./routes.js";
import { viewerLibraries } from "./viewer-session.js";

export type GenePageResult =
    | { document: GeneDocument; exportPath?: string; exportRows?: number }
    | { problem: string; fileName?: string; message?: string };

const geneExports: readonly GeneExport[] = ["all", "rare-damaging", "clinvar-plp", "acmg-plp"];
const alignmentPattern = /\.(bam|cram)$/i;
const vcfPattern = /\.(g?vcf(\.b?gz)?|bcf)$/i;

/**
 * Runs `wgsextract info gene` through the bundle's CLI wrapper, as data sources do. The
 * CLI reads only local files and downloads nothing; the page gets its document.
 */
export async function openGenePage(body: LooseRecord, context: RequestHandlerContext, resultPaths: ResultPaths, signal: AbortSignal): Promise<GenePageResult> {
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
    const args = [
        "info", "gene", "--gene", gene,
        ...(alignment ? ["--input", alignment] : []),
        ...(vcf ? ["--vcf-input", vcf] : []),
        ...(typeof minDepth === "number" ? ["--min-depth", String(minDepth)] : []),
        ...(typeof requested === "string" ? ["--export", requested] : []),
        "--json",
    ];
    const script = await resolvePlatformScriptPath(path.join(context.bundleRoot, "scripts", "run-wgsextract.sh"), context.bundleRoot);
    const result = await context.runProcess(script, args, {
        cwd: context.bundleRoot,
        env: { ...process.env, ...bundleEnvironment(context.bundleRoot), WGSEXTRACT_GUI_CONFIG_PATH: library.config },
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
    if (!requested) return { document: page };
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
