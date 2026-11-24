import type { ActionSpec, BundleManifest, CommandSpec, LooseRecord } from "./types.js";
import type { ResultDocumentRecord, ResultErrorRecord, ResultFileRecord, ResultWarningRecord } from "./generated/result-records.js";

/** Result document formats the app can read (docs/result-documents.md). */
export const resultDocumentFormats = ["wgsextract.result/1"] as const satisfies readonly ResultDocumentRecord["schema"][];

export type ResultDocumentFormat = ResultDocumentRecord["schema"];

/**
 * A manifest's `resultDocument`: actions running one of `executables` write a result document,
 * to the file named by `{{resultDocumentPath}}` in `arguments` or else to stdout.
 */
export interface ResultDocumentSpec {
    format: ResultDocumentFormat;
    /** Raw `command.executable` values, as written in the pages. */
    executables: string[];
    /** Appended to the command, after its optional arguments, to ask for the document. */
    arguments?: string[];
    /** Appended after `arguments` for an action with `resultCard`, to ask for the run's result card. */
    cardArguments?: string[];
}

/** Stands in `arguments` for a file the app makes for each run and reads the document from. */
export const resultDocumentPathPlaceholder = "{{resultDocumentPath}}";

export type ResultScalar = ResultDocumentRecord["metrics"][string];
export type ResultScalars = ResultDocumentRecord["metrics"];

export interface ResultFile extends Pick<ResultFileRecord, "path" | "role" | "kind" | "exists" | "metrics"> {
    sizeBytes: ResultFileRecord["size_bytes"];
}

export interface ResultWarning {
    code: ResultWarningRecord["code"];
    message: ResultWarningRecord["message"];
    details: ResultWarningRecord["details"];
}

export interface ResultError {
    kind: string;
    message: ResultErrorRecord["message"];
}

/** The parts of a result document the app shows and checks. */
export interface ResultDocument extends Pick<ResultDocumentRecord, "command" | "metrics"> {
    format: ResultDocumentFormat;
    status: string;
    exitCode: ResultDocumentRecord["exit_code"] | null;
    error: ResultError | null;
    outputs: ResultFile[];
    warnings: ResultWarning[];
    /** Where the command saved the same document, if it did. */
    manifestPath: ResultDocumentRecord["manifest"];
    /** The document as the command printed it. */
    raw: LooseRecord;
}

/** What a failed run asks of the user: fix the options (usage) or install something (missing_dependency). */
export interface ResultNotice {
    kind: "usage" | "missing_dependency";
    message: string;
}

// 127 is the shell's "command not found", which a wrapper script also uses when the runtime is missing.
const exitCodeErrorKinds: Record<number, ResultNotice["kind"]> = { 2: "usage", 3: "missing_dependency", 127: "missing_dependency" };

/** The manifest's result document declaration when it covers this action's command. */
export function resultDocumentSpecFor(manifest: Pick<BundleManifest, "resultDocument"> | null | undefined, action: ActionSpec): ResultDocumentSpec | undefined {
    const spec = manifest?.resultDocument;
    return action.command && spec?.executables.includes(action.command.executable) ? spec : undefined;
}

/** Whether the document is written to a file the app provides rather than to stdout. */
export function resultDocumentInFile(spec: ResultDocumentSpec): boolean {
    return spec.arguments?.some((argument) => argument.includes(resultDocumentPathPlaceholder)) ?? false;
}

/** The command with the declaration's arguments added at the end, naming `documentPath` as the document's file. */
export function commandWithResultArguments(command: CommandSpec, spec: ResultDocumentSpec | undefined, documentPath: string, card = false): CommandSpec {
    const added = [
        ...(spec?.arguments ?? []).map((argument) => argument.replaceAll(resultDocumentPathPlaceholder, documentPath)),
        ...(card ? spec?.cardArguments ?? [] : []),
    ];
    return added.length ? { ...command, optionalArguments: [...(command.optionalArguments ?? []), added] } : command;
}

/** Parses printed stdout; null when it is not one document of the declared format. */
export function parseResultDocumentText(text: string, format: ResultDocumentFormat): ResultDocument | null {
    try {
        return parseResultDocument(JSON.parse(text.trim()), format);
    }
    catch {
        return null;
    }
}

/**
 * Reads a result document. Fields a job server leaves out when empty read as empty;
 * anything that is not a document of `format` reads as null.
 */
export function parseResultDocument(value: unknown, format: ResultDocumentFormat): ResultDocument | null {
    if (!isRecord(value) || value.schema !== format || typeof value.status !== "string") {
        return null;
    }
    const error = isRecord(value.error) ? { kind: text(value.error.kind), message: text(value.error.message) } : null;
    return {
        format,
        command: text(value.command),
        status: value.status,
        exitCode: typeof value.exit_code === "number" ? value.exit_code : null,
        error,
        outputs: records(value.outputs).map(resultFile).filter((file) => file.path),
        metrics: scalars(value.metrics),
        warnings: records(value.warnings).map((warning) => ({
            code: text(warning.code) || "warning",
            message: text(warning.message),
            details: scalars(warning.details),
        })),
        manifestPath: typeof value.manifest === "string" && value.manifest ? value.manifest : null,
        raw: value,
    };
}

/**
 * Maps a failed run to what the user should do. The document's error kind decides;
 * without a document (the CLI rejected its arguments before writing one, or a wrapper
 * script refused to run), exit code 2, 3 or 127 does, with the last line of stderr as the message.
 */
export function resultNotice(document: ResultDocument | null, exitCode: number | null, stderr: string): ResultNotice | undefined {
    const kind = document ? document.error?.kind : exitCodeErrorKinds[Number(exitCode)];
    if (kind !== "usage" && kind !== "missing_dependency") {
        return undefined;
    }
    const message = document?.error?.message || lastLine(stderr);
    return { kind, message };
}

/** The role `wgsextract --card` gives the page it writes of a run's result (docs/result-manifest.md). */
export const resultCardRole = "result_card";

/** The output an action's `openOutput` names: the first role in its list that the run wrote and still exists. */
export function preferredOutput(document: ResultDocument | null | undefined, roles: readonly string[] | undefined): ResultFile | undefined {
    for (const role of roles ?? []) {
        const output = document?.outputs.find((item) => item.role === role && item.exists);
        if (output) {
            return output;
        }
    }
    return undefined;
}

/** The run's result card, when it wrote one. */
export function resultCardOutput(document: ResultDocument | null | undefined): ResultFile | undefined {
    return document?.outputs.find((output) => output.role === resultCardRole && output.exists);
}

/** Paths a document lets the user open: its outputs and its saved manifest. */
export function resultDocumentPaths(document: ResultDocument): string[] {
    return [...document.outputs.map((output) => output.path), ...(document.manifestPath ? [document.manifestPath] : [])];
}

function resultFile(value: LooseRecord): ResultFile {
    return {
        path: text(value.path),
        role: text(value.role),
        kind: text(value.kind),
        exists: value.exists !== false,
        sizeBytes: typeof value.size_bytes === "number" ? value.size_bytes : null,
        metrics: scalars(value.metrics),
    };
}

function scalars(value: unknown): ResultScalars {
    const result: ResultScalars = {};
    if (isRecord(value)) {
        for (const [key, item] of Object.entries(value)) {
            if (item === null || ["string", "number", "boolean"].includes(typeof item)) {
                result[key] = item as ResultScalar;
            }
        }
    }
    return result;
}

function records(value: unknown): LooseRecord[] {
    return Array.isArray(value) ? value.filter(isRecord) : [];
}

function isRecord(value: unknown): value is LooseRecord {
    return value != null && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): string {
    return typeof value === "string" ? value : "";
}

function lastLine(value: string): string {
    return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).at(-1) ?? "";
}
