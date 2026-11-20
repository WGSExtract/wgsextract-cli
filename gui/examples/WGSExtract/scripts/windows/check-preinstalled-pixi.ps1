# Reports the Pixi and MSYS2 that setup finds. install_windows.bat uses the Pixi
# in PIXI_HOME\bin or ~\.pixi\bin, then one on PATH, and installs one into
# Pixi's home otherwise; commands then run with $env:PIXI, else the Pixi on PATH,
# else Pixi's home (run-wgsextract-env.ps1). Each Pixi either will use must exist
# and be new enough for WGS Extract, which install_windows.bat checks too.
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

# Fails unless the Pixi at $Pixi is new enough for WGS Extract; returns its version.
function Assert-PixiNewEnough {
    param([string]$Pixi, [string]$Role)
    $minimum = Get-PixiMinimum
    $version = Get-PixiVersion $Pixi
    if ($minimum -and (-not $version -or $version -lt $minimum)) {
        $found = if ($version) { "is version $version" } else { "did not report its version" }
        [Console]::Error.WriteLine("WGS Extract needs Pixi >=$minimum, but $Role ($Pixi) $found. Run 'pixi self-update' in a terminal, then run setup again.")
        exit 1
    }
    return $version
}

function Get-PathPixi {
    $command = Get-Command pixi -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

# Pixi's home: PIXI_HOME (Pixi's own setting), then ~\.pixi.
function Get-HomePixi {
    foreach ($pixiHome in @($env:PIXI_HOME, (Join-Path $HOME ".pixi"))) {
        if (-not $pixiHome) { continue }
        $homePixi = Join-Path $pixiHome "bin\pixi.exe"
        if (Test-Path -LiteralPath $homePixi -PathType Leaf) { return $homePixi }
    }
    return $null
}

function Report-Pixi {
    if ($env:PIXI -and -not (Test-Path -LiteralPath $env:PIXI -PathType Leaf)) {
        [Console]::Error.WriteLine("PIXI is set but is not a file: $env:PIXI")
        exit 1
    }
    $homePixi = Get-HomePixi
    $pathPixi = Get-PathPixi
    $setupPixi = if ($homePixi) { $homePixi } else { $pathPixi }
    if ($setupPixi) {
        $setupRole = if ($homePixi) { "the Pixi in Pixi's home folder" } else { "the Pixi on PATH" }
        $version = Assert-PixiNewEnough $setupPixi $setupRole
        Write-Host "Setup will reuse the Pixi $version`: $setupPixi"
    } else {
        $installHome = if ($env:PIXI_HOME) { $env:PIXI_HOME } else { Join-Path $HOME ".pixi" }
        Write-Host "Pixi is not pre-installed; setup will install it into $installHome."
    }
    $commandPixi = if ($env:PIXI) { $env:PIXI } else { $pathPixi }
    if ($commandPixi -and $commandPixi -ne $setupPixi) {
        $role = if ($env:PIXI) { "the Pixi set in PIXI" } else { "the Pixi on PATH" }
        $version = Assert-PixiNewEnough $commandPixi $role
        Write-Host "Commands will run with the Pixi $version`: $commandPixi"
    }
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
