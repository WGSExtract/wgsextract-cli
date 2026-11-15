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
# The machine's own Apps entries and uninstallers are never read or run: TEST_REGISTERED_INSTALL stands
# for the folder a user chose in the installer, and each uninstaller run is logged to TEST_UNINSTALL_LOG.
function Get-ItemProperty {
    [CmdletBinding()] param([string]$LiteralPath)
    if ($env:TEST_REGISTERED_INSTALL -and $LiteralPath -eq "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\WGSExtract") {
        [pscustomobject]@{ InstallLocation = '"' + $env:TEST_REGISTERED_INSTALL + '"' }
    }
}
function Start-Process {
    [CmdletBinding()] param([string]$FilePath, [string[]]$ArgumentList, [switch]$Wait, [switch]$PassThru, [string]$WindowStyle)
    if ($env:TEST_UNINSTALL_LOG) { Add-Content -LiteralPath $env:TEST_UNINSTALL_LOG -Value $FilePath }
    [pscustomobject]@{ ExitCode = 0 }
}
$quick = New-QuickUninstallScript -InstallerName "WGSExtract_1.0.0_x64-setup.exe" -AppName "WGSExtract" -AppVersion "1.0.0" -AppIdentifier "test.quick.uninstall"
& ([scriptblock]::Create($quick.contents))
`;

/** Runs the quick uninstall with a home, app data and Apps entries all under root. */
function runQuickUninstall(root, env = {}) {
  return new Promise((resolve) => {
    execFile(pwsh, ["-NoProfile", "-NonInteractive", "-Command", runner], {
      env: { ...process.env, PACKAGE_SCRIPT: packageScript, HOME: root, LOCALAPPDATA: path.join(root, "LocalAppData"), XDG_DATA_HOME: path.join(root, "data"), APPDATA: path.join(root, "Roaming"), PUBLIC: path.join(root, "Public"), TEST_REGISTERED_INSTALL: "", TEST_UNINSTALL_LOG: path.join(root, "uninstallers.log"), ...env },
    }, (error, stdout, stderr) => resolve({ error, output: `${stdout}${stderr}` }));
  });
}

test("the quick uninstall runs the uninstaller of an app installed outside AppData", { skip: !pwsh && "needs PowerShell" }, async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "wgsextract-gui-quick-uninstall-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  // The folder a user chose in the installer's wizard, on another drive and with a space in it.
  const installed = path.join(root, "D drive", "My Apps", "WGSExtract");
  await mkdir(installed, { recursive: true });
  await writeFile(path.join(installed, "uninstall.exe"), "");
  await writeFile(path.join(installed, "notes.txt"), "the user's own file");

  const result = await runQuickUninstall(root, { TEST_REGISTERED_INSTALL: installed });

  assert.equal(result.error, null, result.output);
  const ran = (await readFile(path.join(root, "uninstallers.log"), "utf8")).trim().split(/\r?\n/);
  assert.deepEqual(ran, [path.join(installed, "uninstall.exe")]);
  // Only the app's own uninstaller removes a chosen folder, as it may hold other files.
  assert.equal(await readFile(path.join(installed, "notes.txt"), "utf8"), "the user's own file");
});

test("PR #531 review: the quick uninstall deletes the app's data but keeps a workspace in it", { skip: !pwsh && "needs PowerShell" }, async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "wgsextract-gui-quick-uninstall-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const localAppData = path.join(root, "LocalAppData");
  const appData = path.join(localAppData, "test.quick.uninstall");
  const workspace = path.join(appData, "BundleWorkspaces", "wgs-extract");
  const notWorkspace = path.join(appData, "BundleWorkspaces", "spoofed");
  await mkdir(path.join(workspace, "reference"), { recursive: true });
  await writeFile(path.join(workspace, ".bundle-workspace"), "WGS Extract GUI workspace\n");
  // One made by a release up to 0.8.7, with the older marker.
  const oldWorkspace = path.join(appData, "BundleWorkspaces", "older");
  await mkdir(oldWorkspace, { recursive: true });
  await writeFile(path.join(oldWorkspace, ".bundle-workspace"), "GUI for CLI bundle workspace\n");
  await writeFile(path.join(workspace, "reference", "hg38.fa"), "the user's library");
  await mkdir(notWorkspace, { recursive: true });
  await writeFile(path.join(notWorkspace, ".bundle-workspace"), "something else\n");
  await writeFile(path.join(appData, "settings.json"), "{}");
  await mkdir(path.join(appData, "EBWebView"), { recursive: true });
  await mkdir(path.join(localAppData, "WGSExtract"), { recursive: true });

  const result = await runQuickUninstall(root);

  assert.equal(result.error, null, result.output);
  assert.match(result.output, /Quick uninstall complete/);
  assert.equal(await readFile(path.join(workspace, "reference", "hg38.fa"), "utf8"), "the user's library");
  assert.ok(existsSync(path.join(oldWorkspace, ".bundle-workspace")));
  for (const removed of [notWorkspace, path.join(appData, "settings.json"), path.join(appData, "EBWebView"), path.join(localAppData, "WGSExtract")]) {
    assert.equal(existsSync(removed), false, removed);
  }
});
