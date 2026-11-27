import { execFile } from "node:child_process";
import { promisify } from "node:util";

export type LocalQuery = (executable: string, args: string[]) => Promise<string>;
const exec = promisify(execFile);

export const queryNetworkCost: LocalQuery = async (executable, args) => (await exec(executable, args, {
    encoding: "utf8",
    env: { ...process.env, LC_ALL: "C" },
    timeout: 2000,
    killSignal: "SIGKILL",
    maxBuffer: 64 * 1024,
    windowsHide: true,
})).stdout;
