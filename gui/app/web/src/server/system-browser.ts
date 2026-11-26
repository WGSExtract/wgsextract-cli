import { spawn } from "node:child_process";
import { systemOpener } from "./result-paths.js";

export function openSystemBrowser(url: URL): Promise<void> {
    return launchBrowserProcess(systemOpener(), [url.href], process.platform === "win32");
}

export function launchBrowserProcess(executable: string, args: string[], explorer = false): Promise<void> {
    return new Promise((resolve, reject) => {
        const child = spawn(executable, args, { detached: true, stdio: "ignore", windowsHide: true });
        // xdg-open may stay alive until the browser exits. Catch immediate launcher failures
        // but do not wait for, capture, or terminate an external browser as an app job.
        let timer: ReturnType<typeof setTimeout> | undefined;
        child.once("spawn", () => {
            child.unref();
            timer = setTimeout(resolve, 500);
        });
        child.once("error", (error) => { clearTimeout(timer); reject(error); });
        child.once("exit", (code) => {
            clearTimeout(timer);
            if (code === 0 || (code === 1 && explorer)) resolve();
            else reject(new Error("The system browser launcher failed."));
        });
    });
}
