import { spawn } from "node:child_process";
import { captureViewerGroups } from "./viewer-process-groups.mjs";

/** On timeout, freeze launches and kill every owned group, including Chromium. */
export async function runViewerVerification(args, { cwd, timeout = 170000, signal, onOutput } = {}) {
  const child = spawn(process.execPath, args, {
    cwd, detached: process.platform !== "win32", windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const chunks = [];
  for (const stream of [child.stdout, child.stderr]) stream.on("data", chunk => {
    chunks.push(chunk);
    onOutput?.(chunk.toString());
  });
  let cancelled = false;
  let shutdown;
  const stop = () => {
    if (cancelled || !child.pid) return;
    cancelled = true;
    shutdown = (async () => {
      if (process.platform === "win32") {
        const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
        await new Promise((resolve, reject) => { killer.once("error", reject); killer.once("close", resolve); });
        return null;
      }
      const groups = await captureViewerGroups(child.pid);
      groups.kill();
      return groups;
    })();
    // Handle capture/tool errors immediately, even if the runner has not closed.
    void shutdown.catch(() => child.kill("SIGTERM"));
  };
  const timer = setTimeout(stop, timeout);
  signal?.addEventListener("abort", stop, { once: true });
  if (signal?.aborted) stop();
  try {
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", resolve);
    });
    if (shutdown) {
      const groups = await shutdown;
      await groups?.wait();
    }
    const output = Buffer.concat(chunks).toString();
    if (cancelled) throw new Error(`Offline verification cancelled or timed out.\n${output}`);
    if (code !== 0) throw new Error(`Offline verification exited ${code}.\n${output}`);
    return output;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", stop);
  }
}
