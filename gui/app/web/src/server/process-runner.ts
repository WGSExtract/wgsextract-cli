import { spawn } from "node:child_process";
import { platform } from "node:os";
import { StringDecoder } from "node:string_decoder";
import type { ProcessResult, ProcessRunOptions, RunProcess } from "../../../shared/types.js";
import { platformCommand } from "./platform-command.js";
import { terminateProcessTree } from "./process-tree.js";
import { TailBuffer } from "./tail-buffer.js";
import { cleanupElevated, windowsAdminCommand } from "./windows-admin.js";
import { errorMessage } from "./errors.js";

export interface ProcessManagerOptions {
    maxOutputBytes: number;
    maxErrorBytes: number;
    /** How long a terminated process tree gets to exit after SIGTERM before SIGKILL. */
    terminationGraceMs?: number;
    /** How long to keep reading output after the direct child exits while descendants still hold its pipes. */
    exitDrainMs?: number;
}

export interface ProcessManager {
    runProcess: RunProcess;
    terminateAllProcesses: () => Promise<void>;
}

const defaultTerminationGraceMs = 3000;
const defaultExitDrainMs = 200;
// Leave time after escalation to observe the child exit, within the desktop close budget.
const terminationExitMs = 1000;

export function createProcessManager(options: ProcessManagerOptions): ProcessManager {
    const terminationGraceMs = options.terminationGraceMs ?? defaultTerminationGraceMs;
    const exitDrainMs = options.exitDrainMs ?? defaultExitDrainMs;
    // Keep tracking children while descendants hold their pipes or a requested tree stop
    // remains unconfirmed.
    const active = new Set<() => Promise<void>>();

    const runProcess: RunProcess = async (executable, args, runOptions) => {
        const baseCommand = await platformCommand(executable, args);
        const elevated = runOptions.requiresAdmin && platform() === "win32"
            ? await windowsAdminCommand(baseCommand, runOptions)
            : undefined;
        try {
            if (runOptions.signal?.aborted) {
                throw new Error("Process cancelled.");
            }
            return await spawnAndCollect(elevated?.command ?? baseCommand, runOptions);
        }
        finally {
            await cleanupElevated(elevated);
        }
    };

    function spawnAndCollect(command: { executable: string; args: string[] }, runOptions: ProcessRunOptions): Promise<ProcessResult> {
        return new Promise((resolve, reject) => {
            const child = spawn(command.executable, command.args, {
                cwd: runOptions.cwd,
                env: runOptions.env,
                shell: false,
                detached: platform() !== "win32",
                stdio: ["ignore", "pipe", "pipe"],
                windowsHide: true,
            });
            const pid = child.pid;
            const stdout = new CapturedStream(runOptions.maxOutputBytes ?? options.maxOutputBytes, runOptions.onStdout);
            const stderr = new CapturedStream(runOptions.maxErrorBytes ?? options.maxErrorBytes, runOptions.onStderr);
            let termination: Promise<void> | undefined;
            let terminationState: "pending" | "confirmed" | "failed" | undefined;
            let stopReason: "cancelled" | "timeout" | undefined;
            let exit: { code: number | null; signal: NodeJS.Signals | null } | undefined;
            let closed = false;
            let finished = false;
            let drainTimer: NodeJS.Timeout | undefined;
            const exited = new Promise<void>((done) => child.once("exit", () => done()));

            const terminate = () => {
                if (pid == null) return Promise.resolve();
                // Once the child has exited, its PID may have been reused. Keep a failed
                // tree stop visible to later callers rather than retrying against that PID.
                if (!termination || (terminationState === "failed" && !exit)) {
                    terminationState = "pending";
                    termination = terminateAndWait(pid).then(() => {
                        terminationState = "confirmed";
                        if (closed) active.delete(terminate);
                    }, (error: unknown) => {
                        terminationState = "failed";
                        throw error;
                    });
                }
                return termination;
            };
            async function terminateAndWait(pid: number) {
                const controller = new AbortController();
                let timer: NodeJS.Timeout | undefined;
                try {
                    await Promise.race([
                        Promise.all([terminateProcessTree(pid, terminationGraceMs, controller.signal), exited]),
                        new Promise<never>((_, rejectDeadline) => {
                            timer = setTimeout(() => {
                                rejectDeadline(new Error(`Timed out waiting for process ${pid} to terminate.`));
                                controller.abort();
                            }, Math.max(defaultTerminationGraceMs, terminationGraceMs) + terminationExitMs);
                        }),
                    ]);
                    runOptions.onTerminated?.();
                }
                finally {
                    clearTimeout(timer);
                }
            }
            if (pid != null) {
                active.add(terminate);
            }
            const finish = (error?: Error) => {
                if (finished) {
                    return;
                }
                finished = true;
                clearTimeout(timeoutTimer);
                clearTimeout(drainTimer);
                runOptions.signal?.removeEventListener("abort", onAbort);
                stdout.end();
                stderr.end();
                if (error) {
                    reject(error);
                }
                else if (stopReason === "cancelled") {
                    reject(new Error("Process cancelled."));
                }
                else if (stopReason === "timeout") {
                    const seconds = Math.max(1, Math.ceil((runOptions.timeoutMs ?? 0) / 1000));
                    reject(new Error(`Process timed out after ${seconds} seconds.`));
                }
                else {
                    resolve({
                        exitCode: exit?.code ?? null,
                        signal: exit?.signal ?? null,
                        stdout: stdout.text(),
                        stderr: stderr.text(),
                        stdoutTruncated: stdout.truncated,
                        stderrTruncated: stderr.truncated,
                    });
                }
            };
            // A stopped run settles once its direct child has exited (escalating to SIGKILL if needed),
            // so callers never start follow-up work while it is still running.
            const stop = (reason: "cancelled" | "timeout") => {
                stopReason ??= reason;
                void terminate().catch((error: unknown) => console.warn(errorMessage(error)));
                if (exit) {
                    finish();
                }
            };
            const onAbort = () => stop("cancelled");
            const timeoutTimer = runOptions.timeoutMs ? setTimeout(() => stop("timeout"), runOptions.timeoutMs) : undefined;
            runOptions.signal?.addEventListener("abort", onAbort, { once: true });

            child.stdout.on("data", (chunk: Buffer) => stdout.write(chunk));
            child.stderr.on("data", (chunk: Buffer) => stderr.write(chunk));
            child.once("exit", (code, signal) => {
                exit = { code, signal };
                if (stopReason) {
                    finish();
                    return;
                }
                // Descendants (for example `cmd &`) may keep the pipes open; don't wait for them.
                drainTimer = setTimeout(() => finish(), exitDrainMs);
            });
            child.once("close", () => {
                closed = true;
                if (!termination || terminationState === "confirmed") {
                    active.delete(terminate);
                }
                finish();
            });
            child.on("error", (error) => {
                if (exit == null && pid == null) {
                    finish(error);
                    return;
                }
                console.warn(`Process ${pid ?? "?"} reported an error: ${errorMessage(error)}`);
            });
        });
    }

    async function terminateAllProcesses() {
        const results = await Promise.allSettled([...active.values()].map((terminate) => terminate()));
        for (const result of results) {
            if (result.status === "rejected") throw result.reason;
        }
    }

    return { runProcess, terminateAllProcesses };
}

class CapturedStream {
    private readonly buffer: TailBuffer;
    private readonly decoder = new StringDecoder("utf8");
    private open = true;

    constructor(limit: number, private readonly onText: ((text: string) => void | Promise<void>) | undefined) {
        this.buffer = new TailBuffer(limit);
    }

    get truncated() {
        return this.buffer.truncated;
    }

    write(chunk: Buffer) {
        if (!this.open) {
            return;
        }
        this.buffer.push(chunk);
        this.forward(this.decoder.write(chunk));
    }

    end() {
        if (!this.open) {
            return;
        }
        this.forward(this.decoder.end());
        this.open = false;
    }

    text() {
        return this.buffer.text();
    }

    private forward(text: string) {
        if (!text || !this.onText) {
            return;
        }
        try {
            void Promise.resolve(this.onText(text)).catch(warnOutputCallbackFailure);
        }
        catch (error) {
            warnOutputCallbackFailure(error);
        }
    }
}

function warnOutputCallbackFailure(error: unknown) {
    console.warn(`Process output callback failed: ${errorMessage(error)}`);
}
