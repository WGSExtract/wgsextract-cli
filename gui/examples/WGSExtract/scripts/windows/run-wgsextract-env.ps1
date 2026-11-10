$ErrorActionPreference = "Stop"

if ($args.Count -lt 1) {
    Write-Error "Usage: run-wgsextract-env.ps1 COMMAND [ARG...]"
    exit 64
}

$scriptDir = Split-Path -Parent $PSCommandPath
$scriptsRoot = Split-Path -Parent $scriptDir
$bundleRoot = Split-Path -Parent $scriptsRoot
$appDir = if ($env:WGSEXTRACT_APP_DIR) { $env:WGSEXTRACT_APP_DIR } else { Join-Path $bundleRoot "runtime\wgsextract-cli\app" }
# The CLI finds the MSYS2 tools setup installed through MSYS2_ROOT (its own
# default is C:\msys64, and the bundle's config.toml doesn't record one).
. (Join-Path $scriptDir "wgsextract-msys2.ps1")
$env:MSYS2_ROOT = Get-WGSExtractMsys2Root

function Get-PacmanBinDirectories {
    $directories = @()
    foreach ($configured in @($env:WGSEXTRACT_PACMAN_UCRT64_BIN, $env:UCRT64_BIN)) {
        if ($configured) {
            $directories += $configured
        }
    }
    if ($env:MSYS2_ROOT) {
        $directories += (Join-Path $env:MSYS2_ROOT "ucrt64\bin")
    }
    $directories += "C:\msys64\ucrt64\bin"
    if ($env:LOCALAPPDATA) {
        $directories += (Join-Path $env:LOCALAPPDATA "Programs\msys64\ucrt64\bin")
    }

    $result = @()
    foreach ($directory in $directories) {
        if (-not $directory) {
            continue
        }
        $result += $directory
        $parent = Split-Path -Parent $directory
        if ($parent -and (Split-Path -Leaf $parent).ToLowerInvariant() -eq "ucrt64") {
            $result += (Join-Path (Split-Path -Parent $parent) "usr\bin")
        }
    }
    return $result | Select-Object -Unique
}

function Resolve-NativeCommand {
    param([Parameter(Mandatory = $true)][string]$Name)

    if ([System.IO.Path]::IsPathRooted($Name)) {
        if (Test-Path -LiteralPath $Name -PathType Leaf) {
            return $Name
        }
        if (-not [System.IO.Path]::HasExtension($Name)) {
            $withExe = "$Name.exe"
            if (Test-Path -LiteralPath $withExe -PathType Leaf) {
                return $withExe
            }
        }
        return $null
    }

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command -and $command.CommandType -in @("Application", "ExternalScript")) {
        if ($command.Path) {
            return $command.Path
        }
        if ($command.Source -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) {
            return $command.Source
        }
    }

    $names = @($Name)
    if (-not [System.IO.Path]::HasExtension($Name)) {
        $names += "$Name.exe"
    }
    foreach ($directory in Get-PacmanBinDirectories) {
        foreach ($candidateName in $names) {
            $candidate = Join-Path $directory $candidateName
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return $candidate
            }
        }
    }
    return $null
}

function Invoke-ForwardedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [object[]]$InputObjects = @()
    )

    if ($env:WGSEXTRACT_FORWARD_STDIN -eq "1") {
        $InputObjects | & $FilePath @Arguments
    } else {
        & $FilePath @Arguments
    }
    exit $LASTEXITCODE
}

# Setup keeps the Pixi environments outside the workspace, in the folder it
# records as detached-environments in .pixi\config.toml (under %LOCALAPPDATA%).
# The workspace can say setup is done while that folder is empty: setup ran in
# another Windows account, or in an app sandbox (an MSIX app such as a store
# app) whose AppData\Local writes Windows keeps apart, or the folder was removed.
function Get-PixiEnvironmentsDirectory {
    param([Parameter(Mandatory = $true)][string]$AppDir)
    $config = Join-Path $AppDir ".pixi\config.toml"
    if (Test-Path -LiteralPath $config -PathType Leaf) {
        foreach ($line in Get-Content -LiteralPath $config) {
            if ($line -match "^\s*detached-environments\s*=\s*(['""])(.+)\1\s*$") {
                return $Matches[2]
            }
        }
    }
    return (Join-Path $AppDir ".pixi\envs")
}

function Test-PixiDefaultEnvironment {
    param([Parameter(Mandatory = $true)][string]$AppDir, [Parameter(Mandatory = $true)][string]$EnvsDir)
    if (Test-Path -LiteralPath (Join-Path $EnvsDir "default\conda-meta") -PathType Container) {
        return $true
    }
    # Detached: <envs>\<project>-<hash>\envs\default, recording the manifest it was installed for.
    $manifest = [System.IO.Path]::GetFullPath((Join-Path $AppDir "pixi.toml"))
    foreach ($record in Get-ChildItem -Path (Join-Path $EnvsDir "*\envs\default\conda-meta\pixi") -File -ErrorAction SilentlyContinue) {
        try {
            $installedFor = (Get-Content -LiteralPath $record.FullName -Raw | ConvertFrom-Json).manifest_path
        } catch {
            return $true
        }
        if ($installedFor -and [string]::Equals([System.IO.Path]::GetFullPath($installedFor), $manifest, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

if (-not (Test-Path -LiteralPath $appDir -PathType Container)) {
    [Console]::Error.WriteLine("WGS Extract bundle runtime is not installed at $appDir. Run setup before running commands.")
    exit 127
}

$commandName = [string]$args[0]
# Python is always the runtime's: a fresh Windows has none (its `python` is a
# Microsoft Store alias), and the bundle's scripts need the runtime's anyway.
if ($commandName -notin @("wgsextract", "python")) {
    $nativeCommand = Resolve-NativeCommand -Name $commandName
    if ($nativeCommand) {
        if ($env:WGSEXTRACT_FORWARD_STDIN -eq "1") {
            Invoke-ForwardedCommand -FilePath $nativeCommand -Arguments @($args | Select-Object -Skip 1) -InputObjects @($input)
        } else {
            Invoke-ForwardedCommand -FilePath $nativeCommand -Arguments @($args | Select-Object -Skip 1)
        }
    }
}

# Pixi resolution: an explicit $env:PIXI, then the Pixi on PATH or in Pixi's
# home (PIXI_HOME, else ~\.pixi), where install_windows.bat installs it
# (Windows setup does not embed Pixi).
$pixi = $env:PIXI
if ($pixi -and -not (Test-Path -LiteralPath $pixi -PathType Leaf)) {
    Write-Error "PIXI is set but is not executable: $pixi"
    exit 127
}
if (-not $pixi) {
    $command = Get-Command pixi -ErrorAction SilentlyContinue
    if ($command -and $command.CommandType -in @("Application", "ExternalScript")) {
        $pixi = if ($command.Path) { $command.Path } else { $command.Source }
    }
}
if (-not $pixi) {
    $pixiHome = if ($env:PIXI_HOME) { $env:PIXI_HOME } else { Join-Path $HOME ".pixi" }
    $homePixi = Join-Path $pixiHome "bin\pixi.exe"
    if (Test-Path -LiteralPath $homePixi -PathType Leaf) {
        $pixi = $homePixi
    }
}
if (-not $pixi) {
    Write-Error "Pixi was not found. Run the WGS Extract setup action again."
    exit 127
}

# Use the package cache setup-wgsextract-pixi.ps1 installed the environment with.
if (-not $env:PIXI_CACHE_DIR) {
    $pixiBaseDir = if ($env:WGSEXTRACT_PIXI_BASE_DIR) { $env:WGSEXTRACT_PIXI_BASE_DIR } else { Join-Path $env:LOCALAPPDATA "WGSExtractPixi" }
    $env:PIXI_CACHE_DIR = Join-Path $pixiBaseDir "cache"
}

# Data sources must answer within seconds, so they never let `pixi run` finish
# an interrupted install: they need the shim setup writes last and run the
# environment as it is.
$runArgs = @("run")
if ($env:GUI_FOR_CLI_DATA_SOURCE -eq "1") {
    $shim = Join-Path (Split-Path -Parent $appDir) "bin\wgsextract.cmd"
    if (-not (Test-Path -LiteralPath $shim -PathType Leaf)) {
        [Console]::Error.WriteLine("WGS Extract CLI setup has not finished ($shim is missing). Run the WGS Extract setup action again.")
        exit 127
    }
    $envsDir = Get-PixiEnvironmentsDirectory -AppDir $appDir
    if (-not (Test-PixiDefaultEnvironment -AppDir $appDir -EnvsDir $envsDir)) {
        [Console]::Error.WriteLine("WGS Extract's tools are missing from $envsDir. Run Setup again on the Settings page to install them.")
        exit 127
    }
    $runArgs += "--no-install"
}

Push-Location $appDir
try {
    if ($env:WGSEXTRACT_FORWARD_STDIN -eq "1") {
        Invoke-ForwardedCommand -FilePath $pixi -Arguments ($runArgs + $args) -InputObjects @($input)
    } else {
        Invoke-ForwardedCommand -FilePath $pixi -Arguments ($runArgs + $args)
    }
} finally {
    Pop-Location
}
