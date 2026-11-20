import { execFile } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { promisify } from "node:util";

const exec = promisify(execFile);
async function processes() {
  const { stdout } = await exec("ps", ["-eo", "pid=,ppid=,pgid=,stat="]);
  return stdout.trim().split("\n").map(line => {
    const [pid, parent, group, state] = line.trim().split(/\s+/);
    return { pid: Number(pid), parent: Number(parent), group: Number(group), state };
  });
}

function send(group, signal) {
  try { process.kill(-group, signal); } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
}

/** Freeze launches first; Chromium forks into a group separate from the verifier. */
export async function captureViewerGroups(pid) {
  const descendants = new Set([pid]);
  const groups = new Set([pid]);
  send(pid, "SIGSTOP");
  try {
    // Each discovered group is frozen before another scan, so neither the
    // verifier nor its browser can launch a group after the ownership snapshot.
    for (;;) {
      const rows = await processes();
      for (let previous = 0; previous !== descendants.size;) {
        previous = descendants.size;
        for (const row of rows) if (descendants.has(row.parent) || groups.has(row.group)) descendants.add(row.pid);
      }
      const next = rows.filter(row => descendants.has(row.pid) && !groups.has(row.group));
      if (!next.length) break;
      for (const row of next) {
        groups.add(row.group);
        send(row.group, "SIGSTOP");
      }
    }
  } catch (error) {
    for (const group of groups) {
      send(group, "SIGKILL");
      send(group, "SIGCONT");
    }
    throw error;
  }
  return {
    kill() { for (const group of groups) send(group, "SIGKILL"); },
    async wait() {
      for (let attempt = 0; attempt < 100; attempt++) {
        const alive = (await processes()).some(row => groups.has(row.group) && !row.state.startsWith("Z"));
        if (!alive) return;
        await delay(50);
      }
      throw new Error("Viewer process groups did not shut down; retain verification output.");
    },
  };
}
