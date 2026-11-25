import { readdir, readFile, realpath, stat } from "node:fs/promises";
import path from "node:path";
import { renderedCommand } from "../../../shared/rendering.js";
import { parseResultDocument, preferredOutput, resultCardOutput, resultCardRole, resultDocumentSpecFor, type ResultDocument } from "../../../shared/result-document.js";
import type { ActionSpec, BundleManifest, CommandContext, LooseRecord } from "../../../shared/types.js";
import { contextWithFileState } from "./action-runner.js";
import { loadBundleState } from "./bundle-state.js";
import { parseConfigToml } from "./config-toml.js";
import { errnoCode } from "./errors.js";
import {
    actionWithForce,
    commandMatches,
    isEligibleArgs,
    normalizePath,
    optionsMatch,
    parseArgs,
    provenanceRecord,
    resolveOutputDirectory,
} from "./existing-output-args.js";
import { findConfigEditorControl } from "./manifest-lookup.js";
import { configPath, resolveUserPath } from "./paths.js";
import type { ResultPaths } from "./result-paths.js";
import { usersWorkspaceFor } from "./users-workspace.js";

export { actionWithForce };

export type ExistingOutputResult =
    | { kind: "none" }
    | { kind: "display"; document: ResultDocument; cardPath?: string; openPath?: string; outputs?: string[]; finishedAt?: string }
    | { kind: "already-run"; document: ResultDocument; outputs: string[]; finishedAt?: string };

const manifestSuffix = ".manifest.json";
const failedManifestSuffix = ".failed.manifest.json";
const primaryInputRoles = new Set(["input", "vcf_input", "proband", "fastq_r1", "fastq_r2"]);
const aggregatingCommands = new Set(["report", "qc multiqc"]);

interface RecordedInput {
    path: string;
    role: string;
    sizeBytes: number | null;
}

interface CandidateMatch {
    result: ExistingOutputResult;
    document: ResultDocument;
    timestampMs: number;
}

/**
 * Checks whether `action` has already been run with matching inputs and options and all of its
 * permanent outputs in the output folder exist, stay inside `outdir`, and are newer than inputs.
 */
export async function findExistingOutput(
    manifest: BundleManifest,
    action: ActionSpec,
    context: CommandContext,
    bundleRoot: string,
    resultPaths: ResultPaths,
): Promise<ExistingOutputResult> {
    const spec = resultDocumentSpecFor(manifest, action);
    if (!spec || !action.command || action.viewerInput || action.confirm || action.role === "destructive") {
        return { kind: "none" };
    }
    const resolvedContext = await contextWithFileState(context, bundleRoot);
    const rendered = renderedCommand(action.command, resolvedContext);
    const args = rendered.arguments ?? [];
    if (!args.length || !isEligibleArgs(args, action)) {
        return { kind: "none" };
    }
    const parsed = parseArgs(args, 0, bundleRoot);
    const configured = await loadConfiguredSettings(manifest, bundleRoot);
    const configuredOutdir = parsed.explicitOutdir ? undefined : configured.outputDirectory;
    const outdir = resolveOutputDirectory(args, parsed.explicitOutdir, configuredOutdir, parsed.inputPaths, bundleRoot);
    if (!outdir) {
        return { kind: "none" };
    }
    const realOutdir = await safeRealpath(outdir);
    if (!realOutdir) {
        return { kind: "none" };
    }
    const entries = await listManifestFiles(realOutdir);
    if (!entries.length) {
        return { kind: "none" };
    }

    let best: CandidateMatch | undefined;
    for (const fileName of entries) {
        const manifestPath = path.join(realOutdir, fileName);
        const candidate = await evaluateManifest(manifestPath, realOutdir, entries, args, action, spec.format, bundleRoot, configured.configFile);
        if (candidate && (!best || candidate.timestampMs > best.timestampMs)) {
            best = candidate;
        }
    }
    if (!best) {
        return { kind: "none" };
    }
    resultPaths.remember(best.document, realOutdir);
    return best.result;
}

async function evaluateManifest(
    manifestPath: string,
    realOutdir: string,
    allManifestFiles: string[],
    currentArgs: string[],
    action: ActionSpec,
    format: "wgsextract.result/1",
    bundleRoot: string,
    configFile: string | undefined,
): Promise<CandidateMatch | undefined> {
    let raw: unknown;
    try {
        raw = JSON.parse(await readFile(manifestPath, "utf8"));
    } catch {
        return undefined;
    }
    const doc = parseResultDocument(raw, format);
    if (!doc || doc.status !== "ok" || doc.exitCode !== 0 || doc.outputs.length === 0) {
        return undefined;
    }
    const cmdWords = doc.command.split(/\s+/).filter(Boolean);
    if (!commandMatches(currentArgs, cmdWords)) {
        return undefined;
    }
    const current = parseArgs(currentArgs, cmdWords.length, bundleRoot);
    const recordedInputs = extractRecordedInputs(doc.raw);
    if (recordedInputs.length === 0) {
        return undefined;
    }
    if (!inputsMatch(current.inputPaths, recordedInputs, bundleRoot)) {
        return undefined;
    }
    if (!optionsMatch(current, doc.raw, cmdWords, bundleRoot)) {
        return undefined;
    }
    const checked = await checkFreshnessAndContainment(doc, recordedInputs, realOutdir, allManifestFiles, manifestPath, configFile);
    if (!checked.fresh || !checked.validatedDocument) {
        return undefined;
    }
    const validatedDoc = checked.validatedDocument;
    const card = action.resultCard ? resultCardOutput(validatedDoc) : undefined;
    const report = preferredOutput(validatedDoc, action.openOutput);
    const nonCardOutputs = validatedDoc.outputs.filter((output) => output.role !== resultCardRole && output.exists !== false);
    const finishedAt = typeof provenanceRecord(validatedDoc.raw)?.finished === "string"
        ? String(provenanceRecord(validatedDoc.raw)?.finished)
        : undefined;

    let result: ExistingOutputResult;
    if (card || report) {
        result = {
            kind: "display",
            document: validatedDoc,
            ...(card ? { cardPath: card.path } : {}),
            ...(report ? { openPath: report.path } : {}),
            outputs: nonCardOutputs.map((output) => path.basename(output.path)),
            ...(finishedAt ? { finishedAt } : {}),
        };
    } else if (nonCardOutputs.length > 0) {
        result = {
            kind: "already-run",
            document: validatedDoc,
            outputs: nonCardOutputs.map((output) => path.basename(output.path)),
            ...(finishedAt ? { finishedAt } : {}),
        };
    } else {
        // E.g. a report-only action whose result card is absent: re-run to produce the card.
        return undefined;
    }
    return { result, document: validatedDoc, timestampMs: checked.timestampMs };
}

function extractRecordedInputs(raw: LooseRecord): RecordedInput[] {
    if (!Array.isArray(raw.inputs)) return [];
    const results: RecordedInput[] = [];
    for (const item of raw.inputs) {
        if (!item || typeof item !== "object" || Array.isArray(item)) continue;
        const record = item as LooseRecord;
        if (typeof record.path !== "string" || !record.path) continue;
        results.push({
            path: record.path,
            role: typeof record.role === "string" ? record.role : "input",
            sizeBytes: typeof record.size_bytes === "number" ? record.size_bytes : null,
        });
    }
    return results;
}

function inputsMatch(currentInputs: string[], recordedInputs: RecordedInput[], bundleRoot: string): boolean {
    const recordedSet = new Set(recordedInputs.map((item) => normalizePath(item.path, bundleRoot)));
    const currentSet = new Set(currentInputs.map((item) => normalizePath(item, bundleRoot)));
    if (currentSet.size === 0) {
        return false;
    }
    for (const input of currentSet) {
        if (!recordedSet.has(input)) {
            return false;
        }
    }
    for (const item of recordedInputs) {
        if (primaryInputRoles.has(item.role) && !currentSet.has(normalizePath(item.path, bundleRoot))) {
            return false;
        }
    }
    return true;
}

async function checkFreshnessAndContainment(
    doc: ResultDocument,
    recordedInputs: RecordedInput[],
    realOutdir: string,
    allManifestFiles: string[],
    manifestPath: string,
    configFile: string | undefined,
): Promise<{ fresh: boolean; timestampMs: number; validatedDocument?: ResultDocument }> {
    let maxInputMtimeMs = 0;
    if (configFile) {
        // Editing config.toml (e.g., microarray_min_dp or confidence_min_depth) invalidates older runs.
        const cfgInfo = await safeStat(configFile);
        if (cfgInfo && cfgInfo.mtimeMs > maxInputMtimeMs) {
            maxInputMtimeMs = cfgInfo.mtimeMs;
        }
    }
    for (const input of recordedInputs) {
        if (input.path.includes("://")) continue;
        const info = await safeStat(input.path);
        if (!info) return { fresh: false, timestampMs: 0 };
        if (input.sizeBytes != null && info.isFile() && info.size !== input.sizeBytes) {
            return { fresh: false, timestampMs: 0 };
        }
        if (info.mtimeMs > maxInputMtimeMs) {
            maxInputMtimeMs = info.mtimeMs;
        }
    }
    let minOutputMtimeMs = Number.POSITIVE_INFINITY;
    let maxOutputMtimeMs = 0;
    const validatedOutputs: ResultDocument["outputs"] = [];
    for (const output of doc.outputs) {
        if (output.exists === false) continue;
        const resolved = path.isAbsolute(output.path) ? output.path : path.resolve(realOutdir, output.path);
        const canonical = await safeRealpath(resolved);
        // Untrusted manifests on disk must never authorize or launch paths outside outdir.
        if (!canonical || !isContainedInDirectory(canonical, realOutdir)) {
            return { fresh: false, timestampMs: 0 };
        }
        const info = await safeStat(canonical);
        if (!info) return { fresh: false, timestampMs: 0 };
        if (info.mtimeMs < minOutputMtimeMs) minOutputMtimeMs = info.mtimeMs;
        if (info.mtimeMs > maxOutputMtimeMs) maxOutputMtimeMs = info.mtimeMs;
        validatedOutputs.push({ ...output, path: canonical });
    }
    if (!Number.isFinite(minOutputMtimeMs) || minOutputMtimeMs < maxInputMtimeMs) {
        return { fresh: false, timestampMs: 0 };
    }
    // Aggregating commands (personal report, MultiQC) combine prior outputs/manifests in outdir.
    if (aggregatingCommands.has(doc.command)) {
        const selfName = path.basename(manifestPath);
        for (const other of allManifestFiles) {
            if (other === selfName) continue;
            const info = await safeStat(path.join(realOutdir, other));
            if (info && info.mtimeMs > minOutputMtimeMs) {
                return { fresh: false, timestampMs: 0 };
            }
        }
    }
    const validatedDocument: ResultDocument = {
        ...doc,
        outputs: validatedOutputs,
        manifestPath,
    };
    return { fresh: true, timestampMs: maxOutputMtimeMs, validatedDocument };
}

function isContainedInDirectory(candidatePath: string, parentDir: string): boolean {
    const relative = path.relative(parentDir, candidatePath);
    return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

async function loadConfiguredSettings(
    manifest: BundleManifest,
    bundleRoot: string,
): Promise<{ outputDirectory?: string; configFile?: string }> {
    try {
        const control = findConfigEditorControl(manifest, "wgs_settings");
        const saved = await loadBundleState(bundleRoot);
        const file = configPath(control, saved.configFilePaths.wgs_settings, bundleRoot);
        const values = parseConfigToml(await readFile(file, "utf8"));
        const raw = values.output_directory;
        const outputDirectory = typeof raw === "string" && raw.trim()
            ? resolveUserPath(raw.trim(), usersWorkspaceFor(bundleRoot))
            : undefined;
        return { ...(outputDirectory ? { outputDirectory } : {}), configFile: file };
    } catch {
        // Non-WGSExtract bundles or missing config.toml simply use default output locations.
        return {};
    }
}

async function listManifestFiles(outdir: string): Promise<string[]> {
    try {
        const entries = await readdir(outdir);
        return entries.filter((name) => name.endsWith(manifestSuffix) && !name.endsWith(failedManifestSuffix));
    } catch (error) {
        if (["ENOENT", "ENOTDIR"].includes(errnoCode(error) ?? "")) {
            return [];
        }
        throw error;
    }
}

async function safeRealpath(filePath: string): Promise<string | undefined> {
    try {
        return await realpath(filePath);
    } catch {
        return undefined;
    }
}

async function safeStat(filePath: string) {
    try {
        return await stat(filePath);
    } catch {
        return undefined;
    }
}
