import { homedir } from "node:os";
import path from "node:path";
import { renderedCommand } from "../../../shared/rendering.js";
import type { ActionSpec, CommandContext, LooseRecord } from "../../../shared/types.js";
import { contextWithFileState } from "./action-runner.js";

const excludedTopCommands = new Set([
    "help", "config", "deps", "report-issue", "sample", "serve", "ref", "example-genome", "benchmark",
]);
const excludedFullCommands = new Set([
    "bam index", "bam unindex", "bam unsort", "sign verify",
]);
const excludedFlags = new Set(["--list", "--list-presets", "--status", "--dry-run", "--delete"]);
export const forceFlags = new Set(["--force", "--no-resume", "--restart", "--force-extract", "--force-step", "--force-steps"]);
const commandsSupportingForce = new Set(["qc multiqc", "vep run"]);
const commandsSupportingRestart = new Set([
    "realign", "analyze comprehensive", "vcf chain-annotate", "vcf deepvariant",
]);

/** Flags that do not change which logical analysis result a command produces. */
const ignoredFlags = new Set([
    "config", "result_file", "card", "no_card", "json", "quiet", "verbose", "debug",
    "keep_awake", "atomic_output", "outdir", "threads", "tmp_dir", "force",
    "resume", "no_resume", "restart", "force_steps", "log_file",
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

export interface ParsedInvocation {
    args: string[];
    flags: Map<string, string[] | boolean>;
    positionals: string[];
    explicitOutdir: string | undefined;
    inputPaths: string[];
}

/**
 * Returns `action` with `--force` (or `--restart` for step workflows) added when the user explicitly
 * chose to force re-run an already-completed command whose CLI skips existing outputs by default.
 */
export async function actionWithForce(action: ActionSpec, context: CommandContext, bundleRoot: string): Promise<ActionSpec> {
    if (!action.command) {
        return action;
    }
    const resolvedContext = await contextWithFileState(context, bundleRoot);
    const args = renderedCommand(action.command, resolvedContext).arguments ?? [];
    const forceMode = forceModeForArgs(args);
    if (!forceMode || args.some((arg) => forceFlags.has(arg))) {
        return action;
    }
    const cliFlag = forceMode === "restart" ? "--restart" : "--force";
    const paramKey = forceMode === "restart" ? "restart" : "force";
    const command = {
        ...action.command,
        optionalArguments: [...(action.command.optionalArguments ?? []), [cliFlag]],
    };
    const serverOperation = action.serverOperation
        ? { ...action.serverOperation, parameters: { ...(action.serverOperation.parameters ?? {}), [paramKey]: true } }
        : undefined;
    return { ...action, command, ...(serverOperation ? { serverOperation } : {}) };
}

function forceModeForArgs(args: string[]): "force" | "restart" | undefined {
    const two = args.slice(0, 2).join(" ");
    const one = args[0] ?? "";
    if (commandsSupportingRestart.has(two) || commandsSupportingRestart.has(one)) return "restart";
    if (commandsSupportingForce.has(two) || commandsSupportingForce.has(one)) return "force";
    return undefined;
}

export function isEligibleArgs(args: string[], action: ActionSpec): boolean {
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
    if (action.serverOperation?.parameters?.force === true || action.serverOperation?.parameters?.restart === true) {
        return false;
    }
    return true;
}

export function commandMatches(currentArgs: string[], cmdWords: string[]): boolean {
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

export function optionsMatch(current: ParsedInvocation, raw: LooseRecord, cmdWords: string[], bundleRoot: string): boolean {
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

export function parseArgs(args: string[], startIndex: number, bundleRoot: string): ParsedInvocation {
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
        const nextToken = i + 1 < args.length ? (args[i + 1] ?? "") : undefined;
        const nextIsFlagValue = nextToken !== undefined && (!nextToken.startsWith("-") || /^-\d/.test(nextToken));
        if (inlineValue !== undefined) {
            value = inlineValue;
        } else if (rawFlag.startsWith("--no-") && !globalValueFlags.has(rawFlag)) {
            key = normalizeFlagName(`--${rawFlag.slice(5)}`);
            value = false;
        } else if (nextIsFlagValue) {
            value = nextToken;
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

export function resolveOutputDirectory(
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
    // `wgse` is the output folder of releases up to v0.8.8.
    return ["wgsev6", "wgse"].includes(path.basename(parent).toLowerCase()) ? parent : path.join(parent, "WGSEv6");
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

export function provenanceRecord(raw: LooseRecord): LooseRecord | undefined {
    return raw.provenance && typeof raw.provenance === "object" && !Array.isArray(raw.provenance)
        ? raw.provenance as LooseRecord
        : undefined;
}

function looksLikePath(value: string): boolean {
    return value.includes("/") || value.includes("\\") || value.startsWith("~") || primaryRank(value) !== undefined;
}

export function normalizePath(filePath: string, bundleRoot: string): string {
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
