import { readdir, readFile, stat } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import { renderedCommand } from "../../../shared/rendering.js";
import { parseResultDocument, preferredOutput, resultCardOutput, resultCardRole, resultDocumentSpecFor, type ResultDocument } from "../../../shared/result-document.js";
import type { ActionSpec, BundleManifest, CommandContext, LooseRecord } from "../../../shared/types.js";
import { contextWithFileState } from "./action-runner.js";
import { loadBundleState } from "./bundle-state.js";
import { parseConfigToml } from "./config-toml.js";
import { errnoCode } from "./errors.js";
import { findConfigEditorControl } from "./manifest-lookup.js";
import { configPath, resolveUserPath } from "./paths.js";
import type { ResultPaths } from "./result-paths.js";
import { usersWorkspaceFor } from "./users-workspace.js";

export type ExistingOutputResult =
    | { kind: "none" }
    | { kind: "display"; document: ResultDocument; cardPath?: string; openPath?: string }
    | { kind: "already-run"; document: ResultDocument; outputs: string[]; finishedAt?: string };

const manifestSuffix = ".manifest.json";
const failedManifestSuffix = ".failed.manifest.json";

const excludedTopCommands = new Set([
    "help", "config", "deps", "report-issue", "sample", "serve", "ref", "example-genome", "benchmark",
]);
const excludedFullCommands = new Set([
    "bam index", "bam unindex", "bam unsort", "sign verify",
]);
const excludedFlags = new Set(["--list", "--list-presets", "--status", "--dry-run", "--delete"]);
const forceFlags = new Set(["--force", "--no-resume", "--force-extract", "--force-step"]);
const commandsSupportingForce = new Set([
    "qc multiqc", "vep run", "realign", "analyze comprehensive", "vcf chain-annotate", "vcf deepvariant",
]);

/** Flags that do not change which logical analysis result a command produces. */
const ignoredFlags = new Set([
    "config", "result_file", "card", "no_card", "json", "quiet", "verbose", "debug",
    "keep_awake", "atomic_output", "outdir", "threads", "tmp_dir", "force", "no_resume", "log_file",
]);

/** Short CLI aliases mapped to their canonical argparse dest names. */
const shortFlagAliases: Record<string, string> = {
    "-i": "input",
    "-o": "outdir",
    "-r": "region",
    "-t": "threads",
};

/** Global flags (with one argument) that may precede a subcommand in provenance.argv. */
const globalValueFlags = new Set(["--config", "--result-file", "--log-file", "--tmp-dir", "--threads", "-t"]);
const globalBoolFlags = new Set(["--json", "--card", "--no-card", "--quiet", "--verbose", "--debug", "--keep-awake", "--atomic-output"]);

/** Primary input file flags in rank order (matching src/wgsextract_cli/core/output_location.py). */
const primaryInputFlags = new Set(["input", "vcf_input", "proband", "r1", "r2", "se"]);
const secondaryInputFileFlags = new Set(["by", "bed", "gene_file", "extra_vcf", "pedigree", "mother", "father", "panel", "targets"]);
const primaryInputRoles = new Set(["input", "vcf_input", "proband", "fastq_r1", "fastq_r2"]);
const compressedSuffixes = [".gz", ".bgz", ".zip", ".bz2", ".xz", ".zst"];
const primaryKindSuffixes: readonly (readonly string[])[] = [
    [".bam", ".cram", ".sam"],
    [".fastq", ".fq", ".ora"],
    [".vcf", ".bcf", ".gvcf"],
    [".txt", ".csv", ".tsv"],
];
const differentiatingServerParams = new Set([
    "detailed", "with_bins", "bins", "by", "region", "gene", "genes", "preset", "formats",
    "ybrowse", "export", "filter", "expr", "sample", "build", "table_format", "fraction", "mode", "caller", "model", "panel",
]);

interface ParsedInvocation {
    args: string[];
    flags: Map<string, string[] | boolean>;
    positionals: string[];
    explicitOutdir: string | undefined;
    inputPaths: string[];
}

interface CandidateMatch {
    result: ExistingOutputResult;
    document: ResultDocument;
    timestampMs: number;
}

/**
 * Checks whether `action` has already been run with matching inputs and options and all of its
 * permanent outputs in the output folder exist and are newer than the inputs.
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
    const configuredOutdir = parsed.explicitOutdir ? undefined : await configuredOutputDirectory(manifest, bundleRoot);
    const outdir = resolveOutputDirectory(args, parsed.explicitOutdir, configuredOutdir, parsed.inputPaths, bundleRoot);
    if (!outdir) {
        return { kind: "none" };
    }
    const entries = await listManifestFiles(outdir);
    if (!entries.length) {
        return { kind: "none" };
    }

    let best: CandidateMatch | undefined;
    for (const fileName of entries) {
        const manifestPath = path.join(outdir, fileName);
        const candidate = await evaluateManifest(manifestPath, outdir, entries, args, action, spec.format, bundleRoot);
        if (candidate && (!best || candidate.timestampMs > best.timestampMs)) {
            best = candidate;
        }
    }
    if (!best) {
        return { kind: "none" };
    }
    resultPaths.remember(best.document, bundleRoot);
    return best.result;
}

/**
 * Returns `action` with `--force` added when the user explicitly chose to force re-run an
 * already-completed command whose CLI skips existing outputs unless `--force` is given.
 */
export async function actionWithForce(action: ActionSpec, context: CommandContext, bundleRoot: string): Promise<ActionSpec> {
    if (!action.command) {
        return action;
    }
    const resolvedContext = await contextWithFileState(context, bundleRoot);
    const args = renderedCommand(action.command, resolvedContext).arguments ?? [];
    const matched = matchedForceCommand(args);
    if (!matched || args.some((arg) => forceFlags.has(arg))) {
        return action;
    }
    const command = {
        ...action.command,
        optionalArguments: [...(action.command.optionalArguments ?? []), ["--force"]],
    };
    const serverOperation = action.serverOperation
        ? { ...action.serverOperation, parameters: { ...(action.serverOperation.parameters ?? {}), force: true } }
        : undefined;
    return { ...action, command, ...(serverOperation ? { serverOperation } : {}) };
}

function matchedForceCommand(args: string[]): string | undefined {
    const two = args.slice(0, 2).join(" ");
    if (commandsSupportingForce.has(two)) return two;
    const one = args[0] ?? "";
    return commandsSupportingForce.has(one) ? one : undefined;
}

function isEligibleArgs(args: string[], action: ActionSpec): boolean {
    const first = args[0] ?? "";
    if (!first || first.startsWith("-") || excludedTopCommands.has(first)) {
        return false;
    }
    const two = args.slice(0, 2).join(" ");
    if (excludedFullCommands.has(two)) {
        return false;
    }
    if (args.some((arg) => excludedFlags.has(arg) || forceFlags.has(arg))) {
        return false;
    }
    if (action.serverOperation?.parameters?.force === true) {
        return false;
    }
    return true;
}

async function evaluateManifest(
    manifestPath: string,
    outdir: string,
    allManifestFiles: string[],
    currentArgs: string[],
    action: ActionSpec,
    format: "wgsextract.result/1",
    bundleRoot: string,
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
    const freshness = await checkFreshness(doc, recordedInputs, outdir, allManifestFiles, manifestPath);
    if (!freshness.fresh) {
        return undefined;
    }

    const card = action.resultCard ? resultCardOutput(doc) : undefined;
    const report = preferredOutput(doc, action.openOutput);
    const nonCardOutputs = doc.outputs.filter((output) => output.role !== resultCardRole && output.exists !== false);

    let result: ExistingOutputResult;
    if (card || report) {
        result = {
            kind: "display",
            document: doc,
            ...(card ? { cardPath: card.path } : {}),
            ...(report ? { openPath: report.path } : {}),
        };
    } else if (nonCardOutputs.length > 0) {
        const finishedAt = typeof provenanceRecord(doc.raw)?.finished === "string"
            ? String(provenanceRecord(doc.raw)?.finished)
            : undefined;
        result = {
            kind: "already-run",
            document: doc,
            outputs: nonCardOutputs.map((output) => path.basename(output.path)),
            ...(finishedAt ? { finishedAt } : {}),
        };
    } else {
        // E.g. a report-only action whose result card is absent: re-run to produce the card.
        return undefined;
    }
    return { result, document: doc, timestampMs: freshness.timestampMs };
}

function commandMatches(currentArgs: string[], cmdWords: string[]): boolean {
    if (cmdWords.length === 0 || currentArgs.length < cmdWords.length) {
        return false;
    }
    for (let i = 0; i < cmdWords.length; i += 1) {
        if (currentArgs[i] !== cmdWords[i]) {
            return false;
        }
    }
    // Prevent a 1-word command like "info" from matching "info calculate-coverage".
    const next = currentArgs[cmdWords.length];
    if (next && !next.startsWith("-") && cmdWords.length === 1 && /^[a-z][a-z0-9-]*$/.test(next)) {
        return false;
    }
    return true;
}

interface RecordedInput {
    path: string;
    role: string;
    sizeBytes: number | null;
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

function optionsMatch(current: ParsedInvocation, raw: LooseRecord, cmdWords: string[], bundleRoot: string): boolean {
    const provenance = provenanceRecord(raw);
    const prevArgv = Array.isArray(provenance?.argv)
        ? provenance.argv.filter((entry): entry is string => typeof entry === "string")
        : [];
    if (prevArgv.length > 0) {
        const stripped = stripLeadingGlobalFlags(prevArgv);
        if (commandMatches(stripped, cmdWords)) {
            const previous = parseArgs(stripped, cmdWords.length, bundleRoot);
            return comparableFlagsEqual(current, previous, bundleRoot);
        }
    }
    const params = provenance?.params && typeof provenance.params === "object" && !Array.isArray(provenance.params)
        ? provenance.params as LooseRecord
        : undefined;
    if (!params) {
        return false;
    }
    return paramsMatch(current, params, bundleRoot);
}

function comparableFlagsEqual(left: ParsedInvocation, right: ParsedInvocation, bundleRoot: string): boolean {
    const leftMap = semanticFlags(left.flags, bundleRoot);
    const rightMap = semanticFlags(right.flags, bundleRoot);
    if (leftMap.size !== rightMap.size) return false;
    for (const [key, value] of leftMap) {
        if (rightMap.get(key) !== value) return false;
    }
    const leftPos = left.positionals.map((item) => normalizeValue(item, bundleRoot));
    const rightPos = right.positionals.map((item) => normalizeValue(item, bundleRoot));
    return leftPos.length === rightPos.length && leftPos.every((value, index) => value === rightPos[index]);
}

function semanticFlags(flags: Map<string, string[] | boolean>, bundleRoot: string): Map<string, string> {
    const result = new Map<string, string>();
    for (const [key, value] of flags) {
        if (ignoredFlags.has(key)) continue;
        if (typeof value === "boolean") {
            result.set(key, String(value));
        } else {
            result.set(key, JSON.stringify(value.map((entry) => normalizeValue(entry, bundleRoot))));
        }
    }
    return result;
}

function paramsMatch(current: ParsedInvocation, params: LooseRecord, bundleRoot: string): boolean {
    for (const [key, value] of current.flags) {
        if (ignoredFlags.has(key)) continue;
        const recorded = params[key];
        if (typeof value === "boolean") {
            if (Boolean(recorded) !== value) return false;
        } else if (!valuesEqual(value, recorded, bundleRoot)) {
            return false;
        }
    }
    for (const key of differentiatingServerParams) {
        if (current.flags.has(key)) continue;
        const recorded = params[key];
        if (recorded !== undefined && recorded !== null && recorded !== false && recorded !== "" && !(Array.isArray(recorded) && recorded.length === 0)) {
            return false;
        }
    }
    return true;
}

function valuesEqual(given: string[], recorded: unknown, bundleRoot: string): boolean {
    if (recorded == null) return false;
    if (Array.isArray(recorded)) {
        const expected = recorded.map((item) => normalizeValue(String(item), bundleRoot));
        const actual = given.flatMap((item) => item.includes(",") && expected.length > 1 ? item.split(",") : [item])
            .map((item) => normalizeValue(item, bundleRoot));
        return actual.length === expected.length && actual.every((item, index) => item === expected[index]);
    }
    const single = given.at(-1) ?? "";
    if (typeof recorded === "number") {
        return Number(single) === recorded;
    }
    if (typeof recorded === "boolean") {
        return (single.toLowerCase() === "true") === recorded;
    }
    return normalizeValue(single, bundleRoot) === normalizeValue(String(recorded), bundleRoot);
}

async function checkFreshness(
    doc: ResultDocument,
    recordedInputs: RecordedInput[],
    outdir: string,
    allManifestFiles: string[],
    manifestPath: string,
): Promise<{ fresh: boolean; timestampMs: number }> {
    let maxInputMtimeMs = 0;
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
    for (const output of doc.outputs) {
        if (output.exists === false) continue;
        const info = await safeStat(output.path);
        if (!info) return { fresh: false, timestampMs: 0 };
        if (info.mtimeMs < minOutputMtimeMs) minOutputMtimeMs = info.mtimeMs;
        if (info.mtimeMs > maxOutputMtimeMs) maxOutputMtimeMs = info.mtimeMs;
    }
    if (!Number.isFinite(minOutputMtimeMs) || minOutputMtimeMs < maxInputMtimeMs) {
        return { fresh: false, timestampMs: 0 };
    }
    // A sample summary report aggregates every analysis manifest in outdir: any newer manifest makes it stale.
    if (doc.command === "report") {
        const selfName = path.basename(manifestPath);
        for (const other of allManifestFiles) {
            if (other === selfName) continue;
            const info = await safeStat(path.join(outdir, other));
            if (info && info.mtimeMs > minOutputMtimeMs) {
                return { fresh: false, timestampMs: 0 };
            }
        }
    }
    return { fresh: true, timestampMs: maxOutputMtimeMs };
}

function stripLeadingGlobalFlags(argv: string[]): string[] {
    let index = 0;
    while (index < argv.length) {
        const token = argv[index] ?? "";
        if (globalValueFlags.has(token)) {
            index += 2;
            continue;
        }
        if (globalBoolFlags.has(token) || [...globalValueFlags].some((flag) => token.startsWith(`${flag}=`))) {
            index += 1;
            continue;
        }
        break;
    }
    return argv.slice(index);
}

function parseArgs(args: string[], startIndex: number, bundleRoot: string): ParsedInvocation {
    const flags = new Map<string, string[] | boolean>();
    const positionals: string[] = [];
    const inputPaths: string[] = [];
    let explicitOutdir: string | undefined;

    for (let i = startIndex; i < args.length; i += 1) {
        const token = args[i] ?? "";
        if (!token.startsWith("-") || token === "-") {
            positionals.push(token);
            if (looksLikePath(token)) {
                inputPaths.push(normalizePath(token, bundleRoot));
            }
            continue;
        }
        const eqIndex = token.indexOf("=");
        const rawFlag = eqIndex >= 0 ? token.slice(0, eqIndex) : token;
        const inlineValue = eqIndex >= 0 ? token.slice(eqIndex + 1) : undefined;
        let key = normalizeFlagName(rawFlag);
        let value: string | boolean;
        if (inlineValue !== undefined) {
            value = inlineValue;
        } else if (rawFlag.startsWith("--no-") && !globalValueFlags.has(rawFlag)) {
            key = normalizeFlagName(`--${rawFlag.slice(5)}`);
            value = false;
        } else if (i + 1 < args.length && !(args[i + 1] ?? "").startsWith("-")) {
            value = args[i + 1] ?? "";
            i += 1;
        } else {
            value = true;
        }

        if (typeof value === "boolean") {
            flags.set(key, value);
        } else {
            const existing = flags.get(key);
            flags.set(key, Array.isArray(existing) ? [...existing, value] : [value]);
            if (key === "outdir") {
                explicitOutdir = normalizePath(value, bundleRoot);
            } else if (primaryInputFlags.has(key) || secondaryInputFileFlags.has(key)) {
                inputPaths.push(normalizePath(value, bundleRoot));
            }
        }
    }
    return { args, flags, positionals, explicitOutdir, inputPaths };
}

function normalizeFlagName(rawFlag: string): string {
    if (rawFlag in shortFlagAliases) {
        return shortFlagAliases[rawFlag] ?? rawFlag;
    }
    return rawFlag.replace(/^-+/, "").replaceAll("-", "_");
}

function resolveOutputDirectory(
    args: string[],
    explicitOutdir: string | undefined,
    configuredOutdir: string | undefined,
    inputPaths: string[],
    bundleRoot: string,
): string | undefined {
    if (explicitOutdir) return explicitOutdir;
    if (configuredOutdir) return configuredOutdir;
    const primary = choosePrimaryInput(inputPaths);
    if (!primary) return undefined;
    const parent = path.dirname(normalizePath(primary, bundleRoot));
    if (args[0] === "sign" && args[1] === "create") {
        return parent;
    }
    return path.basename(parent).toLowerCase() === "wgse" ? parent : path.join(parent, "wgse");
}

function choosePrimaryInput(paths: string[]): string | undefined {
    let best: { rank: number; file: string } | undefined;
    for (const file of paths) {
        if (!file || file.includes("://")) continue;
        const rank = primaryRank(file);
        if (rank !== undefined && (!best || rank < best.rank)) {
            best = { rank, file };
        }
    }
    return best?.file;
}

function primaryRank(filePath: string): number | undefined {
    let name = path.basename(filePath).toLowerCase();
    for (const suffix of compressedSuffixes) {
        if (name.endsWith(suffix)) {
            name = name.slice(0, -suffix.length);
            break;
        }
    }
    for (let rank = 0; rank < primaryKindSuffixes.length; rank += 1) {
        if (primaryKindSuffixes[rank]?.some((suffix) => name.endsWith(suffix))) {
            return rank;
        }
    }
    return undefined;
}

async function configuredOutputDirectory(manifest: BundleManifest, bundleRoot: string): Promise<string | undefined> {
    try {
        const control = findConfigEditorControl(manifest, "wgs_settings");
        const saved = await loadBundleState(bundleRoot);
        const file = configPath(control, saved.configFilePaths.wgs_settings, bundleRoot);
        const values = parseConfigToml(await readFile(file, "utf8"));
        const raw = values.output_directory;
        if (typeof raw === "string" && raw.trim()) {
            return resolveUserPath(raw.trim(), usersWorkspaceFor(bundleRoot));
        }
    } catch {
        // Non-WGSExtract bundles or missing config.toml simply use default output locations.
    }
    return undefined;
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

async function safeStat(filePath: string) {
    try {
        return await stat(filePath);
    } catch {
        return undefined;
    }
}

function provenanceRecord(raw: LooseRecord): LooseRecord | undefined {
    return raw.provenance && typeof raw.provenance === "object" && !Array.isArray(raw.provenance)
        ? raw.provenance as LooseRecord
        : undefined;
}

function looksLikePath(value: string): boolean {
    return value.includes("/") || value.includes("\\") || value.startsWith("~") || primaryRank(value) !== undefined;
}

function normalizePath(filePath: string, bundleRoot: string): string {
    const trimmed = filePath.trim();
    if (!trimmed || trimmed.includes("://")) return trimmed;
    const expanded = trimmed === "~"
        ? homedir()
        : trimmed.startsWith("~/") || trimmed.startsWith("~\\")
            ? path.join(homedir(), trimmed.slice(2))
            : trimmed;
    return path.resolve(bundleRoot, expanded);
}

function normalizeValue(value: string, bundleRoot: string): string {
    return looksLikePath(value) ? normalizePath(value, bundleRoot) : value.trim();
}
