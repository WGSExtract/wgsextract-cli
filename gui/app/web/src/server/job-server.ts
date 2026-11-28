import { homedir } from "node:os";
import { renderedCommand } from "../../../shared/rendering.js";
import type { JobServerSpec, RunProcess } from "../../../shared/types.js";
import { errorMessage } from "./errors.js";
import type { JobServerAddress } from "./job-server-client.js";
import { bundleEnvironment } from "./paths.js";
import { isPlatformScriptReference, resolvePlatformScriptPath } from "./platform-scripts.js";
import { localJobServerURL } from "./local-job-server-url.js";
import type { NetworkMode } from "../../../shared/network-settings.js";
import { cliNetworkArguments, isWgsExtractCli } from "./cli-network-policy.js";

const defaultStartupTimeoutSeconds = 60;

export interface JobServer {
    /** The running server's address, starting it on first use; undefined when it can't be started. */
    address: () => Promise<JobServerAddress | undefined>;
    /** Stops the server; the next `address()` starts a new one. */
    stop: (onlyAddress?: JobServerAddress) => void;
    /** The current desktop mode for a new WGS Extract job, independent of the listener's lifetime. */
    networkMode?: () => Promise<NetworkMode>;
}

/**
 * Runs the bundle's job server command through `runProcess`, so shutdown and bundle
 * reloads terminate it with every other process. A server that exits is started again
 * on next use.
 */
export function createJobServer(spec: JobServerSpec, bundleRoot: string, runProcess: RunProcess, readMode?: () => Promise<NetworkMode>): JobServer {
    const networkMode = isWgsExtractCli(spec.command.executable, spec.command.arguments) ? readMode : undefined;
    let starting: Promise<JobServerAddress | undefined> | undefined;
    let stopper: AbortController | undefined;
    let currentAddress: JobServerAddress | undefined;

    async function start(): Promise<JobServerAddress | undefined> {
        const controller = new AbortController();
        stopper = controller;
        // {{appPid}} lets the server exit when the app dies, even when it is killed.
        const rendered = renderedCommand(spec.command, { bundleRootPath: bundleRoot, homePath: homedir(), fieldValues: { appPid: String(process.pid) } });
        const executable = isPlatformScriptReference(rendered.executable, bundleRoot)
            ? await resolvePlatformScriptPath(rendered.executable, bundleRoot)
            : rendered.executable;
        const timeoutMs = (spec.startupTimeoutSeconds ?? defaultStartupTimeoutSeconds) * 1000;
        return new Promise((resolve) => {
            let pending = "";
            let announced = false;
            const clearCurrent = () => {
                if (stopper === controller) {
                    starting = undefined;
                    stopper = undefined;
                    currentAddress = undefined;
                }
            };
            const fail = (reason: string) => {
                clearTimeout(timer);
                if (!announced) {
                    announced = true;
                    console.warn(`Job server unavailable: ${reason}`);
                    controller.abort();
                    resolve(undefined);
                }
            };
            const timer = setTimeout(() => fail(`no ready line within ${timeoutMs / 1000} seconds`), timeoutMs);
            const onStdout = (text: string) => {
                if (announced || stopper !== controller) {
                    return;
                }
                // Wrapper scripts may print other lines first.
                const lines = (pending + text).split("\n");
                pending = lines.pop() ?? "";
                for (const line of lines) {
                    const refusal = startupError(line);
                    if (refusal) {
                        fail(`refused to start: ${refusal}`);
                        return;
                    }
                    const address = readyAddress(line);
                    if (address) {
                        currentAddress = address;
                        announced = true;
                        clearTimeout(timer);
                        resolve(address);
                        return;
                    }
                }
            };
            const args = rendered.arguments ?? [];
            // The listener never transfers data. Its floor must allow all modes so
            // new jobs can tighten or relax their own policy without restarting it.
            runProcess(executable, networkMode ? cliNetworkArguments(args, "full") : args, {
                cwd: bundleRoot,
                env: { ...process.env, ...bundleEnvironment(bundleRoot) },
                signal: controller.signal,
                maxOutputBytes: 64 * 1024,
                onStdout,
                onTerminated: () => {
                    announced = true;
                    clearTimeout(timer);
                    clearCurrent();
                    resolve(undefined);
                },
            })
                .then((result) => fail(`exited with code ${result.exitCode ?? result.signal}: ${result.stderr.trim().slice(-500)}`))
                .catch((error: unknown) => fail(errorMessage(error)))
                .finally(clearCurrent);
        });
    }

    return {
        ...(networkMode ? { networkMode } : {}),
        address: () => {
            starting ??= start();
            return starting;
        },
        stop: (onlyAddress) => {
            if (onlyAddress && currentAddress !== onlyAddress) return;
            stopper?.abort();
            starting = undefined;
            stopper = undefined;
            currentAddress = undefined;
        },
    };
}

/** The message of an `{"error": {"message": ...}}` line, printed by a server that can't start. */
function startupError(line: string): string | undefined {
    try {
        const value: unknown = JSON.parse(line);
        if (value && typeof value === "object" && "error" in value) {
            const { error } = value;
            if (typeof error === "string") {
                return error;
            }
            if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
                return error.message;
            }
        }
    }
    catch {
        // Not JSON.
    }
    return undefined;
}

function readyAddress(line: string): JobServerAddress | undefined {
    try {
        const value: unknown = JSON.parse(line);
        if (value && typeof value === "object" && "url" in value && "token" in value) {
            const { url, token } = value;
            const local = typeof url === "string" ? localJobServerURL(url) : undefined;
            if (local && typeof token === "string" && token) {
                return { url: local, token, clientJobIDs: "client_job_ids" in value && value.client_job_ids === true };
            }
        }
    }
    catch {
        // Not JSON; reported by the caller.
    }
    return undefined;
}
