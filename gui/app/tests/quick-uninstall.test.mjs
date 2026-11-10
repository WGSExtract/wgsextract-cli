import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const packageScript = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../tools/packaging/windows/package_tauri.ps1");
const pwsh = ["pwsh", "powershell.exe"].find((shell) => {
  try {
    execFileSync(shell, ["-NoProfile", "-Command", "exit 0"], { stdio: "ignore" });
    return true;
  }
  catch {
    return false;
  }
});

// Generates the release's quick-uninstall script with package_tauri.ps1's own function and runs it whole.
// Get-CimInstance is stubbed out everywhere so the script finds no processes to stop. On Windows the real
// one matches every command line containing "WGSExtract", which includes anything run from a
// wgsextract-cli checkout: this test's own runner, the dev app and the developer's shell (and agent) sessions.
const runner = `
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:PACKAGE_SCRIPT, [ref]$null, [ref]$null)
foreach ($name in "ConvertTo-PowerShellLiteral", "New-QuickUninstallScript") {
    . ([scriptblock]::Create($ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true).Extent.Text))
}
function Get-CimInstance { }
$quick = New-QuickUninstallScript -InstallerName "WGSExtract_1.0.0_x64-setup.exe" -AppName "WGSExtract" -AppVersion "1.0.0" -AppIdentifier "test.quick.uninstall"
& ([scriptblock]::Create($quick.contents))
`;

test("PR #531 review: the quick uninstall deletes the app's data but keeps a workspace in it", { skip: !pwsh && "needs PowerShell" }, async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "gui-for-cli-quick-uninstall-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const localAppData = path.join(root, "LocalAppData");
  const appData = path.join(localAppData, "test.quick.uninstall");
  const workspace = path.join(appData, "BundleWorkspaces", "wgs-extract");
  const notWorkspace = path.join(appData, "BundleWorkspaces", "spoofed");
  await mkdir(path.join(workspace, "reference"), { recursive: true });
  await writeFile(path.join(workspace, ".bundle-workspace"), "GUI for CLI bundle workspace\n");
  await writeFile(path.join(workspace, "reference", "hg38.fa"), "the user's library");
  await mkdir(notWorkspace, { recursive: true });
  await writeFile(path.join(notWorkspace, ".bundle-workspace"), "something else\n");
  await writeFile(path.join(appData, "settings.json"), "{}");
  await mkdir(path.join(appData, "EBWebView"), { recursive: true });
  await mkdir(path.join(localAppData, "WGSExtract"), { recursive: true });

  const result = await new Promise((resolve) => {
    execFile(pwsh, ["-NoProfile", "-NonInteractive", "-Command", runner], {
      env: { ...process.env, PACKAGE_SCRIPT: packageScript, HOME: root, LOCALAPPDATA: localAppData, XDG_DATA_HOME: path.join(root, "data"), APPDATA: path.join(root, "Roaming"), PUBLIC: path.join(root, "Public") },
    }, (error, stdout, stderr) => resolve({ error, output: `${stdout}${stderr}` }));
  });

  assert.equal(result.error, null, result.output);
  assert.match(result.output, /Quick uninstall complete/);
  assert.equal(await readFile(path.join(workspace, "reference", "hg38.fa"), "utf8"), "the user's library");
  for (const removed of [notWorkspace, path.join(appData, "settings.json"), path.join(appData, "EBWebView"), path.join(localAppData, "WGSExtract")]) {
    assert.equal(existsSync(removed), false, removed);
  }
});
