import { interpolate, renderedCommand } from "../../../shared/rendering.js";
import type { ResultDocumentSpec } from "../../../shared/result-document.js";
import type { ActionSpec, BundleManifest, CommandContext, MissingPrerequisite, RunProcess } from "../../../shared/types.js";
import { contextWithFileState, runAction, type ActionEmit, type ActionRunResult } from "./action-runner.js";
import { errorMessage } from "./errors.js";
import { bundleEnvironment } from "./paths.js";
import { isPlatformScriptReference, resolvePlatformScriptPath } from "./platform-scripts.js";

// Reading a BAM/CRAM header and the reference library takes a few seconds; a check that hangs mustn't hold the action back forever.
const checkTimeoutMs = 60_000;

/**
 * What `action` needs that isn't installed, from the bundle's prerequisite check (docs/prerequisites.md).
 * An action the check doesn't cover, or a check that fails, needs nothing: the action then runs and reports any
 * problem itself, as it did before the check existed.
 */
export async function missingPrerequisites(
    manifest: BundleManifest,
    action: ActionSpec,
    context: CommandContext,
    bundleRoot: string,
    runProcess: RunProcess,
): Promise<MissingPrerequisite[]> {
    try {
        return await checkPrerequisites(manifest, action, context, bundleRoot, runProcess);
    }
    catch (error) {
        console.warn(`[gui-for-cli] Prerequisite check for action ${action.id ?? action.title ?? ""} failed; running it anyway: ${errorMessage(error)}`);
        return [];
    }
}

/** What `action` needs that isn't installed; throws when the check fails. */
async function checkPrerequisites(
    manifest: BundleManifest,
    action: ActionSpec,
    context: CommandContext,
    bundleRoot: string,
    runProcess: RunProcess,
): Promise<MissingPrerequisite[]> {
    const spec = manifest.prerequisites;
    if (!spec || !action.command) {
        return [];
    }
    const resolvedContext = await contextWithFileState(context, bundleRoot);
    const commandLine = checkedCommandLine(spec.executables, action, resolvedContext);
    if (!commandLine) {
        return [];
    }
    const check = renderedCommand(spec.command, resolvedContext);
    const executable = await scriptPath(check.executable, bundleRoot);
    const result = await runProcess(executable, [...(check.arguments ?? []), ...commandLine], {
        cwd: bundleRoot,
        env: { ...process.env, ...bundleEnvironment(bundleRoot) },
        timeoutMs: checkTimeoutMs,
    });
    if (result.exitCode !== 0) {
        throw new Error(`exited ${result.exitCode}: ${result.stderr.trim().slice(-500) || "no stderr"}`);
    }
    return parseMissing(result.stdout);
}

/**
 * Installs the missing item `id` of `action`, as the check reports it now, with the check's executable. The
 * client names only the item, so it can't run arbitrary arguments. An item the check no longer reports was
 * installed meanwhile: that completes at once with exit code 0, so the client goes on to run the action. A check
 * that fails, or an item the app can't install, is an error.
 */
export async function installPrerequisite(
    manifest: BundleManifest,
    action: ActionSpec,
    id: string,
    context: CommandContext,
    signal: AbortSignal | undefined,
    bundleRoot: string,
    runProcess: RunProcess,
    emit: ActionEmit,
    resultSpecFor: (installAction: ActionSpec) => ResultDocumentSpec | undefined,
): Promise<ActionRunResult> {
    const spec = manifest.prerequisites;
    if (!spec) {
        throw new Error("This bundle declares no prerequisite check.");
    }
    const missing = await checkPrerequisites(manifest, action, context, bundleRoot, runProcess);
    const item = missing.find((candidate) => candidate.id === id);
    if (!item) {
        const now = new Date().toISOString();
        const result: ActionRunResult = { exitCode: 0, stdout: "", stderr: "", startedAt: now, finishedAt: now, command: "" };
        await emit({ type: "complete", result });
        return result;
    }
    if (!item.installArguments) {
        throw new Error(`The app can't install ${item.name}.`);
    }
    const installAction: ActionSpec = {
        id: `install-${item.id}`,
        title: item.name,
        command: { executable: spec.command.executable, arguments: item.installArguments },
    };
    return runAction(installAction, context, signal, bundleRoot, runProcess, emit, resultSpecFor(installAction));
}

/** The command line the check is given for `action`, or null when the check doesn't cover it. */
function checkedCommandLine(executables: Record<string, string[]>, action: ActionSpec, context: CommandContext): string[] | null {
    if (action.prerequisiteArguments) {
        return renderedCommand({ executable: "", arguments: action.prerequisiteArguments }, context).arguments ?? [];
    }
    const rendered = renderedCommand(action.command, context);
    const prefix = Object.entries(executables).find(([executable]) => interpolate(executable, context) === rendered.executable)?.[1];
    return prefix ? [...prefix, ...(rendered.arguments ?? [])] : null;
}

async function scriptPath(executable: string, bundleRoot: string): Promise<string> {
    return isPlatformScriptReference(executable, bundleRoot) ? resolvePlatformScriptPath(executable, bundleRoot) : executable;
}

function parseMissing(stdout: string): MissingPrerequisite[] {
    // The report is the last line; a wrapper may print others before it.
    const line = stdout.trim().split(/\r?\n/).pop() ?? "";
    const report: unknown = JSON.parse(line || "{}");
    const items = report && typeof report === "object" && "missing" in report ? report.missing : undefined;
    if (!Array.isArray(items)) {
        throw new Error("printed no {\"missing\": [...]} report");
    }
    return items.map((item: unknown) => {
        const record = (item ?? {}) as Record<string, unknown>;
        const { id, kind, name, installArguments } = record;
        if (typeof id !== "string" || !id || typeof kind !== "string" || typeof name !== "string") {
            throw new Error(`reported an item without id, kind and name: ${JSON.stringify(item)}`);
        }
        const install = Array.isArray(installArguments) && installArguments.every((argument) => typeof argument === "string")
            ? installArguments as string[]
            : null;
        return { id, kind, name, installArguments: install };
    });
}
