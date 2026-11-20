import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { runBundleTest } from "../dist/web/src/server/bundle-test-runner.js";
import { currentSetupPlatform } from "../dist/shared/setup-platforms.js";

const otherPlatform = currentSetupPlatform() === "windows" ? "linux" : "windows";

for (const scenario of [
  { name: "no setup", setup: [], runSetup: false, ready: true },
  { name: "other-platform setup", setup: [{ platforms: [otherPlatform] }], runSetup: false, ready: true },
  { name: "setup not yet run", setup: [{}], runSetup: false, ready: false },
  { name: "successful setup", setup: [{}], runSetup: true, ready: true },
  { name: "optional setup warning", setup: [{ value: "missing-tool-1138", optional: true }], runSetup: true, ready: true },
  { name: "failed setup", setup: [{ value: "missing-tool-1138" }], runSetup: true, ready: false },
]) {
  test(`#1138: headless report action respects ${scenario.name}`, async () => {
    const root = await mkdtemp(path.join(tmpdir(), "setup-ready-"));
    const bundleRoot = path.join(root, "Bundle");
    const workspaceRoot = path.join(root, "Workspace");
    await mkdir(bundleRoot);
    await writeFile(path.join(bundleRoot, "manifest.json"), JSON.stringify({
      id: "dev.wgsextract-gui.setup-ready",
      displayName: "Setup readiness",
      summary: "Setup-gated report regression fixture.",
      setup: {
        steps: scenario.setup.map((step) => ({
          id: "node", kind: "pathTool", label: "Find Node", value: process.execPath, ...step,
        })),
      },
      pages: [{
        id: "main", title: "Main", summary: "Report actions.", sections: [{
          id: "report", actions: [{
            id: "report-issue", title: "Report issue",
            disabledWhen: [{ placeholder: "setupReady", notEquals: "true" }],
            command: { executable: process.execPath, arguments: ["-e", "console.log('report sent')"] },
          }],
        }],
      }],
    }));
    try {
      const report = await runBundleTest(bundleRoot, {
        steps: [
          ...(scenario.runSetup ? [{ kind: "setup", continueOnFailure: true }] : []),
          { kind: "action", actionID: "report-issue" },
        ],
      }, { workspaceURL: workspaceRoot });
      const action = report.steps.at(-1);
      assert.equal(action.status, scenario.ready ? "passed" : "failed", JSON.stringify(report.steps));
      if (scenario.ready) {
        assert.match(action.output, /report sent/);
      } else {
        assert.match(action.error, /Action is disabled/);
      }
      if (scenario.name === "successful setup") {
        const reportPath = path.join(root, "report.json");
        await promisify(execFile)(process.execPath, [
          fileURLToPath(new URL("../dist/web/src/server/bundle-test-cli.js", import.meta.url)),
          "--run-setup", "--action", "report-issue",
          "--workspace", path.join(root, "CLIWorkspace"),
          "--report", reportPath, "--log", path.join(root, "cli.log"), "--quiet", bundleRoot,
        ], { timeout: 15_000 });
        const cliReport = JSON.parse(await readFile(reportPath, "utf8"));
        assert.equal(cliReport.status, "passed", JSON.stringify(cliReport.steps));
        assert.match(cliReport.steps[1].output, /report sent/);
      }
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}
