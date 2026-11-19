# Reports the Pixi and MSYS2 that setup finds. install_windows.bat reuses a Pixi
# on PATH or in ~\.pixi and installs one into ~\.pixi otherwise; $env:PIXI
# overrides the Pixi that commands run with, so an unusable one fails here, and
# so does a Pixi older than WGS Extract needs, which install_windows.bat rejects.
$ErrorActionPreference = "Stop"

# The bundled release's requires-pixi (scripts\wgsextract-pixi-minimum.txt).
function Get-PixiMinimum {
    $file = Join-Path (Split-Path -Parent $PSScriptRoot) "wgsextract-pixi-minimum.txt"
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $null }
    $line = "$(Get-Content -LiteralPath $file -TotalCount 1)".Trim()
    if ($line -match '^\d+\.\d+\.\d+$') { return [version]$line }
    return $null
}

function Get-PixiVersion {
    param([string]$Pixi)
    $output = & $Pixi --version 2>$null
    if ($LASTEXITCODE -eq 0 -and "$output" -match '^pixi (\d+\.\d+\.\d+)') { return [version]$Matches[1] }
    return $null
}

# Reports the user's Pixi, or fails when it is too old to install WGS Extract.
function Report-ReusedPixi {
    param([string]$Pixi, [string]$Where)
    $minimum = Get-PixiMinimum
    $version = Get-PixiVersion $Pixi
    if ($minimum -and (-not $version -or $version -lt $minimum)) {
        $found = if ($version) { "Pixi $version" } else { "a Pixi whose version could not be read" }
        [Console]::Error.WriteLine("WGS Extract needs Pixi >=$minimum, but found $found $Where`: $Pixi. Run 'pixi self-update' in a terminal, then run setup again.")
        exit 1
    }
    Write-Host "Setup will reuse the Pixi $version $Where`: $Pixi"
}

function Report-Pixi {
    if ($env:PIXI) {
        if (-not (Test-Path -LiteralPath $env:PIXI -PathType Leaf)) {
            [Console]::Error.WriteLine("PIXI is set but is not a file: $env:PIXI")
            exit 1
        }
        Write-Host "Commands will run with the Pixi set in PIXI: $env:PIXI"
        return
    }
    $command = Get-Command pixi -ErrorAction SilentlyContinue
    if ($command) {
        Report-ReusedPixi $command.Source "on PATH"
        return
    }
    # Pixi's home: PIXI_HOME (Pixi's own setting), then ~\.pixi.
    foreach ($pixiHome in @($env:PIXI_HOME, (Join-Path $HOME ".pixi"))) {
        if (-not $pixiHome) { continue }
        $homePixi = Join-Path $pixiHome "bin\pixi.exe"
        if (Test-Path -LiteralPath $homePixi -PathType Leaf) {
            Report-ReusedPixi $homePixi "in $pixiHome"
            return
        }
    }
    $installHome = if ($env:PIXI_HOME) { $env:PIXI_HOME } else { Join-Path $HOME ".pixi" }
    Write-Host "Pixi is not pre-installed; setup will install it into $installHome."
}

function Report-Msys2 {
    $candidates = @()
    . (Join-Path $PSScriptRoot "wgsextract-msys2.ps1")
    $roots = @(Get-WGSExtractMsys2Root)
    foreach ($root in $roots) {
        if ($root -and (Test-Path -LiteralPath (Join-Path $root "usr\bin\pacman.exe") -PathType Leaf)) {
            $candidates += $root
        }
    }
    $pacmanCmd = Get-Command pacman.exe -ErrorAction SilentlyContinue
    if ($pacmanCmd) {
        $root = (Resolve-Path (Join-Path (Split-Path -Parent $pacmanCmd.Source) "..\..")).Path.TrimEnd('\')
        if ($candidates -notcontains $root) { $candidates += $root }
    }
    if ($candidates.Count -gt 0) {
        Write-Host "MSYS2 is pre-installed: $($candidates[0])"
    } else {
        Write-Host "MSYS2 is not pre-installed; setup will install it if needed."
    }
}

Report-Pixi
Report-Msys2
exit 0
