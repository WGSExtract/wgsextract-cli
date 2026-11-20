param(
    [string]$Workspace = (Join-Path $env:USERPROFILE "wgs-extract"),
    [switch]$RemoveConfig
)

$ErrorActionPreference = "Stop"
if (-not $RemoveConfig -or -not (Test-Path -LiteralPath $Workspace)) { exit 0 }

$current = [System.IO.Path]::GetFullPath($Workspace)
while ($current) {
    $item = Get-Item -LiteralPath $current -Force
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Refusing to remove users workspace config through a reparse point: $Workspace"
    }
    $parent = Split-Path -Parent $current
    if (-not $parent -or $parent -eq $current) { break }
    $current = $parent
}

$marker = Join-Path $Workspace ".users-workspace"
$sentinel = "WGS Extract users workspace"
[string]$markerText = ""
if ((Test-Path -LiteralPath $marker -PathType Leaf) -and
    -not ((Get-Item -LiteralPath $marker -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
    $markerText = [System.IO.File]::ReadAllText($marker)
}
if ($markerText.TrimEnd([char[]]"`r`n") -cne $sentinel) {
    Write-Output "Keeping config in an unmarked users workspace: $Workspace"
    exit 0
}

$config = Join-Path $Workspace "config.toml"
if (Test-Path -LiteralPath $config) {
    if (Test-Path -LiteralPath $config -PathType Container) {
        throw "Refusing to remove a directory named config.toml: $config"
    }
    Remove-Item -LiteralPath $config -Force
    Write-Output "Removed config: $config"
}

$remaining = @(Get-ChildItem -LiteralPath $Workspace -Force)
if ($remaining.Count -eq 1 -and $remaining[0].Name -eq ".users-workspace") {
    Remove-Item -LiteralPath $marker -Force
    try {
        [System.IO.Directory]::Delete($Workspace)
    } catch {
        # A concurrent writer may have populated it; restore ownership without
        # overwriting a marker that another process already recreated.
        if (Test-Path -LiteralPath $Workspace -PathType Container) {
            try {
                $stream = [System.IO.File]::Open($marker, [System.IO.FileMode]::CreateNew)
                try {
                    $bytes = [System.Text.Encoding]::UTF8.GetBytes("$sentinel`n")
                    $stream.Write($bytes, 0, $bytes.Length)
                } finally { $stream.Dispose() }
            } catch [System.IO.IOException] { }
        }
    }
}
