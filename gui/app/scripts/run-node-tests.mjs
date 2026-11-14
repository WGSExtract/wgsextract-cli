import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const defaultTests = [
  "tests/dev-runner.test.mjs",
  "tests/rendering.test.mjs",
  "tests/rendering-regressions.test.mjs",
  "tests/rendering-layout.test.mjs",
  "tests/bundle-loader.test.mjs",
  "tests/manifest-validator.test.mjs",
  "tests/bundle-archive.test.mjs",
  "tests/appimage-environment.test.mjs",
  "tests/paths.test.mjs",
  "tests/config-store.test.mjs",
  "tests/bundle-test-runner.test.mjs",
  "tests/bundle-uninstall-cli.test.mjs",
  "tests/quick-uninstall.test.mjs",
  "tests/wgsextract-missing-pixi-environment.test.mjs",
  "tests/example-bundles.test.mjs",
  "tests/workspace.test.mjs",
  "tests/workspace-location.test.mjs",
  "tests/platform.test.mjs",
  "tests/platform-command.test.mjs",
  "tests/process-runner.test.mjs",
  "tests/job-server.test.mjs",
  "tests/path-picker.test.mjs",
  "tests/operations.test.mjs",
  "tests/routes.test.mjs",
  "tests/setup-runner.test.mjs",
  "tests/tauri-updater.test.mjs",
  "tests/developer-cache.test.mjs",
  "tests/terminal.test.mjs",
  "tests/result-document.test.mjs",
  "tests/conformance.test.mjs",
  "tests/action-confirmation.test.mjs",
  "tests/prerequisites.test.mjs",
  "tests/ui-mode.test.mjs",
];

const tests = process.argv.slice(2);
// The app's data folder (from LOCALAPPDATA or XDG_DATA_HOME) holds the CLI runtime that uninstall tests remove,
// stopping whatever uses it. Tests get a throwaway one, so a test that forgets to isolate it can't touch the
// developer's own installed app.
const isolatedRoot = mkdtempSync(path.join(tmpdir(), "wgsextract-gui-tests-"));
const child = spawn(process.execPath, ["--test", ...(tests.length > 0 ? tests : defaultTests)], {
  stdio: ["ignore", "pipe", "pipe"],
  windowsHide: true,
  env: {
    ...process.env,
    LOCALAPPDATA: path.join(isolatedRoot, "LocalAppData"),
    XDG_DATA_HOME: path.join(isolatedRoot, "data"),
  },
});

const stdout = [];
const stderr = [];
child.stdout.on("data", (chunk) => stdout.push(chunk));
child.stderr.on("data", (chunk) => stderr.push(chunk));

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    child.kill(signal);
  });
}

child.on("close", (code, signal) => {
  try {
    rmSync(isolatedRoot, { recursive: true, force: true, maxRetries: 3 });
  } catch (error) {
    // A file Windows still holds open must not hide the results or change the outcome.
    console.warn(`Could not remove the tests' app data folder ${isolatedRoot}: ${error.message}`);
  }
  const stdoutText = Buffer.concat(stdout).toString("utf8");
  const stderrText = Buffer.concat(stderr).toString("utf8");
  if (signal) {
    writeBufferedOutput(stdoutText, stderrText);
    process.kill(process.pid, signal);
    return;
  }
  if (code === 0) {
    console.log(successSummary(stdoutText));
  } else {
    writeBufferedOutput(stdoutText, stderrText);
  }
  process.exit(code ?? 1);
});

child.on("error", (error) => {
  console.error(error);
  process.exit(1);
});

function successSummary(output) {
  const total = output.match(/tests\s+(\d+)/)?.[1];
  const passed = output.match(/pass\s+(\d+)/)?.[1];
  const skipped = output.match(/skipped\s+(\d+)/)?.[1];
  const duration = output.match(/duration_ms\s+([\d.]+)/)?.[1];
  const parts = [];
  if (passed) {
    parts.push(`${passed} passed`);
  }
  if (skipped && skipped !== "0") {
    parts.push(`${skipped} skipped`);
  }
  if (total) {
    parts.push(`${total} total`);
  }
  if (duration) {
    parts.push(`${Math.round(Number(duration))}ms`);
  }
  return parts.length > 0 ? `Node tests passed (${parts.join(", ")}).` : "Node tests passed.";
}

function writeBufferedOutput(stdoutText, stderrText) {
  if (stdoutText.length > 0) {
    process.stdout.write(stdoutText);
  }
  if (stderrText.length > 0) {
    process.stderr.write(stderrText);
  }
}
