# Serves the Web UI in dev mode (TypeScript watch, server restart, browser
# reload) with the WGSExtract bundle running the CLI from this checkout, so a
# browser refresh picks up both GUI and CLI edits.
#   powershell -ExecutionPolicy Bypass -File gui\scripts\dev-checkout-cli.ps1 [-Port 8787]
param([int]$Port = 8787)
$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not (Test-Path -LiteralPath (Join-Path $repo ".pixi\envs\default\conda-meta\pixi") -PathType Leaf)) {
    throw "This checkout's Pixi environment is not installed. Run 'pixi install' in $repo first."
}

# The bundle's Windows runner wants a setup-shaped runtime: <runtime>\app (the
# checkout, through a junction) and the <runtime>\bin\wgsextract.cmd shim setup
# writes. Keep it out of the repository and the installed app's data folder.
$runtime = Join-Path $env:LOCALAPPDATA ("wgsextract-gui-dev\cli-dev\" + (Split-Path -Leaf $repo))
$app = Join-Path $runtime "app"
$bin = Join-Path $runtime "bin"
New-Item -ItemType Directory -Force $bin | Out-Null
$existing = Get-Item -LiteralPath $app -ErrorAction SilentlyContinue
if ($existing -and [string]$existing.Target -ne $repo) {
    $existing.Delete()
    $existing = $null
}
if (-not $existing) {
    New-Item -ItemType Junction -Path $app -Target $repo | Out-Null
}
Set-Content -Encoding ascii -LiteralPath (Join-Path $bin "wgsextract.cmd") -Value @(
    "@echo off",
    "pixi run --manifest-path `"%~dp0..\app\pixi.toml`" wgsextract %*"
)

$env:WGSEXTRACT_APP_DIR = $app
Write-Host "CLI: $repo (via $app)"
Set-Location (Join-Path $repo "gui\app")
& npm run dev -- --bundle ..\examples\WGSExtract --port $Port
exit $LASTEXITCODE
